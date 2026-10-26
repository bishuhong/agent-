"""Receipt-authorized publication and external CAS reconciliation."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from etl_agent.infrastructure.persistence import models as m
from etl_agent.infrastructure.persistence.access import required_record
from etl_agent.modules.audit.service import EvidenceLedger
from etl_agent.modules.harness.service import CommandAuthorizer
from etl_agent.modules.identity_access.service import actor_from_user
from etl_agent.shared.canonical import digest
from etl_agent.shared.context import utcnow
from etl_agent.shared.errors import ApplicationError, unavailable


def target_key(scope: dict[str, Any]) -> str:
    return digest(
        {
            name: scope[name]
            for name in ("connection_id", "file_asset_id", "resource_name", "environment")
        }
    )


@dataclass(frozen=True)
class TargetObservation:
    command_id: UUID
    # The adapter must verify the target's durable command marker and generation.
    generation: int
    retained_resource: str | None
    confirmed: bool


class TargetAdapter(Protocol):
    def execute(self, spec: dict[str, Any]) -> TargetObservation: ...
    def query(self, spec: dict[str, Any]) -> TargetObservation | None: ...


class PublicationService:
    def __init__(
        self,
        database: Any,
        authorizer: CommandAuthorizer,
        ledger: EvidenceLedger,
        adapter: TargetAdapter | None = None,
    ):
        self.database, self.authorizer, self.ledger, self.adapter = (
            database,
            authorizer,
            ledger,
            adapter,
        )

    def plan(self, session: Session, run: m.ExecutionRun) -> m.PublicationRecord | None:
        prep = required_record(session, m.Preparation, run.preparation_id)
        if prep.purpose != "execute_etl" or run.status != "succeeded":
            return None
        session.scalars(
            select(m.Project).where(m.Project.id == run.project_id).with_for_update()
        ).one()
        existing = session.scalar(
            select(m.PublicationRecord).where(m.PublicationRecord.execution_run_id == run.id)
        )
        if existing:
            return existing
        quality = session.scalar(
            select(m.QualityReport)
            .where(m.QualityReport.execution_run_id == run.id)
            .order_by(m.QualityReport.created_at.desc())
            .limit(1)
        )
        budget = session.scalar(
            select(m.RuntimeSupervisionSnapshot)
            .where(m.RuntimeSupervisionSnapshot.execution_run_id == run.id)
            .order_by(m.RuntimeSupervisionSnapshot.created_at.desc())
            .limit(1)
        )
        if (
            quality is None
            or not quality.publish_eligible
            or budget is None
            or budget.decision != "within_budget"
        ):
            return None
        parent = session.scalars(
            select(m.ManagedCommand).where(
                m.ManagedCommand.execution_run_id == run.id, m.ManagedCommand.tool == "execute_etl"
            )
        ).one()
        if session.get(m.CapabilityConsumption, parent.capability_id) is None:
            raise ApplicationError("FORBIDDEN", "发布缺少原搬运消费收据", 403)
        auth = required_record(session, m.CapabilityAuthorization, parent.capability_id)
        subject = required_record(session, m.User, auth.subject_id)
        scope = prep.rollback_plan["target"]
        expected = prep.rollback_plan["expected_generation"]
        if expected is None or not prep.rollback_plan["retained_resource"]:
            raise unavailable("冻结目标代次与保留资源绑定")
        expected = int(expected)
        row = m.PublicationRecord(
            project_id=run.project_id,
            execution_run_id=run.id,
            target=scope,
            target_key=target_key(scope),
            expected_generation=expected,
            status="not_published",
        )
        session.add(row)
        command = self.authorizer.create(
            session,
            actor_from_user(session, subject),
            prep,
            run,
            "publish",
            f"publish:{run.id}",
            parent_command_id=parent.id,
        )
        row.command_id = command.id
        session.flush()
        return row

    def execute(self, spec: dict[str, Any]) -> None:
        if self.adapter is None:
            raise unavailable("已验证的目标原子替换、恢复、清理与结果查询适配器")
        command_id, run_id = UUID(spec["command_id"]), UUID(spec["run_id"])
        tool = spec["tool"]
        if tool == "rollback":
            with self.database.session_factory() as session:
                publication_id = session.scalar(
                    select(m.PublicationRecord.id).where(
                        m.PublicationRecord.execution_run_id == run_id,
                        m.PublicationRecord.status.in_(["publishing", "published", "reconciling"]),
                    )
                )
            if publication_id is None:
                return self._clean_shadow(spec)
        if tool == "cleanup":
            raise unavailable("基于保留期限及精确资源的清理策略适配器")
        if tool not in {"publish", "rollback"}:
            raise ApplicationError("FORBIDDEN", "不受支持的发布工具", 403)
        with self.database.session_factory.begin() as session:
            run = required_record(session, m.ExecutionRun, run_id)
            session.scalars(
                select(m.Project).where(m.Project.id == run.project_id).with_for_update()
            ).one()
            completed_operation = session.scalar(
                select(m.RunOperation).where(
                    m.RunOperation.command_id == command_id, m.RunOperation.status == "succeeded"
                )
            )
            if completed_operation is not None:
                return
            scope = spec["rollback_plan"]["target"]
            key = target_key(scope)
            head = session.get(m.TargetHead, (run.project_id, key), with_for_update=True)
            if head is None:
                head = m.TargetHead(
                    project_id=run.project_id, target_key=key, generation=0, revision=1
                )
                session.add(head)
                session.flush()
            publication = session.scalar(
                select(m.PublicationRecord).where(m.PublicationRecord.execution_run_id == run.id)
            )
            if publication is None:
                raise ApplicationError("STATE_CONFLICT", "缺少可核验的发布记录", 409)
            generation = (
                publication.expected_generation
                if tool == "publish"
                else publication.published_generation
            )
            if tool == "publish" and publication.status == "published":
                return
            if generation is None or head.generation != generation:
                raise ApplicationError(
                    "PUBLICATION_CONFLICT", "目标代次已改变，禁止覆盖后续发布", 409
                )
            if tool == "rollback" and (
                head.current_publication_id != publication.id
                or publication.retained_until is None
                or utcnow() >= publication.retained_until
            ):
                raise ApplicationError("ROLLBACK_UNAVAILABLE", "保留资源或发布头不再允许恢复", 409)
            busy = session.scalar(
                select(m.PublicationRecord.id)
                .where(
                    m.PublicationRecord.project_id == run.project_id,
                    m.PublicationRecord.target_key == key,
                    m.PublicationRecord.id != publication.id,
                    m.PublicationRecord.status.in_(["publishing", "reconciling"]),
                )
                .limit(1)
            )
            if busy:
                raise ApplicationError("PUBLICATION_CONFLICT", "该目标仍有尚未对账的发布", 409)
            if tool == "publish":
                publication.status = "publishing"
            spec = {
                **spec,
                "expected_generation": generation,
                "target_key": key,
                "retained_resource": publication.retained_resource
                if tool == "rollback"
                else spec["rollback_plan"]["retained_resource"],
            }
        # Unknown submissions only query the durable command marker; no second swap.
        observation = (
            self.adapter.query(spec) if spec.get("recovering") else self.adapter.execute(spec)
        )
        if (
            observation is None
            or not observation.confirmed
            or observation.command_id != command_id
            or observation.generation != generation + 1
        ):
            with self.database.session_factory.begin() as session:
                row = required_record(session, m.PublicationRecord, publication.id)
                if tool == "publish":
                    row.status = "reconciling"
            raise ApplicationError("EXECUTION_UNKNOWN", "目标变更结果尚未得到权威核验", 409)
        if tool == "publish" and observation.retained_resource != spec["retained_resource"]:
            raise ApplicationError("EXECUTION_UNKNOWN", "保留资源证据与冻结范围不一致", 409)
        with self.database.session_factory.begin() as session:
            session.scalars(
                select(m.Project).where(m.Project.id == run.project_id).with_for_update()
            ).one()
            head = session.get(m.TargetHead, (run.project_id, key), with_for_update=True)
            if head is None or head.generation != generation:
                raise ApplicationError(
                    "PUBLICATION_CONFLICT", "外部变更完成但本地代次冲突，必须对账", 409
                )
            row = required_record(session, m.PublicationRecord, publication.id)
            head.generation += 1
            head.revision += 1
            head.current_publication_id = row.id if tool == "publish" else None
            if tool == "publish":
                row.status, row.published_generation = "published", observation.generation
                row.retained_resource = observation.retained_resource
                row.retained_until = utcnow() + timedelta(
                    milliseconds=int(spec["rollback_plan"]["retention_ms"])
                )
                row.revision += 1
            for operation in session.scalars(
                select(m.RunOperation).where(m.RunOperation.command_id == command_id)
            ):
                operation.status = "succeeded"
                operation.revision += 1
            self.ledger.append(
                session,
                run.project_id,
                None,
                f"target_{tool}_confirmed",
                "publication",
                row.id,
                {"command_id": command_id, "generation": head.generation},
            )

    def _clean_shadow(self, spec: dict[str, Any]) -> None:
        """A newly approved rollback may clean only frozen pre-publication resources."""
        if self.adapter is None:
            raise unavailable("经验证的影子资源清理适配器")
        run_id, command_id = UUID(spec["run_id"]), UUID(spec["command_id"])
        names = {
            spec["rollback_plan"]["shadow_resource"],
            spec["quality_contract"]["error_resource"]["resource_name"],
        }
        scopes = [scope for scope in spec["resource_scopes"] if scope["resource_name"] in names]
        if {scope["resource_name"] for scope in scopes} != names:
            raise ApplicationError("FORBIDDEN", "清理资源不在新审批冻结范围内", 403)
        with self.database.session_factory() as session:
            run = required_record(session, m.ExecutionRun, run_id)
            if run.status not in {"failed", "cancelled", "succeeded"}:
                raise ApplicationError("STATE_CONFLICT", "未知或活动搬运不能清理写入资源", 409)
            previous = session.scalar(
                select(m.RunOperation).where(
                    m.RunOperation.command_id == command_id, m.RunOperation.status == "succeeded"
                )
            )
            if previous:
                return
        bounded = {**spec, "operation_mode": "cleanup_shadow", "cleanup_resources": scopes}
        observation = (
            self.adapter.query(bounded) if spec.get("recovering") else self.adapter.execute(bounded)
        )
        if observation is None or not observation.confirmed or observation.command_id != command_id:
            raise ApplicationError("EXECUTION_UNKNOWN", "影子清理结果未得到权威核验", 409)
        with self.database.session_factory.begin() as session:
            session.scalars(
                select(m.Project).where(m.Project.id == run.project_id).with_for_update()
            ).one()
            operation = session.scalars(
                select(m.RunOperation).where(m.RunOperation.command_id == command_id)
            ).one()
            operation.status = "succeeded"
            operation.revision += 1
            self.ledger.append(
                session,
                run.project_id,
                None,
                "shadow_cleanup_confirmed",
                "run_operation",
                operation.id,
                {"command_id": command_id, "resources": sorted(names)},
            )
