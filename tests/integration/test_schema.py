"""Real PostgreSQL schema/constraint tests, isolated in a per-run schema.

Without TEST_DATABASE_URL these are explicitly skipped, never replaced by SQLite.
The supplied database must be a disposable test database whose role can create a
temporary schema; this module does not migrate the default application schema.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from etl_agent.infrastructure.persistence import models as m
from etl_agent.infrastructure.persistence.database import Database
from etl_agent.shared.errors import ApplicationError


@pytest.fixture(scope="module")
def postgres():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is absent: real PostgreSQL integration is not configured")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    if not url.startswith("postgresql+psycopg://"):
        pytest.fail(
            "TEST_DATABASE_URL must identify PostgreSQL psycopg; SQLite is not a substitute"
        )
    schema = "etl_agent_test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        url, connect_args={"options": f"-c search_path={schema} -c timezone=UTC"}
    )
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "migrations")
    )
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    try:
        yield engine, sessionmaker(engine, expire_on_commit=False)
    finally:
        engine.dispose()
        # schema is generated above and never comes from user input or a path.
        assert (
            schema.startswith("etl_agent_test_")
            and schema.removeprefix("etl_agent_test_").isalnum()
        )
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def make_user(session: Session, *, person_ref=None):
    user_id = uuid4()
    user = m.User(
        id=user_id,
        subject_ref=f"local:{user_id}",
        username="test_" + user_id.hex,
        person_ref=person_ref or uuid4(),
        display_name="Test person",
        status="enabled",
    )
    session.add(user)
    session.flush()
    return user


def make_project(session: Session):
    project = m.Project(id=uuid4(), name="Test project", code=uuid4().hex)
    session.add(project)
    session.flush()
    return project


def make_preparation(session: Session):
    user = make_user(session)
    project = make_project(session)
    pipeline = m.Pipeline(id=uuid4(), project_id=project.id, code=uuid4().hex, name="Test pipeline")
    session.add(pipeline)
    session.flush()
    version = m.PipelineVersion(
        id=uuid4(), project_id=project.id, pipeline_id=pipeline.id, version_number=1
    )
    session.add(version)
    session.flush()
    digest = "a" * 64
    report = m.ValidationReport(
        id=uuid4(),
        project_id=project.id,
        version_id=version.id,
        content_digest=digest,
        rule_version="test-v1",
        passed=True,
    )
    session.add(report)
    session.flush()
    for artifact_type in ("etl_plan", "hocon", "dag", "quality_contract", "supervision_contract"):
        session.add(
            m.PipelineArtifact(
                project_id=project.id,
                version_id=version.id,
                artifact_type=artifact_type,
                artifact_digest=digest,
                content={},
            )
        )
    version.etl_plan_json = {}
    version.hocon_text = "test-hocon"
    version.artifact_digest = digest
    version.validation_report_id = report.id
    version.is_immutable = True
    session.flush()
    prep = m.Preparation(
        id=uuid4(),
        project_id=project.id,
        version_id=version.id,
        maker_id=user.id,
        input_fingerprint=digest,
        input_bindings=[],
        resource_scopes=[],
        impact_summary="test",
        data_classification="test",
        environment="isolated",
        risk_level="P2",
        required_slots=["data", "security"],
        quality_contract={},
        supervision_contract={},
        rollback_plan={},
        policy_version="test-v1",
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    session.add(prep)
    session.flush()
    return user, project, version, prep


def test_explicit_schema_contains_all_records(postgres):
    engine, _ = postgres
    inspector = inspect(engine)
    assert set(m.Base.metadata.tables).issubset(inspector.get_table_names())
    assert len(m.Base.metadata.tables) >= 48
    assert "tenant_id" not in {
        column.name for table in m.Base.metadata.tables.values() for column in table.columns
    }
    fk = inspector.get_foreign_keys("connections")
    assert any(item["constrained_columns"] == ["project_id", "secret_ref_id"] for item in fk)
    indexes = inspector.get_indexes("outbox_messages")
    assert any(index["dialect_options"].get("postgresql_where") for index in indexes)


def test_cross_project_reference_is_rejected(postgres):
    _, factory = postgres
    with factory.begin() as session:
        one, two = make_project(session), make_project(session)
        secret = m.SecretReference(
            id=uuid4(),
            project_id=one.id,
            provider="test",
            public_alias="test",
            provider_locator="protected/test",
            secret_version="1",
        )
        session.add(secret)
        session.flush()
        secret_id, second_id = secret.id, two.id
    with pytest.raises(IntegrityError):
        with factory.begin() as session:
            session.add(
                m.Connection(
                    project_id=second_id,
                    name="bad-cross-project",
                    connector_type="mysql",
                    endpoint={"kind": "database"},
                    secret_ref_id=secret_id,
                    environment="isolated",
                )
            )


def test_profile_xor_and_append_only(postgres):
    _, factory = postgres
    with factory.begin() as session:
        project = make_project(session)
        asset = m.FileAsset(
            id=uuid4(),
            project_id=project.id,
            file_name="test.csv",
            file_path="protected/test",
            file_size=1,
            file_format="csv",
            content_digest="b" * 64,
        )
        session.add(asset)
        session.flush()
        profile = m.MetadataProfile(
            id=uuid4(),
            project_id=project.id,
            file_asset_id=asset.id,
            resource_name="test",
            fields=[],
            statistics={"row_count": None, "is_approximate": True},
            profile_digest="c" * 64,
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
        session.add(profile)
        session.flush()
        profile_id, project_id = profile.id, project.id
    with pytest.raises(IntegrityError):
        with factory.begin() as session:
            session.execute(
                text("UPDATE metadata_profiles SET resource_name = 'changed' WHERE id = :id"),
                {"id": profile_id},
            )
    with pytest.raises(IntegrityError):
        with factory.begin() as session:
            session.add(
                m.MetadataProfile(
                    project_id=project_id,
                    resource_name="invalid",
                    fields=[],
                    statistics={},
                    profile_digest="d" * 64,
                    expires_at=datetime.now(UTC) + timedelta(minutes=15),
                )
            )


def test_same_person_cannot_approve_as_different_account(postgres):
    _, factory = postgres
    with factory.begin() as session:
        maker, project, version, prep = make_preparation(session)
        checker = make_user(session, person_ref=maker.person_ref)
        session.add(
            m.ProjectMembership(project_id=project.id, user_id=checker.id, role="data_checker")
        )
        session.add(m.ProjectRoleGrant(project_id=project.id, user_id=checker.id, role_slot="data"))
        approval = m.ApprovalRequest(
            id=uuid4(),
            project_id=project.id,
            preparation_id=prep.id,
            version_id=version.id,
            required_role="data",
        )
        session.add(approval)
        session.flush()
        approval_id, checker_id = approval.id, checker.id
    with pytest.raises(IntegrityError):
        with factory.begin() as session:
            approval = session.get(m.ApprovalRequest, approval_id)
            approval.status, approval.decision = "approved", "approve"
            approval.approver_id, approval.decided_at = checker_id, datetime.now(UTC)


def test_commit_records_are_atomic_and_do_not_consume(postgres):
    _, factory = postgres
    with factory.begin() as session:
        user, project, version, prep = make_preparation(session)
        ids = user.id, project.id, version.id, prep.id
    run_id = uuid4()
    with pytest.raises(RuntimeError, match="injected failure"):
        with factory.begin() as session:
            session.add(
                m.ExecutionRun(
                    id=run_id,
                    project_id=ids[1],
                    version_id=ids[2],
                    preparation_id=ids[3],
                    capability_token_digest="e" * 64,
                )
            )
            session.flush()
            raise RuntimeError("injected failure")
    with factory() as session:
        assert session.get(m.ExecutionRun, run_id) is None
        assert (
            session.scalar(
                select(m.CapabilityConsumption).where(
                    m.CapabilityConsumption.execution_run_id == run_id
                )
            )
            is None
        )


def command_fixture(factory, *, expired=False):
    with factory.begin() as session:
        user, project, version, prep = make_preparation(session)
        run_id, command_id, cap_id = uuid4(), uuid4(), uuid4()
        now = datetime.now(UTC)
        session.add(
            m.ExecutionRun(
                id=run_id,
                project_id=project.id,
                version_id=version.id,
                preparation_id=prep.id,
                capability_token_digest="f" * 64,
            )
        )
        session.flush()
        session.add(
            m.ManagedCommand(
                id=command_id,
                project_id=project.id,
                execution_run_id=run_id,
                preparation_id=prep.id,
                tool="execute_etl",
                capability_id=cap_id,
                authorization_ref="protected/test",
                payload_digest="a" * 64,
                idempotency_key=uuid4().hex,
            )
        )
        session.add(
            m.CapabilityAuthorization(
                capability_id=cap_id,
                project_id=project.id,
                command_id=command_id,
                subject_id=user.id,
                preparation_id=prep.id,
                tool="execute_etl",
                environment="isolated",
                resource_scopes=[],
                artifact_digest="b" * 64,
                input_fingerprint=prep.input_fingerprint,
                issued_at=now - timedelta(minutes=10),
                expires_at=now - timedelta(minutes=1) if expired else now + timedelta(minutes=5),
                key_id="test",
                encrypted_token="encrypted-test-only",
                token_digest="f" * 64,
            )
        )
        return dict(
            project_id=project.id,
            capability_id=cap_id,
            command_id=command_id,
            execution_run_id=run_id,
            token_digest="f" * 64,
        )


def test_concurrent_capability_consumption_has_exactly_one_winner(postgres):
    _, factory = postgres
    values = command_fixture(factory)

    def consume():
        try:
            with factory.begin() as session:
                session.add(m.CapabilityConsumption(**values))
            return "consumed"
        except IntegrityError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: consume(), range(2)))
    assert sorted(results) == ["consumed", "rejected"]
    with factory() as session:
        assert session.get(m.CapabilityConsumption, values["capability_id"]) is not None


def test_expired_capability_cannot_use_backdated_consumption(postgres):
    _, factory = postgres
    values = command_fixture(factory, expired=True)
    with pytest.raises(IntegrityError):
        with factory.begin() as session:
            session.add(
                m.CapabilityConsumption(**values, consumed_at=datetime.now(UTC) - timedelta(days=1))
            )


def test_audit_chain_cannot_be_rewritten_or_skip_sequence(postgres):
    _, factory = postgres
    with factory.begin() as session:
        project = make_project(session)
        event = m.AuditEvent(
            project_id=project.id,
            actor_kind="system",
            event_type="test.v1",
            resource_type="project",
            resource_id=str(project.id),
            payload_digest="a" * 64,
            event_hash="b" * 64,
            sequence_no=1,
        )
        session.add(event)
        session.flush()
        project_id, event_id = project.id, event.id
    with pytest.raises(IntegrityError):
        with factory.begin() as session:
            session.execute(
                text("UPDATE audit_events SET event_type = 'tampered' WHERE id = :id"),
                {"id": event_id},
            )
    with pytest.raises(IntegrityError):
        with factory.begin() as session:
            session.add(
                m.AuditEvent(
                    project_id=project_id,
                    actor_kind="system",
                    event_type="test.v1",
                    resource_type="project",
                    resource_id=str(project_id),
                    payload_digest="a" * 64,
                    prev_event_hash="b" * 64,
                    event_hash="c" * 64,
                    sequence_no=3,
                )
            )


def test_database_rejects_non_postgresql_url():
    with pytest.raises(ValueError, match="PostgreSQL"):
        Database("sqlite:///:memory:")


class _TestSecretProvider:
    def __init__(self):
        self.values = {}
        self.sequence = 0

    def store(self, alias, values):
        from etl_agent.modules.assets.ports import StoredSecret

        self.sequence += 1
        key = f"{alias}:{self.sequence}"
        self.values[key] = dict(values)
        return StoredSecret("test", key, str(self.sequence))

    def resolve(self, secret):
        return self.values[secret.locator]


class _ReadOnlyProbe:
    def __init__(self, engine, *, fail=False):
        self.engine, self.fail, self.calls = engine, fail, 0

    def test(self, endpoint, credentials):
        assert self.engine.pool.checkedout() == 0, "external I/O must not hold a DB transaction"
        self.calls += 1
        if self.fail:
            raise RuntimeError("password=private-driver-detail")

    def profile(self, endpoint, credentials, resource_names, sample_limit):
        from etl_agent.modules.assets.ports import ProbeProfile

        self.test(endpoint, credentials)
        return [
            ProbeProfile(
                name,
                [
                    {
                        "field_name": "email",
                        "ordinal": 0,
                        "native_type": "text",
                        "logical_type": "string",
                        "nullable": True,
                        "is_primary_key": False,
                        "precision": None,
                        "scale": None,
                        "sensitivity": "unclassified",
                    }
                ],
                {"row_count": "10", "is_approximate": True},
                [{"values": {"email": "[REDACTED]"}, "masked_fields": ["email"]}]
                if sample_limit
                else [],
            )
            for name in resource_names
        ]


def _assets_setup(postgres, tmp_path, *, fail=False):
    from types import SimpleNamespace

    from etl_agent.api.schemas import contracts as c
    from etl_agent.config import Settings
    from etl_agent.modules.assets.ports import ProbeRegistry
    from etl_agent.modules.assets.service import AssetsService
    from etl_agent.modules.audit.service import EvidenceLedger
    from etl_agent.shared.context import ActorContext

    engine, factory = postgres
    with factory.begin() as session:
        user, project = make_user(session), make_project(session)
        actor = ActorContext(
            user.id, user.person_ref, {project.id: frozenset({"maker", "operator"})}
        )
        project_id = project.id
    provider, probe = _TestSecretProvider(), _ReadOnlyProbe(engine, fail=fail)
    service = AssetsService(
        Settings(
            environment="test",
            storage_root=tmp_path,
            endpoint_allowlist=["127.0.0.1"],
            probe_connectors=frozenset({"postgresql"}),
        ),
        EvidenceLedger(),
        secrets=provider,
        probes=ProbeRegistry({"postgresql": probe}),
    )
    payload = c.ConnectionCreateRequest(
        project_id=project_id,
        name="source_" + uuid4().hex,
        connector_type="postgresql",
        endpoint={
            "kind": "database",
            "host": "127.0.0.1",
            "port": 5432,
            "database": "test",
            "schema": "public",
        },
        environment="test",
        credentials={
            "mode": "inline_secret",
            "secret_values": {"username": "etl", "password": "private-test-password"},
        },
    )
    with factory() as session:
        connection = service.create_connection(session, actor, payload)
        session.commit()
    return service, actor, connection, SimpleNamespace(session_factory=factory), probe


def test_assets_connection_replace_omission_and_secret_rotation(postgres, tmp_path):
    from etl_agent.api.schemas import contracts as c

    service, actor, connection, database, _ = _assets_setup(postgres, tmp_path)
    factory = database.session_factory
    connection_id, old_secret = UUID(connection["id"]), UUID(connection["secret_ref_id"])
    replacement = c.ConnectionReplaceRequest(
        name="renamed_" + uuid4().hex,
        connector_type="postgresql",
        endpoint=connection["endpoint"],
        environment="test",
    )
    with factory() as session:
        result = service.replace_connection(session, actor, connection_id, replacement, 1)
        session.commit()
    assert result["secret_ref_id"] == str(old_secret)
    assert result["revision"] == 2
    with factory() as session:
        rotated = service.rotate_secret(
            session,
            actor,
            c.RotateCredentialRequest(
                secret_ref_id=old_secret,
                expected_revision=1,
                credentials={
                    "mode": "inline_secret",
                    "secret_values": {"username": "etl", "password": "rotated-private-password"},
                },
            ),
        )
        session.commit()
    assert rotated["id"] != str(old_secret)
    with factory() as session:
        result = service.get_connection(session, actor, connection_id)
    assert result["revision"] == 3
    assert result["secret_ref_id"] == rotated["id"]
    assert "password" not in str(result)
    with pytest.raises(ApplicationError) as failure:
        with factory() as session:
            service.replace_connection(session, actor, connection_id, replacement, 1)
    assert failure.value.status_code == 412


def test_assets_persistent_test_and_profile_complete_without_long_transaction(postgres, tmp_path):
    from etl_agent.api.schemas import contracts as c

    service, actor, connection, database, probe = _assets_setup(postgres, tmp_path)
    with database.session_factory() as session:
        accepted = service.start_test(session, actor, UUID(connection["id"]))
        task = service.start_profile(
            session,
            actor,
            UUID(connection["id"]),
            c.ProfileCreateRequest(
                resource_names=["public.customer", "public.customer"], sample_limit=1
            ),
        )
        session.commit()
    service.process_test(database, UUID(accepted["id"]))
    service.process_profile(database, UUID(task["resource_id"]))
    with database.session_factory() as session:
        test = service.get_test(session, actor, UUID(accepted["id"]))
        observed = service.get_task(session, actor, UUID(task["resource_id"]))
        profile = service.get_profile(session, actor, UUID(observed["profile_ids"][0]))
    assert test["status"] == "succeeded"
    assert observed["status"] == "succeeded" and len(observed["profile_ids"]) == 1
    assert profile["masked_samples"][0]["values"] == {"email": "[REDACTED]"}
    assert probe.calls == 2
    service.process_profile(database, UUID(task["resource_id"]))
    assert probe.calls == 2


def test_assets_probe_failure_is_durable_and_sanitized(postgres, tmp_path):
    from etl_agent.api.schemas import contracts as c

    service, actor, connection, database, _ = _assets_setup(postgres, tmp_path, fail=True)
    with database.session_factory() as session:
        task = service.start_profile(
            session,
            actor,
            UUID(connection["id"]),
            c.ProfileCreateRequest(resource_names=["public.customer"]),
        )
        session.commit()
    service.process_profile(database, UUID(task["resource_id"]))
    with database.session_factory() as session:
        observed = service.get_task(session, actor, UUID(task["resource_id"]))
    assert observed["status"] == "failed"
    assert observed["profile_ids"] == []
    assert "private-driver-detail" not in str(observed)
    assert observed["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


def test_assets_upload_uses_managed_key_and_keeps_failed_parse_honest(postgres, tmp_path):
    from etl_agent.api.schemas import contracts as c

    service, actor, connection, database, _ = _assets_setup(postgres, tmp_path)
    project_id = UUID(connection["project_id"])
    with database.session_factory() as session:
        uploaded = service.upload(
            session,
            actor,
            c.FileAssetUploadRequest(
                project_id=project_id, file=b"id,email\n001,test@example.com\n"
            ),
            "../../unsafe/customer.csv",
        )
        invalid_asset = service.upload(
            session,
            actor,
            c.FileAssetUploadRequest(project_id=project_id, file=b"not JSON"),
            "bad.json",
        )
        session.commit()
    assert uploaded["file_name"] == "customer.csv" and uploaded["parse_status"] == "succeeded"
    assert "file_path" not in uploaded
    assert invalid_asset["parse_status"] == "failed" and invalid_asset["fields"] is None
    assert len(list(tmp_path.iterdir())) == 2
    assert all(
        path.parent == tmp_path and "customer" not in path.name for path in tmp_path.iterdir()
    )
