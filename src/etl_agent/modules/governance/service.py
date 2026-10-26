"""Frozen preparation facts and independent approvals, with no external writes."""

from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from etl_agent.api.schemas import contracts as c
from etl_agent.config import Settings
from etl_agent.infrastructure.persistence import models as m
from etl_agent.infrastructure.persistence.access import (
    project_entity,
    public_record,
    required_record,
)
from etl_agent.modules.audit.service import EvidenceLedger
from etl_agent.modules.identity_access.domain import assert_action, assert_separation
from etl_agent.modules.identity_access.service import ProjectService, actor_from_user
from etl_agent.shared.canonical import digest
from etl_agent.shared.context import ActorContext
from etl_agent.shared.errors import ApplicationError, forbidden, invalid, unavailable


class GovernanceService:
    def __init__(self, settings: Settings, ledger: EvidenceLedger):
        self.settings, self.ledger = settings, ledger
        self.projects = ProjectService(ledger, settings)

    def collect_bindings(
        self, session: Session, version: m.PipelineVersion, *, require_fresh: bool = True
    ) -> tuple[list[dict], list[m.MetadataProfile]]:
        plan = c.EtlPlan.model_validate(version.etl_plan_json)
        ids = list(dict.fromkeys([*plan.source_profile_ids, plan.target_profile_id]))
        now = session.execute(select(func.clock_timestamp())).scalar_one()
        profiles: list[m.MetadataProfile] = []
        bindings: dict[tuple[str, UUID], dict] = {}
        for profile_id in ids:
            profile = session.get(m.MetadataProfile, profile_id)
            if profile is None or profile.project_id != version.project_id:
                raise forbidden("制品引用了非本项目Profile")
            if require_fresh and now >= profile.expires_at:
                raise ApplicationError("FINGERPRINT_CHANGED", "Profile已经过期，需要重新探查", 409)
            profiles.append(profile)
            bindings[("profile", profile.id)] = {
                "resource_type": "profile",
                "resource_id": str(profile.id),
                "resource_revision": None,
                "content_digest": profile.profile_digest,
                "secret_version": None,
            }
            if profile.connection_id:
                connection = required_record(session, m.Connection, profile.connection_id)
                if connection is None or connection.project_id != version.project_id:
                    raise forbidden("连接归属不匹配")
                bindings[("connection", connection.id)] = {
                    "resource_type": "connection",
                    "resource_id": str(connection.id),
                    "resource_revision": connection.revision,
                    "content_digest": digest(
                        {
                            "endpoint": connection.endpoint,
                            "environment": connection.environment,
                            "connector_type": connection.connector_type,
                        }
                    ),
                    "secret_version": None,
                }
                if connection.secret_ref_id:
                    secret = session.get(m.SecretReference, connection.secret_ref_id)
                    if secret is None or secret.project_id != version.project_id:
                        raise forbidden("Secret引用归属不匹配")
                    bindings[("secret", secret.id)] = {
                        "resource_type": "secret",
                        "resource_id": str(secret.id),
                        "resource_revision": secret.revision,
                        "content_digest": None,
                        "secret_version": secret.secret_version,
                    }
            if profile.file_asset_id:
                asset = session.get(m.FileAsset, profile.file_asset_id)
                if asset is None or asset.project_id != version.project_id:
                    raise forbidden("文件归属不匹配")
                bindings[("file", asset.id)] = {
                    "resource_type": "file",
                    "resource_id": str(asset.id),
                    "resource_revision": None,
                    "content_digest": asset.content_digest,
                    "secret_version": None,
                }
        error_scope = plan.quality_contract.error_resource
        if error_scope.connection_id:
            error_connection = session.get(m.Connection, error_scope.connection_id)
            if error_connection is None or error_connection.project_id != version.project_id:
                raise forbidden("错误流连接归属不匹配")
            if error_connection.environment != error_scope.environment:
                raise forbidden("错误流实际连接环境不匹配")
            bindings[("connection", error_connection.id)] = {
                "resource_type": "connection",
                "resource_id": str(error_connection.id),
                "resource_revision": error_connection.revision,
                "content_digest": digest(
                    {
                        "endpoint": error_connection.endpoint,
                        "environment": error_connection.environment,
                        "connector_type": error_connection.connector_type,
                    }
                ),
                "secret_version": None,
            }
            if error_connection.secret_ref_id:
                secret = session.get(m.SecretReference, error_connection.secret_ref_id)
                if secret is None or secret.project_id != version.project_id:
                    raise forbidden("错误流Secret归属不匹配")
                bindings[("secret", secret.id)] = {
                    "resource_type": "secret",
                    "resource_id": str(secret.id),
                    "resource_revision": secret.revision,
                    "content_digest": None,
                    "secret_version": secret.secret_version,
                }
        if error_scope.file_asset_id:
            asset = session.get(m.FileAsset, error_scope.file_asset_id)
            if asset is None or asset.project_id != version.project_id:
                raise forbidden("错误流文件归属不匹配")
            bindings[("file", asset.id)] = {
                "resource_type": "file",
                "resource_id": str(asset.id),
                "resource_revision": None,
                "content_digest": asset.content_digest,
                "secret_version": None,
            }
        return [
            bindings[key] for key in sorted(bindings, key=lambda item: (item[0], str(item[1])))
        ], profiles

    @staticmethod
    def fingerprint(
        version: m.PipelineVersion,
        *,
        bindings: list[dict],
        purpose: str,
        environment: str,
        quality: dict,
        supervision: dict,
        rollback: dict,
        scopes: list[dict],
        policy_version: str,
        evaluation: dict | None,
    ) -> str:
        return digest(
            {
                "schema_version": 1,
                "artifact_digest": version.artifact_digest,
                "input_bindings": bindings,
                "purpose": purpose,
                "environment": environment,
                "quality_contract": quality,
                "supervision_contract": supervision,
                "rollback_plan": rollback,
                "resource_scopes": scopes,
                "policy_version": policy_version,
                "evaluation_binding": evaluation,
            }
        )

    def prepare(
        self, session: Session, actor: ActorContext, version_id: UUID, payload: c.PrepareRequest
    ) -> dict:
        version = project_entity(session, m.PipelineVersion, version_id, actor, "prepare")
        if not version.is_immutable or not version.artifact_digest:
            raise ApplicationError("STATE_CONFLICT", "只能准备已冻结且通过门禁的版本", 409)
        if payload.environment not in self.settings.permitted_environments:
            raise forbidden("环境不在部署允许范围")
        bindings, profiles = self.collect_bindings(
            session, version, require_fresh=payload.purpose.value != "rollback"
        )
        plan = c.EtlPlan.model_validate(version.etl_plan_json)
        budget = payload.supervision_contract.model_dump(mode="json")
        caps = {
            "max_input_records": "1000000",
            "max_output_bytes": "1073741824",
            "max_duration_ms": "3600000",
            "max_amplification_ratio": "10",
            "max_rejection_ratio": "0.05",
        }
        for name, maximum in caps.items():
            if Decimal(budget[name]) > Decimal(maximum):
                raise forbidden(f"运行预算{name}超出当前受管策略上限")
        classifications = {field["sensitivity"] for profile in profiles for field in profile.fields}
        rules = self.settings.classification_risks
        if not classifications or any(value not in rules for value in classifications):
            raise unavailable("已确认数据分级策略")
        priority = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        classification = min(classifications, key=lambda item: priority[rules[item]])
        risk = rules[classification]
        if risk == "P0":
            raise forbidden("P0禁止级工具意图不能形成执行准备单")
        target = next(profile for profile in profiles if profile.id == plan.target_profile_id)
        prep_id = uuid4()
        purpose = payload.purpose.value
        if (
            purpose in {"isolated_trial", "run_l2_benchmark"}
            and payload.environment not in self.settings.isolated_environments
        ):
            raise forbidden("真实试运行和L2只能使用明确配置的隔离环境")
        scopes: list[dict] = []
        for profile in profiles:
            if profile.connection_id:
                connection = required_record(session, m.Connection, profile.connection_id)
                if connection.environment != payload.environment:
                    raise forbidden("源/目标连接环境与申请环境不一致")
            operations = (
                ["profile"]
                if profile.id == target.id
                else ["execute_etl", "run_l2_benchmark", "cancel", "pause", "resume"]
            )
            scopes.append(
                {
                    "connection_id": str(profile.connection_id) if profile.connection_id else None,
                    "file_asset_id": str(profile.file_asset_id) if profile.file_asset_id else None,
                    "resource_name": profile.resource_name,
                    "operations": operations,
                    "environment": payload.environment,
                }
            )
        target_scope = {
            "connection_id": str(target.connection_id) if target.connection_id else None,
            "file_asset_id": str(target.file_asset_id) if target.file_asset_id else None,
            "resource_name": target.resource_name,
            "operations": ["publish", "rollback", "cleanup"],
            "environment": payload.environment,
        }
        if target.connection_id is None:
            raise invalid("本期原子发布目标必须是已验证的受管数据库连接")
        if purpose in {"isolated_trial", "run_l2_benchmark"}:
            target_scope["operations"] = ["cleanup"]
        scopes.append(plan.quality_contract.error_resource.model_dump(mode="json"))
        if scopes[-1]["environment"] != payload.environment:
            raise forbidden("错误流必须属于同一批准环境")
        for scope in scopes:
            ref = scope.get("connection_id")
            if ref:
                resource = session.get(m.Connection, UUID(ref))
                if resource is None or resource.project_id != version.project_id:
                    raise forbidden("错误/执行资源越项目")
        shadow = f"{target.resource_name}__etl_{prep_id.hex}"
        scopes.append(
            {
                **target_scope,
                "resource_name": shadow,
                "operations": [
                    "execute_etl",
                    "run_l2_benchmark",
                    "cancel",
                    "pause",
                    "resume",
                    "cleanup",
                ],
            }
        )
        scopes.append(target_scope)
        retained = f"{target.resource_name}__retained_{prep_id.hex}"
        scopes.append(
            {
                **target_scope,
                "resource_name": retained,
                "operations": ["publish", "rollback", "cleanup"],
            }
        )
        key = digest(
            {
                name: target_scope[name]
                for name in ("connection_id", "file_asset_id", "resource_name", "environment")
            }
        )
        head = session.get(m.TargetHead, (version.project_id, key))
        rollback = {
            "schema_version": 1,
            "target": target_scope,
            "shadow_resource": shadow,
            "retained_resource": retained,
            "retention_ms": "86400000",
            "expected_generation": str(head.generation) if head else "0",
            "steps": ["核验目标发布代次与保留资源", "仅恢复或清理本次冻结资源"],
        }
        if purpose == "rollback":
            if payload.execution_run_id is None:
                raise invalid("回滚必须指定原运行")
            original = project_entity(
                session, m.ExecutionRun, payload.execution_run_id, actor, "rollback", lock=True
            )
            if original.version_id != version.id:
                raise forbidden("回滚准备与原运行版本不一致")
            old_preparation = required_record(session, m.Preparation, original.preparation_id)
            rollback = dict(old_preparation.rollback_plan)
            cleanup_names = {
                rollback["shadow_resource"],
                old_preparation.quality_contract["error_resource"]["resource_name"],
            }
            scopes = [
                {
                    **scope,
                    "operations": list(dict.fromkeys([*scope["operations"], "rollback"]))
                    if scope["resource_name"] in cleanup_names
                    else list(scope["operations"]),
                }
                for scope in old_preparation.resource_scopes
            ]
        evaluation = (
            payload.evaluation_binding.model_dump(mode="json")
            if payload.evaluation_binding
            else None
        )
        if evaluation:
            dataset = project_entity(
                session, m.BenchmarkDataset, UUID(evaluation["dataset_id"]), actor
            )
            if dataset.content_digest != evaluation["dataset_digest"]:
                raise ApplicationError("FINGERPRINT_CHANGED", "用例集摘要不匹配", 409)
            cases = session.scalars(
                select(m.BenchmarkCase).where(
                    m.BenchmarkCase.dataset_id == dataset.id, m.BenchmarkCase.level == "L2"
                )
            ).all()
            if not cases:
                raise invalid("用例集中没有L2用例")
            if any(case.input.get("version_id") != str(version.id) for case in cases):
                raise invalid("同一L2准备只能授权同一被测冻结版本")
            for case in cases:
                scopes.append(
                    {
                        **target_scope,
                        "resource_name": f"{shadow}_{case.id.hex}",
                        "operations": ["run_l2_benchmark", "cancel", "pause", "resume", "cleanup"],
                    }
                )
                error = plan.quality_contract.error_resource.model_dump(mode="json")
                scopes.append(
                    {
                        **error,
                        "resource_name": f"{error['resource_name']}__etl_{prep_id.hex}_{case.id.hex}",
                        "operations": ["run_l2_benchmark", "cleanup"],
                    }
                )
        policy_version = self.settings.policy_version
        quality = plan.quality_contract.model_dump(mode="json")
        fingerprint = self.fingerprint(
            version,
            bindings=bindings,
            purpose=purpose,
            environment=payload.environment,
            quality=quality,
            supervision=budget,
            rollback=rollback,
            scopes=scopes,
            policy_version=policy_version,
            evaluation=evaluation,
        )
        now = session.execute(select(func.clock_timestamp())).scalar_one()
        preparation = m.Preparation(
            id=prep_id,
            project_id=version.project_id,
            version_id=version.id,
            purpose=purpose,
            execution_run_id=payload.execution_run_id,
            maker_id=actor.user_id,
            input_fingerprint=fingerprint,
            input_bindings=bindings,
            resource_scopes=scopes,
            impact_summary="读取冻结源Profile，仅写批准影子/错误资源，发布须独立受管命令",
            data_classification=classification,
            environment=payload.environment,
            risk_level=risk,
            required_slots=["data", "security"],
            quality_contract=quality,
            supervision_contract=budget,
            rollback_plan=rollback,
            policy_version=policy_version,
            expires_at=now + timedelta(minutes=30),
            evaluation_binding=evaluation,
        )
        session.add(preparation)
        # Approval's immediate guard reads the parent; a deferred FK alone does
        # not order ORM inserts when no ORM relationship is declared.
        session.flush([preparation])
        for slot in preparation.required_slots:
            session.add(
                m.ApprovalRequest(
                    project_id=version.project_id,
                    preparation_id=prep_id,
                    version_id=version.id,
                    required_role=slot,
                    status="pending",
                )
            )
        session.flush()
        self.ledger.append(
            session,
            version.project_id,
            actor,
            "prepared",
            "preparation",
            prep_id,
            {"fingerprint": fingerprint, "purpose": purpose},
        )
        return self.get(session, actor, prep_id)

    def assert_fresh(
        self, session: Session, preparation: m.Preparation, *, allow_expired: bool = False
    ) -> None:
        now = session.execute(select(func.clock_timestamp())).scalar_one()
        if not allow_expired and now >= preparation.expires_at:
            raise ApplicationError("STATE_CONFLICT", "准备单已经过期", 409)
        if preparation.policy_version != self.settings.policy_version:
            raise ApplicationError("FINGERPRINT_CHANGED", "受管策略版本已经变化", 409)
        version = required_record(session, m.PipelineVersion, preparation.version_id)
        if version is None or not version.is_immutable:
            raise ApplicationError("FINGERPRINT_CHANGED", "版本事实不再有效", 409)
        bindings, _ = self.collect_bindings(
            session, version, require_fresh=not allow_expired and preparation.purpose != "rollback"
        )
        current = self.fingerprint(
            version,
            bindings=bindings,
            purpose=preparation.purpose,
            environment=preparation.environment,
            quality=preparation.quality_contract,
            supervision=preparation.supervision_contract,
            rollback=preparation.rollback_plan,
            scopes=preparation.resource_scopes,
            policy_version=preparation.policy_version,
            evaluation=preparation.evaluation_binding,
        )
        if current != preparation.input_fingerprint:
            raise ApplicationError(
                "FINGERPRINT_CHANGED", "Schema、连接、Secret或冻结输入已经变化", 409
            )
        if preparation.evaluation_binding:
            dataset = session.get(
                m.BenchmarkDataset, UUID(preparation.evaluation_binding["dataset_id"])
            )
            if (
                dataset is None
                or dataset.content_digest != preparation.evaluation_binding["dataset_digest"]
            ):
                raise ApplicationError("FINGERPRINT_CHANGED", "用例集绑定已经变化", 409)

    def assert_approved(self, session: Session, preparation: m.Preparation) -> None:
        approvals = session.scalars(
            select(m.ApprovalRequest).where(m.ApprovalRequest.preparation_id == preparation.id)
        ).all()
        maker = required_record(session, m.User, preparation.maker_id)
        if maker is None or maker.status != "enabled":
            raise forbidden("申请主体已停用")
        assert_action(actor_from_user(session, maker), preparation.project_id, "prepare")
        people: set[UUID] = set()
        for slot in preparation.required_slots:
            approval = next((item for item in approvals if item.required_role == slot), None)
            if approval is None or approval.status != "approved" or approval.approver_id is None:
                raise ApplicationError("STATE_CONFLICT", "尚未取得全部独立必需审批", 409)
            checker = session.get(m.User, approval.approver_id)
            if checker is None or checker.status != "enabled":
                raise forbidden("审批主体已停用")
            checker_actor = actor_from_user(session, checker)
            assert_action(checker_actor, preparation.project_id, f"approve_{slot}")
            assert_separation(maker.person_ref, checker.person_ref, people)
            people.add(checker.person_ref)

    def decide(
        self,
        session: Session,
        actor: ActorContext,
        approval_id: UUID,
        payload: c.ApprovalDecisionRequest,
        expected_revision: int,
    ) -> dict:
        approval = project_entity(session, m.ApprovalRequest, approval_id, actor)
        session.scalar(
            select(m.Project).where(m.Project.id == approval.project_id).with_for_update()
        )
        preparation = session.scalars(
            select(m.Preparation)
            .where(m.Preparation.id == approval.preparation_id)
            .with_for_update()
        ).one()
        session.refresh(approval)
        if approval.revision != expected_revision:
            raise ApplicationError("REVISION_MISMATCH", "审批记录已经变化", 412)
        self.assert_fresh(session, preparation)
        assert_action(actor, approval.project_id, f"approve_{approval.required_role}")
        if approval.status != "pending":
            raise ApplicationError("STATE_CONFLICT", "审批决定已记录，不可覆盖历史决定", 409)
        maker = required_record(session, m.User, preparation.maker_id)
        others = session.scalars(
            select(m.User)
            .join(m.ApprovalRequest, m.ApprovalRequest.approver_id == m.User.id)
            .where(
                m.ApprovalRequest.preparation_id == preparation.id,
                m.ApprovalRequest.id != approval.id,
                m.ApprovalRequest.status == "approved",
            )
        ).all()
        assert_separation(maker.person_ref, actor.person_ref, {item.person_ref for item in others})
        approval.decision = payload.decision
        approval.status = "approved" if payload.decision == "approve" else "rejected"
        approval.approver_id = actor.user_id
        approval.decided_at = session.execute(select(func.clock_timestamp())).scalar_one()
        approval.revision += 1
        session.flush()
        self.ledger.append(
            session,
            approval.project_id,
            actor,
            "approval_decided",
            "approval",
            approval.id,
            {"decision": payload.decision, "slot": approval.required_role},
        )
        return public_record(approval, c.ApprovalResponse)

    def get(self, session: Session, actor: ActorContext, preparation_id: UUID) -> dict:
        preparation = project_entity(session, m.Preparation, preparation_id, actor)
        approvals = session.scalars(
            select(m.ApprovalRequest)
            .where(m.ApprovalRequest.preparation_id == preparation.id)
            .order_by(m.ApprovalRequest.required_role)
        ).all()
        validity = "valid"
        try:
            self.assert_fresh(session, preparation)
        except ApplicationError:
            now = session.execute(select(func.clock_timestamp())).scalar_one()
            validity = "expired" if now >= preparation.expires_at else "stale"
        if any(item.status == "rejected" for item in approvals):
            validity = "blocked" if validity == "valid" else validity
        actions = self.projects.actions(
            actor, preparation.project_id, ["approve_data", "approve_security", "commit"]
        )
        for action in actions:
            if validity != "valid":
                action.update(
                    allowed=False,
                    reason_code="STATE_CONFLICT",
                    reason_message="准备事实已失效或受阻",
                )
            if action["action"].startswith("approve_") and actor.user_id == preparation.maker_id:
                action.update(
                    allowed=False,
                    reason_code="SELF_APPROVAL_DENIED",
                    reason_message="申请人不能自批",
                )
            if action["action"] == "commit":
                try:
                    self.assert_approved(session, preparation)
                except ApplicationError as exc:
                    action.update(allowed=False, reason_code=exc.code, reason_message=exc.message)
        return {
            "preparation": public_record(preparation, c.PreparationResponseBody),
            "approvals": [public_record(item, c.ApprovalResponse) for item in approvals],
            "validity": validity,
            "available_actions": actions,
        }
