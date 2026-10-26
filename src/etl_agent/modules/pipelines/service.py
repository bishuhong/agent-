"""Candidate lifecycle and immutable artifacts; no execution side effects."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from etl_agent.api.schemas import contracts as c
from etl_agent.config import Settings
from etl_agent.infrastructure.persistence import models as m
from etl_agent.infrastructure.persistence.access import (
    compare_revision,
    project_entity,
    public_record,
)
from etl_agent.modules.audit.service import EvidenceLedger
from etl_agent.modules.identity_access.domain import assert_action, has_action
from etl_agent.modules.validation.domain import validate_candidate
from etl_agent.shared.canonical import artifact_package, canonical_bytes
from etl_agent.shared.context import ActorContext, utcnow
from etl_agent.shared.errors import ApplicationError, invalid


def artifacts_for(plan: dict[str, Any], hocon: str) -> dict[str, Any]:
    return {
        "etl_plan": plan,
        "hocon": hocon,
        "dag": {"schema_version": 1, "nodes": plan["nodes"], "edges": plan["edges"]},
        "quality_contract": plan["quality_contract"],
        "supervision_contract": plan["supervision_contract"],
    }


def candidate_plan(version: m.PipelineVersion) -> dict[str, Any]:
    if not isinstance(version.etl_plan_json, dict):
        raise invalid("候选计划必须是类型化对象")
    return version.etl_plan_json


def schema_fields(profile: m.MetadataProfile) -> list[dict[str, Any]]:
    if not isinstance(profile.fields, list) or any(
        not isinstance(item, dict) for item in profile.fields
    ):
        raise invalid("Profile字段结构无效")
    return profile.fields


def profile_context(
    session: Session,
    actor: ActorContext,
    source_ids: list[UUID],
    target_id: UUID,
    project_id: UUID,
    *,
    require_fresh: bool = True,
) -> tuple[list[m.MetadataProfile], m.MetadataProfile]:
    profiles = [
        project_entity(session, m.MetadataProfile, resource_id, actor) for resource_id in source_ids
    ]
    target = project_entity(session, m.MetadataProfile, target_id, actor)
    all_profiles = [*profiles, target]
    if any(profile.project_id != project_id for profile in all_profiles):
        raise invalid("所有Profile必须属于同一项目")
    if require_fresh and any(profile.expires_at <= utcnow() for profile in all_profiles):
        raise ApplicationError("FINGERPRINT_CHANGED", "Profile已过期，请重新探查", 409)
    return profiles, target


def validate_materials(
    session: Session,
    actor: ActorContext,
    version: m.PipelineVersion,
    plan: dict[str, Any],
    hocon: str,
):
    sources, target = profile_context(
        session,
        actor,
        [UUID(value) for value in plan["source_profile_ids"]],
        UUID(plan["target_profile_id"]),
        version.project_id,
    )
    source_schema: dict[str, Any] = {}
    resources: set[str] = set()
    secret_refs: set[str] = set()
    for profile in [*sources, target]:
        resources.add(profile.resource_name)
        if profile.connection_id:
            connection = project_entity(session, m.Connection, profile.connection_id, actor)
            if connection.secret_ref_id:
                secret = project_entity(session, m.SecretReference, connection.secret_ref_id, actor)
                secret_refs.update((str(secret.id), secret.public_alias))
    # Duplicate names require an unambiguous resource.field reference.
    unqualified: dict[str, list[dict[str, Any]]] = {}
    for profile in sources:
        for field in schema_fields(profile):
            unqualified.setdefault(field["field_name"], []).append(field)
            source_schema[profile.resource_name + "." + field["field_name"]] = field
    source_schema.update({name: items[0] for name, items in unqualified.items() if len(items) == 1})
    target_schema = {field["field_name"]: field for field in schema_fields(target)}
    return validate_candidate(hocon, plan, source_schema, target_schema, resources, secret_refs)


def action_projection(
    actor: ActorContext, project_id: UUID, actions: list[str], *, frozen: bool = False
) -> list[dict[str, Any]]:
    result = []
    for action in actions:
        allowed = has_action(actor, project_id, action) and not (
            frozen and action in {"generate", "freeze_version"}
        )
        result.append(
            {
                "action": action,
                "allowed": allowed,
                "reason_code": None if allowed else "STATE_OR_PERMISSION_DENIED",
                "reason_message": None if allowed else "当前状态或职责不允许此操作",
            }
        )
    return result


class PipelineService:
    def __init__(self, settings: Settings, ledger: EvidenceLedger | None = None):
        self.settings = settings
        self.ledger = ledger or EvidenceLedger()

    def create(
        self, session: Session, actor: ActorContext, payload: c.PipelineCreateRequest
    ) -> dict[str, Any]:
        assert_action(actor, payload.project_id, "create_pipeline")
        # Lock the project to serialize application-level code checks with creation.
        project = session.scalar(
            select(m.Project).where(m.Project.id == payload.project_id).with_for_update()
        )
        if project is None:
            raise ApplicationError("RESOURCE_NOT_FOUND", "项目不存在", 404)
        if session.scalar(
            select(m.Pipeline.id).where(
                m.Pipeline.project_id == payload.project_id, m.Pipeline.code == payload.code
            )
        ):
            raise ApplicationError("STATE_CONFLICT", "项目内Pipeline代码已存在", 409)
        pipeline = m.Pipeline(
            id=uuid4(),
            project_id=payload.project_id,
            name=payload.name,
            code=payload.code,
            description=payload.description,
            status="draft",
        )
        session.add(pipeline)
        session.flush()
        version = m.PipelineVersion(
            id=uuid4(),
            project_id=payload.project_id,
            pipeline_id=pipeline.id,
            version_number=1,
            is_immutable=False,
            etl_plan_json=None,
            hocon_text=None,
            artifact_digest=None,
            validation_report_id=None,
        )
        session.add(version)
        session.flush()
        self.ledger.append(
            session, payload.project_id, actor, "pipeline_created", "pipeline", pipeline.id
        )
        return {
            "pipeline": public_record(
                pipeline,
                c.PipelineResponse,
                available_actions=action_projection(
                    actor, payload.project_id, ["view", "generate"]
                ),
            ),
            "candidate_version_id": str(version.id),
        }

    def design(self, session: Session, actor: ActorContext, version_id: UUID) -> dict[str, Any]:
        version = project_entity(session, m.PipelineVersion, version_id, actor)
        report = (
            session.get(m.ValidationReport, version.validation_report_id)
            if version.validation_report_id
            else None
        )
        return c.DesignResponse.model_validate(
            {
                "version": public_record(version, c.PipelineVersionResponse),
                "etl_plan": version.etl_plan_json,
                "hocon_text": version.hocon_text,
                "validation_report": public_record(report, c.ValidationReportResponse)
                if report
                else None,
                "available_actions": action_projection(
                    actor,
                    version.project_id,
                    ["view", "generate", "freeze_version", "prepare"],
                    frozen=version.is_immutable,
                ),
            }
        ).model_dump(mode="json")

    def freeze(
        self, session: Session, actor: ActorContext, payload: c.FreezeVersionRequest
    ) -> dict[str, Any]:
        version = project_entity(
            session, m.PipelineVersion, payload.version_id, actor, "freeze_version", lock=True
        )
        compare_revision(version.revision, payload.expected_revision)
        if version.is_immutable:
            raise ApplicationError("STATE_CONFLICT", "版本已经冻结", 409)
        if not version.etl_plan_json or not version.hocon_text:
            raise invalid("冻结前必须生成完整制品")
        report = project_entity(session, m.ValidationReport, payload.validation_report_id, actor)
        if (
            report.version_id != version.id
            or version.validation_report_id != report.id
            or not report.passed
        ):
            raise ApplicationError("STATE_CONFLICT", "必须引用当前制品的通过报告", 409)
        materials = artifacts_for(candidate_plan(version), version.hocon_text)
        root, hashes = artifact_package(materials)
        if root != payload.content_digest or root != report.content_digest:
            raise ApplicationError("FINGERPRINT_CHANGED", "制品内容已变化", 409)
        gate = validate_materials(
            session, actor, version, candidate_plan(version), version.hocon_text
        )
        if not gate.passed:
            raise ApplicationError("STATE_CONFLICT", "冻结门禁未通过，须重新校验", 409)
        version.is_immutable = True
        version.artifact_digest = root
        version.revision += 1
        version.updated_at = utcnow()
        for kind, content in materials.items():
            session.add(
                m.PipelineArtifact(
                    id=uuid4(),
                    project_id=version.project_id,
                    version_id=version.id,
                    artifact_type=kind,
                    artifact_digest=hashes[kind],
                    content=content
                    if kind == "hocon"
                    else canonical_bytes(content).decode("utf-8"),
                )
            )
        pipeline = project_entity(session, m.Pipeline, version.pipeline_id, actor, lock=True)
        pipeline.status = "ready"
        pipeline.revision += 1
        pipeline.updated_at = utcnow()
        self.ledger.append(
            session,
            version.project_id,
            actor,
            "version_frozen",
            "pipeline_version",
            version.id,
            {"artifact_digest": root},
        )
        session.flush()
        return public_record(version, c.PipelineVersionResponse)

    def new_candidate(
        self,
        session: Session,
        actor: ActorContext,
        pipeline_id: UUID,
        base_version_id: UUID | None = None,
    ) -> dict[str, Any]:
        pipeline = project_entity(session, m.Pipeline, pipeline_id, actor, "generate", lock=True)
        base = (
            project_entity(session, m.PipelineVersion, base_version_id, actor)
            if base_version_id
            else None
        )
        if base is not None and (base.pipeline_id != pipeline.id or not base.is_immutable):
            raise invalid("基础版本必须是当前Pipeline的冻结版本")
        maximum = (
            session.scalar(
                select(func.max(m.PipelineVersion.version_number)).where(
                    m.PipelineVersion.pipeline_id == pipeline.id
                )
            )
            or 0
        )
        version = m.PipelineVersion(
            id=uuid4(),
            project_id=pipeline.project_id,
            pipeline_id=pipeline.id,
            version_number=maximum + 1,
            etl_plan_json=base.etl_plan_json if base else None,
            hocon_text=base.hocon_text if base else None,
            is_immutable=False,
            artifact_digest=None,
            validation_report_id=None,
        )
        session.add(version)
        session.flush()
        self.ledger.append(
            session, pipeline.project_id, actor, "candidate_created", "pipeline_version", version.id
        )
        return public_record(version, c.PipelineVersionResponse)

    def validate_static(
        self, session: Session, actor: ActorContext, version_id: UUID
    ) -> dict[str, Any]:
        version = project_entity(
            session, m.PipelineVersion, version_id, actor, "generate", lock=True
        )
        if not version.etl_plan_json or not version.hocon_text:
            raise invalid("尚无可校验的候选制品")
        if version.is_immutable:
            report = session.get(m.ValidationReport, version.validation_report_id)
            return public_record(report, c.ValidationReportResponse)
        root, _ = artifact_package(artifacts_for(candidate_plan(version), version.hocon_text))
        result = validate_materials(
            session, actor, version, candidate_plan(version), version.hocon_text
        )
        report = m.ValidationReport(
            id=uuid4(),
            project_id=version.project_id,
            version_id=version.id,
            content_digest=root,
            rule_version=result.rule_version,
            passed=result.passed,
            issues=[asdict(issue) for issue in result.issues],
        )
        session.add(report)
        session.flush()
        version.validation_report_id = report.id
        version.revision += 1
        version.updated_at = utcnow()
        self.ledger.append(
            session,
            version.project_id,
            actor,
            "candidate_validated",
            "pipeline_version",
            version.id,
            {"passed": result.passed},
        )
        return public_record(report, c.ValidationReportResponse)

    def diff(
        self, session: Session, actor: ActorContext, base_version_id: UUID, target_version_id: UUID
    ) -> dict[str, Any]:
        base = project_entity(session, m.PipelineVersion, base_version_id, actor)
        target = project_entity(session, m.PipelineVersion, target_version_id, actor)
        if base.project_id != target.project_id or base.pipeline_id != target.pipeline_id:
            raise invalid("只能比较同一Pipeline中的版本")

        def material(version: m.PipelineVersion) -> dict[str, Any]:
            plan = candidate_plan(version) if version.etl_plan_json is not None else None
            schemas = None
            if plan:
                profiles, sink = profile_context(
                    session,
                    actor,
                    [UUID(value) for value in plan["source_profile_ids"]],
                    UUID(plan["target_profile_id"]),
                    version.project_id,
                    require_fresh=False,
                )
                schemas = {
                    "sources": [
                        {"resource_name": profile.resource_name, "fields": profile.fields}
                        for profile in profiles
                    ],
                    "target": {"resource_name": sink.resource_name, "fields": sink.fields},
                }
            return {"etl_plan": plan, "hocon_text": version.hocon_text, "schemas": schemas}

        changes: list[dict[str, Any]] = []

        def walk(before: Any, after: Any, path: str) -> None:
            if before == after:
                return
            if isinstance(before, dict) and isinstance(after, dict):
                for key in sorted(set(before) | set(after)):
                    child = path + "." + key if path else key
                    if key not in before:
                        changes.append(
                            {
                                "path": child,
                                "change_type": "added",
                                "before": None,
                                "after": after[key],
                            }
                        )
                    elif key not in after:
                        changes.append(
                            {
                                "path": child,
                                "change_type": "removed",
                                "before": before[key],
                                "after": None,
                            }
                        )
                    else:
                        walk(before[key], after[key], child)
            else:
                changes.append(
                    {"path": path, "change_type": "modified", "before": before, "after": after}
                )

        walk(material(base), material(target), "")
        return c.VersionDiffResponse.model_validate(
            {"base_version_id": base.id, "target_version_id": target.id, "changes": changes}
        ).model_dump(mode="json")
