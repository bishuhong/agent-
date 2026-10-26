"""Assets application services. Accepted tasks are durable control-plane facts."""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from etl_agent.api.schemas import contracts as c
from etl_agent.config import Settings
from etl_agent.infrastructure.persistence import models as m
from etl_agent.infrastructure.persistence.access import (
    compare_revision,
    project_entity,
    public_record,
)
from etl_agent.infrastructure.persistence.database import Database
from etl_agent.modules.assets.ports import FileParser, ProbeRegistry, SecretProvider, StoredSecret
from etl_agent.modules.audit.service import EvidenceLedger
from etl_agent.modules.identity_access.domain import assert_action
from etl_agent.shared.canonical import digest, sha256
from etl_agent.shared.context import ActorContext, utcnow
from etl_agent.shared.errors import ApplicationError, invalid


def _value(value: Any) -> str:
    return str(getattr(value, "value", value))


class AssetsService:
    def __init__(
        self,
        settings: Settings,
        ledger: EvidenceLedger,
        *,
        secrets: SecretProvider | None = None,
        probes: ProbeRegistry | None = None,
        files: FileParser | None = None,
    ) -> None:
        from etl_agent.adapters.files.parsers import StructuredFileParser
        from etl_agent.adapters.probes.postgresql import PostgreSQLProbe
        from etl_agent.adapters.secrets.vault import VaultSecretProvider

        self.settings, self.ledger = settings, ledger
        self.secrets = secrets or VaultSecretProvider(settings)
        self.probes = probes or ProbeRegistry(
            {
                "postgresql": PostgreSQLProbe(
                    settings.endpoint_allowlist, require_tls=settings.environment == "production"
                )
            }
            if "postgresql" in settings.probe_connectors
            else {}
        )
        self.files = files or StructuredFileParser()

    def _endpoint(self, endpoint: dict[str, Any], environment: str) -> None:
        if environment not in self.settings.permitted_environments:
            raise invalid("目标环境未受管授权", "environment")
        host = (
            endpoint.get("host")
            if endpoint["kind"] == "database"
            else urlsplit(endpoint["base_url"]).hostname
        )
        if not host or host.casefold().rstrip(".") not in {
            item.casefold().rstrip(".") for item in self.settings.endpoint_allowlist
        }:
            raise ApplicationError("FORBIDDEN", "目标主机不在受管允许清单中", 403)

    @staticmethod
    def _external(session: Session, call: Callable[[], Any]) -> Any:
        # API entrypoints provide one fresh UoW. Release only read transactions;
        # never implicitly commit or discard another use case's pending mutations.
        if session.new or session.dirty or session.deleted:
            raise RuntimeError("External secret calls require a clean request unit of work")
        if session.in_transaction():
            session.rollback()
        return call()

    def _credential(
        self,
        session: Session,
        actor: ActorContext,
        project_id: UUID,
        credentials: c.CredentialInput | None,
        *,
        alias: str | None = None,
    ) -> m.SecretReference | None:
        if credentials is None:
            return None
        if isinstance(credentials, c.ManagedCredentialInput):
            row = project_entity(
                session, m.SecretReference, credentials.secret_ref_id, actor, "view"
            )
            if row.project_id != project_id:
                raise ApplicationError("RESOURCE_NOT_FOUND", "Secret引用不存在或不可访问", 404)
            return row
        alias = alias or "secret_" + uuid4().hex
        stored = self._external(
            session, lambda: self.secrets.store(alias, credentials.reveal_for_secret_provider())
        )
        row = m.SecretReference(
            id=uuid4(),
            project_id=project_id,
            provider=stored.provider,
            public_alias=alias,
            provider_locator=stored.locator,
            secret_version=stored.version,
        )
        session.add(row)
        session.flush()
        return row

    def create_connection(
        self, session: Session, actor: ActorContext, payload: c.ConnectionCreateRequest
    ) -> dict[str, Any]:
        assert_action(actor, payload.project_id, "create_connection")
        endpoint = payload.endpoint.model_dump(mode="json", by_alias=True)
        self._endpoint(endpoint, payload.environment)
        if endpoint["kind"] == "database" and payload.credentials is None:
            raise invalid("数据库连接需要受管凭据", "credentials")
        secret = self._credential(session, actor, payload.project_id, payload.credentials)
        row = m.Connection(
            id=uuid4(),
            project_id=payload.project_id,
            name=payload.name,
            connector_type=_value(payload.connector_type),
            endpoint=endpoint,
            secret_ref_id=secret.id if secret else None,
            environment=payload.environment,
        )
        session.add(row)
        session.flush()
        self.ledger.append(
            session, row.project_id, actor, "connection.created.v1", "connection", row.id
        )
        return public_record(row, c.ConnectionResponse)

    def replace_connection(
        self,
        session: Session,
        actor: ActorContext,
        connection_id: UUID,
        payload: c.ConnectionReplaceRequest,
        expected_revision: int,
    ) -> dict[str, Any]:
        row = project_entity(session, m.Connection, connection_id, actor, "edit_connection")
        compare_revision(row.revision, expected_revision)
        if row.connector_type != _value(payload.connector_type):
            raise invalid("已创建连接不能更换连接器类型", "connector_type")
        project_id = row.project_id
        endpoint = payload.endpoint.model_dump(mode="json", by_alias=True)
        self._endpoint(endpoint, payload.environment)
        supplied = "credentials" in payload.model_fields_set
        secret = (
            self._credential(session, actor, project_id, payload.credentials) if supplied else None
        )
        row = project_entity(
            session, m.Connection, connection_id, actor, "edit_connection", lock=True
        )
        compare_revision(row.revision, expected_revision)
        row.name, row.endpoint, row.environment = payload.name, endpoint, payload.environment
        if supplied:
            if secret is None:
                raise invalid("显式凭据不能为null", "credentials")
            row.secret_ref_id = secret.id
        row.revision += 1
        row.updated_at = utcnow()
        session.flush()
        self.ledger.append(
            session, row.project_id, actor, "connection.replaced.v1", "connection", row.id
        )
        return public_record(row, c.ConnectionResponse)

    def rotate_secret(
        self, session: Session, actor: ActorContext, payload: c.RotateCredentialRequest
    ) -> dict[str, Any]:
        old = project_entity(
            session, m.SecretReference, payload.secret_ref_id, actor, "rotate_secret"
        )
        compare_revision(old.revision, payload.expected_revision)
        project_id, alias, old_id = old.project_id, old.public_alias, old.id
        new = self._credential(session, actor, project_id, payload.credentials, alias=alias)
        if new is None:
            raise invalid("轮转需要明确的新凭据", "credentials")
        if new.id == old_id:
            raise ApplicationError("STATE_CONFLICT", "轮转必须指向新的凭据版本", 409)
        old = project_entity(session, m.SecretReference, old_id, actor, "rotate_secret", lock=True)
        compare_revision(old.revision, payload.expected_revision)
        old.revision += 1
        old.updated_at = utcnow()
        rows = session.scalars(
            select(m.Connection)
            .where(m.Connection.project_id == project_id, m.Connection.secret_ref_id == old_id)
            .with_for_update()
        ).all()
        for row in rows:
            row.secret_ref_id = new.id
            row.revision += 1
            row.updated_at = utcnow()
        session.flush()
        self.ledger.append(
            session,
            project_id,
            actor,
            "secret.rotated.v1",
            "secret_reference",
            new.id,
            {"previous_id": old_id, "version": new.secret_version},
        )
        return public_record(new, c.SecretReferenceResponse)

    def start_test(
        self, session: Session, actor: ActorContext, connection_id: UUID
    ) -> dict[str, Any]:
        connection = project_entity(session, m.Connection, connection_id, actor, "test_connection")
        self.probes.require(connection.connector_type)
        row = m.ConnectionTestResult(
            id=uuid4(),
            project_id=connection.project_id,
            connection_id=connection.id,
            connection_revision=connection.revision,
            status="running",
        )
        session.add(row)
        session.flush()
        self.ledger.append(
            session, row.project_id, actor, "connection.test.accepted.v1", "connection_test", row.id
        )
        return public_record(row, c.ConnectionTestResponse)

    def start_profile(
        self,
        session: Session,
        actor: ActorContext,
        connection_id: UUID,
        payload: c.ProfileCreateRequest,
    ) -> dict[str, Any]:
        connection = project_entity(
            session, m.Connection, connection_id, actor, "profile_connection"
        )
        self.probes.require(connection.connector_type)
        row = m.ProfileTask(
            id=uuid4(),
            project_id=connection.project_id,
            connection_id=connection.id,
            requested_by=actor.user_id,
            resource_names=list(dict.fromkeys(payload.resource_names)),
            sample_limit=payload.sample_limit,
        )
        session.add(row)
        session.flush()
        self.ledger.append(
            session, row.project_id, actor, "profile.accepted.v1", "profile_task", row.id
        )
        return c.ProfileTaskAccepted(
            resource_id=row.id, status=c.TaskStatus(row.status)
        ).model_dump(mode="json")

    def upload(
        self,
        session: Session,
        actor: ActorContext,
        payload: c.FileAssetUploadRequest,
        filename: str,
    ) -> dict[str, Any]:
        assert_action(actor, payload.project_id, "upload_file")
        if len(payload.file) > self.settings.max_upload_bytes:
            raise invalid("文件超过当前受管上传上限", "file")
        name = filename.replace("\\", "/").rsplit("/", 1)[-1]
        if not name or "\x00" in name:
            raise invalid("文件名无效", "file")
        file_format = Path(name).suffix.lower().removeprefix(".")
        if file_format not in {"csv", "xlsx", "json", "parquet"}:
            raise invalid("仅支持CSV、XLSX、JSON、Parquet", "file")
        parsed = None
        try:
            parsed = self._external(session, lambda: self.files.parse(payload.file, file_format))
            c.SchemaField.model_validate(parsed.fields[0]) if parsed.fields else None
            if not parsed.fields:
                raise invalid("源文件没有字段", "file")
            for item in parsed.fields:
                c.SchemaField.model_validate(item)
        except (ApplicationError, ValidationError):
            # Persist an honestly failed asset, not an empty successful Schema.
            parsed = None
        root = self.settings.storage_root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        asset_id = uuid4()
        target = root / f"{asset_id}.{file_format}"
        if not target.resolve().is_relative_to(root):
            raise invalid("受管存储键无效", "file")
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload.file)
            row = m.FileAsset(
                id=asset_id,
                project_id=payload.project_id,
                file_name=name,
                file_path=str(target),
                file_size=len(payload.file),
                file_format=file_format,
                parse_status="succeeded" if parsed else "failed",
                schema_json=parsed.fields if parsed else None,
                content_digest=sha256(payload.file),
            )
            session.add(row)
            session.flush()
            if parsed:
                fields = parsed.fields
                profile = m.MetadataProfile(
                    id=uuid4(),
                    project_id=payload.project_id,
                    file_asset_id=row.id,
                    resource_name=name,
                    fields=fields,
                    statistics=parsed.statistics,
                    masked_samples=[],
                    profile_digest=digest(
                        {
                            "schema_version": 1,
                            "asset_digest": row.content_digest,
                            "fields": fields,
                            "statistics": parsed.statistics,
                        }
                    ),
                    expires_at=utcnow() + timedelta(minutes=15),
                )
                session.add(profile)
            self.ledger.append(
                session,
                row.project_id,
                actor,
                "file.uploaded.v1",
                "file_asset",
                row.id,
                {"content_digest": row.content_digest, "parse_status": row.parse_status},
            )
            session.flush()
            return public_record(row, c.FileAssetResponse)
        except Exception:
            target.unlink(missing_ok=True)
            raise

    def get_connection(
        self, session: Session, actor: ActorContext, resource_id: UUID
    ) -> dict[str, Any]:
        return public_record(
            project_entity(session, m.Connection, resource_id, actor), c.ConnectionResponse
        )

    def get_test(self, session: Session, actor: ActorContext, resource_id: UUID) -> dict[str, Any]:
        return public_record(
            project_entity(session, m.ConnectionTestResult, resource_id, actor),
            c.ConnectionTestResponse,
        )

    def get_task(self, session: Session, actor: ActorContext, resource_id: UUID) -> dict[str, Any]:
        row = project_entity(session, m.ProfileTask, resource_id, actor)
        ids = session.scalars(
            select(m.ProfileTaskResult.profile_id).where(m.ProfileTaskResult.task_id == row.id)
        ).all()
        return public_record(row, c.ProfileTaskResponse, profile_ids=list(ids))

    def get_profile(
        self, session: Session, actor: ActorContext, resource_id: UUID
    ) -> dict[str, Any]:
        return public_record(
            project_entity(session, m.MetadataProfile, resource_id, actor),
            c.MetadataProfileResponse,
        )

    def get_file(self, session: Session, actor: ActorContext, resource_id: UUID) -> dict[str, Any]:
        return public_record(
            project_entity(session, m.FileAsset, resource_id, actor), c.FileAssetResponse
        )

    def _probe_snapshot(self, session: Session, connection: m.Connection) -> dict[str, Any]:
        secret = (
            session.get(m.SecretReference, connection.secret_ref_id)
            if connection.secret_ref_id
            else None
        )
        return {
            "id": connection.id,
            "project_id": connection.project_id,
            "revision": connection.revision,
            "connector": connection.connector_type,
            "endpoint": connection.endpoint,
            "secret": StoredSecret(secret.provider, secret.provider_locator, secret.secret_version)
            if secret
            else None,
        }

    def _probe_credentials(self, snapshot: dict[str, Any]) -> dict[str, str]:
        return self.secrets.resolve(snapshot["secret"]) if snapshot["secret"] else {}

    def process_test(self, database: Database, test_id: UUID) -> None:
        with database.session_factory.begin() as session:
            row = session.scalar(
                select(m.ConnectionTestResult)
                .where(m.ConnectionTestResult.id == test_id)
                .with_for_update()
            )
            if row is None or row.status != "running":
                return
            connection = session.get(m.Connection, row.connection_id)
            if connection is None:
                raise ApplicationError("STATE_CONFLICT", "任务连接已不存在", 409)
            snapshot = self._probe_snapshot(session, connection)
            expected_revision = row.connection_revision
        error = None
        try:
            if snapshot["revision"] != expected_revision:
                raise ApplicationError("FINGERPRINT_CHANGED", "连接修订已变化", 409)
            self.probes.require(snapshot["connector"]).test(
                snapshot["endpoint"], self._probe_credentials(snapshot)
            )
        except Exception as failure:
            error = self._safe_failure(failure)
        with database.session_factory.begin() as session:
            row = session.scalar(
                select(m.ConnectionTestResult)
                .where(m.ConnectionTestResult.id == test_id)
                .with_for_update()
            )
            if row is None or row.status != "running":
                return
            connection = session.get(m.Connection, row.connection_id)
            if connection is None or connection.revision != expected_revision:
                error = ApplicationError(
                    "FINGERPRINT_CHANGED", "连接修订已变化", 409
                ).safe_payload()
            row.status, row.error = ("failed" if error else "succeeded"), error
            row.finished_at = utcnow()
            self.ledger.append(
                session,
                row.project_id,
                None,
                "connection.test.finished.v1",
                "connection_test",
                row.id,
                {"status": row.status},
            )

    def process_profile(self, database: Database, task_id: UUID) -> None:
        with database.session_factory.begin() as session:
            row = session.scalar(
                select(m.ProfileTask).where(m.ProfileTask.id == task_id).with_for_update()
            )
            if row is None or row.status in {"succeeded", "failed"}:
                return
            # A redelivered read-only probe can resume after a Worker crash. The
            # final row lock allows only the first result to become authoritative.
            if row.status == "queued":
                row.status, row.started_at = "running", utcnow()
                row.revision += 1
            connection = session.get(m.Connection, row.connection_id)
            if connection is None:
                raise ApplicationError("STATE_CONFLICT", "任务连接已不存在", 409)
            snapshot = self._probe_snapshot(session, connection)
            if not isinstance(row.resource_names, list) or any(
                not isinstance(item, str) for item in row.resource_names
            ):
                raise invalid("持久探查资源清单无效")
            resources, sample_limit = list(row.resource_names), row.sample_limit
        error, profiles = None, []
        try:
            profiles = self.probes.require(snapshot["connector"]).profile(
                snapshot["endpoint"], self._probe_credentials(snapshot), resources, sample_limit
            )
            if {item.resource_name for item in profiles} != set(resources) or len(profiles) != len(
                resources
            ):
                raise invalid("探查结果未完整覆盖请求资源")
            for profile in profiles:
                if len(profile.masked_samples) > sample_limit:
                    raise invalid("探查返回超过批准的脱敏样本上限")
                for field in profile.fields:
                    c.SchemaField.model_validate(field)
                for sample in profile.masked_samples:
                    c.MaskedSample.model_validate(sample)
                    if set(sample["values"]) - set(sample["masked_fields"]):
                        raise invalid("未分类字段样本必须全部脱敏")
                    sample["values"] = {name: "[REDACTED]" for name in sample["values"]}
        except Exception as failure:
            error = self._safe_failure(failure)
        with database.session_factory.begin() as session:
            row = session.scalar(
                select(m.ProfileTask).where(m.ProfileTask.id == task_id).with_for_update()
            )
            if row is None or row.status != "running":
                return
            connection = session.get(m.Connection, row.connection_id)
            if connection is None or connection.revision != snapshot["revision"]:
                error = ApplicationError(
                    "FINGERPRINT_CHANGED", "探查期间连接修订变化", 409
                ).safe_payload()
            if not error:
                for item in profiles:
                    created_profile = m.MetadataProfile(
                        id=uuid4(),
                        project_id=row.project_id,
                        connection_id=row.connection_id,
                        resource_name=item.resource_name,
                        fields=item.fields,
                        statistics=item.statistics,
                        masked_samples=item.masked_samples,
                        profile_digest=digest(
                            {
                                "schema_version": 1,
                                "connection_id": row.connection_id,
                                "connection_revision": snapshot["revision"],
                                "resource_name": item.resource_name,
                                "fields": item.fields,
                                "statistics": item.statistics,
                                "masked_samples": item.masked_samples,
                            }
                        ),
                        expires_at=utcnow() + timedelta(minutes=15),
                    )
                    session.add(created_profile)
                    session.flush()
                    session.add(
                        m.ProfileTaskResult(
                            project_id=row.project_id, task_id=row.id, profile_id=created_profile.id
                        )
                    )
                session.flush()
            row.status, row.error, row.finished_at = (
                ("failed" if error else "succeeded"),
                error,
                utcnow(),
            )
            row.revision += 1
            row.updated_at = utcnow()
            self.ledger.append(
                session,
                row.project_id,
                None,
                "profile.finished.v1",
                "profile_task",
                row.id,
                {"status": row.status},
            )

    @staticmethod
    def _safe_failure(error: Exception) -> dict[str, Any]:
        if isinstance(error, ApplicationError):
            return error.safe_payload()
        return ApplicationError(
            "DEPENDENCY_UNAVAILABLE", "受管探查失败，原始错误已隔离", 503, retryable=True
        ).safe_payload()
