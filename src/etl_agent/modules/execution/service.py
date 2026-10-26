"""Run projections and managed command execution. Unknown I/O is never retried blindly."""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import func, or_, select
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
from etl_agent.modules.harness.service import ReplayGuard
from etl_agent.modules.identity_access.service import ProjectService, actor_from_user
from etl_agent.modules.quality.service import QualityService
from etl_agent.modules.supervision.service import SupervisionService
from etl_agent.shared.context import ActorContext, WorkerContext
from etl_agent.shared.errors import ApplicationError, unavailable


@dataclass(frozen=True)
class EngineObservation:
    job_id: str
    status: str
    metrics: dict[str, Any]


class EngineAdapter(Protocol):
    def submit(self, spec: dict[str, Any]) -> EngineObservation: ...
    def query(self, job_id: str) -> EngineObservation | None: ...
    def cancel(self, job_id: str) -> None: ...
    def pause(self, job_id: str) -> None: ...
    def resume(self, job_id: str) -> None: ...


class ExecutionService:
    def __init__(
        self,
        settings: Settings,
        ledger: EvidenceLedger,
        quality: QualityService,
        supervision: SupervisionService,
        cursors: Any,
    ):
        self.settings, self.ledger, self.quality, self.supervision, self.cursors = (
            settings,
            ledger,
            quality,
            supervision,
            cursors,
        )
        self.projects = ProjectService(ledger, settings)

    def get(self, session: Session, actor: ActorContext, run_id: UUID) -> dict:
        run = project_entity(session, m.ExecutionRun, run_id, actor)
        publication = session.scalar(
            select(m.PublicationRecord).where(m.PublicationRecord.execution_run_id == run.id)
        )
        rollback = session.scalar(
            select(m.RunOperation)
            .where(
                m.RunOperation.execution_run_id == run.id,
                m.RunOperation.kind.in_(["rollback", "cleanup"]),
            )
            .order_by(m.RunOperation.created_at.desc())
            .limit(1)
        )
        actions = self.projects.actions(
            actor, run.project_id, ["cancel", "pause", "resume", "rollback", "rerun"]
        )
        stages = {
            "cancel": {"queued", "authorizing", "running", "paused", "reconciling"},
            "pause": {"running"},
            "resume": {"paused"},
            "rollback": {"succeeded", "failed", "cancelled", "reconciling"},
            "rerun": {"failed", "cancelled", "authorization_expired"},
        }
        for action in actions:
            if run.status not in stages[action["action"]]:
                action.update(
                    allowed=False,
                    reason_code="STATE_CONFLICT",
                    reason_message="当前运行阶段不允许此操作",
                )
            if (
                action["action"] in {"pause", "resume"}
                and action["action"] not in self.settings.seatunnel_capabilities
            ):
                action.update(
                    allowed=False,
                    reason_code="DEPENDENCY_UNAVAILABLE",
                    reason_message="引擎能力尚未验证",
                )
        return {
            "run": public_record(
                run,
                c.ExecutionRunResponseBody,
                metrics={**c.RuntimeMetrics().model_dump(mode="json"), **run.metrics},
            ),
            "publication": public_record(publication, c.PublicationResponse)
            if publication
            else None,
            "latest_rollback": public_record(rollback, c.RunOperationResponse)
            if rollback
            else None,
            "quality_report": self.quality.get(session, actor, run.id),
            "supervision": self.supervision.get(session, actor, run.id),
            "available_actions": actions,
        }

    def logs(
        self,
        session: Session,
        actor: ActorContext,
        run_id: UUID,
        query: c.CursorQuery,
        request: Any,
    ) -> dict:
        from etl_agent.api.dependencies import paginate

        run = project_entity(session, m.ExecutionRun, run_id, actor)
        commands = select(m.ManagedCommand.id).where(m.ManagedCommand.execution_run_id == run.id)
        command_ids = [str(value) for value in session.scalars(commands)]
        stmt = select(m.AuditEvent).where(
            or_(m.AuditEvent.resource_id == str(run.id), m.AuditEvent.resource_id.in_(command_ids))
        )

        class QueryRuntime:
            cursors = self.cursors

        page = paginate(
            session,
            actor,
            run.project_id,
            m.AuditEvent,
            c.AuditEventResponse,
            query,
            QueryRuntime(),
            request,
            statement=stmt,
            filters={"run_id": str(run.id)},
        )
        page["items"] = [
            {
                "execution_run_id": str(run.id),
                "occurred_at": row["created_at"],
                "level": "error" if "failed" in row["event_type"] else "info",
                "message": "控制面事件：" + row["event_type"],
                "sequence_no": row["sequence_no"],
            }
            for row in page["items"]
        ]
        return page

    def diagnose(self, session: Session, actor: ActorContext, run_id: UUID) -> dict | None:
        run = project_entity(session, m.ExecutionRun, run_id, actor)
        row = session.scalar(
            select(m.DiagnosticReport)
            .where(m.DiagnosticReport.execution_run_id == run.id)
            .order_by(m.DiagnosticReport.created_at.desc())
            .limit(1)
        )
        return public_record(row, c.DiagnosticReportResponse) if row else None

    def record_failure(
        self, session: Session, run: m.ExecutionRun, code: str, event_id: UUID
    ) -> None:
        explanations = {
            "DEPENDENCY_UNAVAILABLE": "受管执行依赖未配置或不可用",
            "FINGERPRINT_CHANGED": "执行所依据的Schema或授权事实已经变化",
            "CAPABILITY_EXPIRED": "命令未在首次消费窗口内获得有效授权",
            "BUDGET_EXCEEDED": "实际观测指标超过冻结运行预算",
        }
        finding = {
            "certainty": "confirmed",
            "explanation": explanations.get(code, "受管命令失败，详细原因需结合安全证据核实"),
            "evidence": [
                {
                    "source_type": "audit",
                    "resource_id": str(event_id),
                    "summary": "执行失败安全事件",
                }
            ],
        }
        session.add(
            m.DiagnosticReport(
                project_id=run.project_id,
                execution_run_id=run.id,
                findings=[finding],
                suggestions=["核验依赖及冻结事实；修复后使用新准备和新授权安全重跑"],
            )
        )


class ManagedExecutor:
    def _budget_stop(self, session: Session, run: m.ExecutionRun) -> None:
        parent = session.scalars(
            select(m.ManagedCommand).where(
                m.ManagedCommand.execution_run_id == run.id,
                m.ManagedCommand.tool.in_(["execute_etl", "run_l2_benchmark"]),
            )
        ).one()
        auth = required_record(session, m.CapabilityAuthorization, parent.capability_id)
        user = session.get(m.User, auth.subject_id)
        if user is None or user.status != "enabled":
            raise unavailable("预算停止命令的有效授权主体")
        existing = session.scalar(
            select(m.ManagedCommand.id).where(
                m.ManagedCommand.execution_run_id == run.id, m.ManagedCommand.tool == "cancel"
            )
        )
        if existing:
            return
        prep = required_record(session, m.Preparation, run.preparation_id)
        command = self.replay.authorizer.create(
            session,
            actor_from_user(session, user),
            prep,
            run,
            "cancel",
            f"budget-stop:{run.id}",
            parent_command_id=parent.id,
        )
        session.add(
            m.RunOperation(
                project_id=run.project_id,
                execution_run_id=run.id,
                kind="cancel",
                command_id=command.id,
                requested_by=user.id,
                status="requested",
            )
        )
        run.status = "cancel_requested"

    def __init__(
        self,
        database: Any,
        replay: ReplayGuard,
        engine: EngineAdapter,
        execution: ExecutionService,
        ledger: EvidenceLedger,
        target: Any = None,
    ):
        self.database, self.replay, self.engine, self.execution, self.ledger, self.target = (
            database,
            replay,
            engine,
            execution,
            ledger,
            target,
        )

    def execute(self, command_id: UUID, worker_id: str) -> None:
        observation = None
        try:
            with self.database.session_factory.begin() as session:
                claimed = self.replay.consume(
                    session, command_id, WorkerContext(worker_id), recover=True
                )
                if claimed.completed:
                    return
                command = session.get(m.ManagedCommand, command_id)
                run = session.get(m.ExecutionRun, command.execution_run_id)
                prep = session.get(m.Preparation, command.preparation_id)
                version = session.get(m.PipelineVersion, prep.version_id)
                auth = session.get(m.CapabilityAuthorization, command.capability_id)
                spec = {
                    "run_id": str(run.id),
                    "command_id": str(command_id),
                    "tool": command.tool,
                    "hocon": version.hocon_text,
                    "artifact_digest": version.artifact_digest,
                    "preparation_id": str(prep.id),
                    "resource_scopes": auth.resource_scopes,
                    "input_bindings": prep.input_bindings,
                    "rollback_plan": prep.rollback_plan,
                    "quality_contract": prep.quality_contract,
                    "supervision_contract": prep.supervision_contract,
                    "engine_job_id": run.engine_job_id,
                    "recovering": claimed.recovering,
                }
                initial = session.scalar(
                    select(m.ManagedCommand)
                    .where(
                        m.ManagedCommand.execution_run_id == run.id,
                        m.ManagedCommand.tool.in_(["execute_etl", "run_l2_benchmark"]),
                    )
                    .limit(1)
                )
                spec["initial_consumed"] = bool(
                    initial and session.get(m.CapabilityConsumption, initial.capability_id)
                )
            # All external calls are outside the authorization/claim transaction.
            observation = None
            if claimed.tool in {"execute_etl", "run_l2_benchmark"}:
                if claimed.recovering:
                    if not spec["engine_job_id"]:
                        lookup = getattr(self.engine, "lookup", None)
                        observation = lookup(spec["command_id"]) if lookup else None
                    else:
                        observation = self.engine.query(spec["engine_job_id"])
                    if observation is None:
                        raise ApplicationError(
                            "EXECUTION_UNKNOWN", "已有作业暂不可定位，需要对账", 409
                        )
                else:
                    observation = self.engine.submit(spec)
            elif claimed.tool in {"cancel", "pause", "resume"}:
                if not spec["engine_job_id"]:
                    # Cancelling a not-yet-consumed initial command is handled as a
                    # local durable cancellation; never invent an engine job.
                    if claimed.tool != "cancel":
                        raise ApplicationError("STATE_CONFLICT", "运行尚无可控制的引擎作业", 409)
                    if spec["initial_consumed"]:
                        raise ApplicationError(
                            "EXECUTION_UNKNOWN",
                            "原搬运已消费授权但作业标识未知，必须先对账再确认取消",
                            409,
                        )
                else:
                    if not claimed.recovering:
                        getattr(self.engine, claimed.tool)(spec["engine_job_id"])
                    observation = self.engine.query(spec["engine_job_id"])
                    if observation is None:
                        raise ApplicationError(
                            "EXECUTION_UNKNOWN", "控制指令已受理但作业状态尚不可核验", 409
                        )
            else:
                if self.target is None:
                    raise unavailable("经过版本验证的原子发布/恢复适配器")
                self.target.execute(spec)
            with self.database.session_factory.begin() as session:
                project_id = session.get(m.ManagedCommand, command_id).project_id
                session.scalars(
                    select(m.Project).where(m.Project.id == project_id).with_for_update()
                ).one()
                command = session.get(m.ManagedCommand, command_id, with_for_update=True)
                run = session.get(m.ExecutionRun, claimed.execution_run_id, with_for_update=True)
                now = session.execute(select(func.clock_timestamp())).scalar_one()
                if observation:
                    run.engine_job_id = observation.job_id
                    run.metrics = c.RuntimeMetrics.model_validate(observation.metrics).model_dump(
                        mode="json"
                    )
                    for name in (
                        "input_records",
                        "output_records",
                        "error_records",
                        "bytes_processed",
                    ):
                        value = run.metrics.get(name)
                        setattr(run, name, int(value) if value is not None else None)
                    run.status = (
                        "cancel_requested"
                        if run.status == "cancel_requested" and observation.status == "running"
                        else observation.status
                    )
                    if run.started_at is None:
                        run.started_at = now
                    if observation.status in {"succeeded", "failed", "cancelled"}:
                        run.finished_at = now
                elif claimed.tool == "cancel":
                    run.status, run.finished_at = "cancelled", now
                controlled = {"cancel": "cancelled", "pause": "paused", "resume": "running"}
                complete = (
                    run.status == controlled[claimed.tool]
                    if claimed.tool in controlled
                    else run.status in {"succeeded", "failed", "cancelled"}
                    if claimed.tool in {"execute_etl", "run_l2_benchmark"}
                    else True
                )
                command.status = "completed" if complete else "started"
                command.lease_until = None
                run.revision += 1
                operations = session.scalars(
                    select(m.RunOperation).where(m.RunOperation.command_id == command_id)
                ).all()
                for operation in operations:
                    # Receipt is not confirmation; an engine still running keeps
                    # cancel/pause/resume requested until observation proves it.
                    confirmed = (
                        (claimed.tool == "cancel" and run.status == "cancelled")
                        or (claimed.tool == "pause" and run.status == "paused")
                        or (claimed.tool == "resume" and run.status == "running")
                    )
                    if claimed.tool in {"rollback", "cleanup", "publish"}:
                        confirmed = True
                    operation.status = "succeeded" if confirmed else "running"
                    operation.revision += 1
                self.ledger.append(
                    session,
                    run.project_id,
                    None,
                    "command_observed",
                    "execution_run",
                    run.id,
                    {"command_id": command.id, "execution_status": run.status},
                )
                if observation and claimed.tool in {"execute_etl", "run_l2_benchmark"}:
                    self.execution.supervision.observe(
                        session, run.id, stop_callback=self._budget_stop
                    )
                    if run.status in {"succeeded", "failed", "cancelled"}:
                        report = session.scalar(
                            select(m.QualityReport.id)
                            .where(m.QualityReport.execution_run_id == run.id)
                            .limit(1)
                        )
                        if report is None:
                            self.execution.quality.record(session, run.id)
                        if self.target is not None:
                            self.target.plan(session, run)
        except ApplicationError as error:
            # Duplicate delivery cannot overwrite a live lease or a terminal run.
            if error.code in {"STATE_CONFLICT", "CAPABILITY_REPLAYED"}:
                raise
            with self.database.session_factory.begin() as session:
                before = session.get(m.ManagedCommand, command_id)
                if before is None:
                    raise
                session.scalars(
                    select(m.Project).where(m.Project.id == before.project_id).with_for_update()
                ).one()
                command = session.get(m.ManagedCommand, command_id, with_for_update=True)
                if command is None:
                    raise
                run = session.get(m.ExecutionRun, command.execution_run_id, with_for_update=True)
                uncertain = error.code == "EXECUTION_UNKNOWN" or observation is not None
                if observation is not None:
                    # Preserve the external correlation even if local quality or
                    # bookkeeping failed; recovery can query this exact job.
                    run.engine_job_id = observation.job_id
                command.status = (
                    "reconciling"
                    if uncertain
                    else "expired"
                    if error.code == "CAPABILITY_EXPIRED"
                    else "failed"
                )
                command.lease_until = None
                if command.tool in {"execute_etl", "run_l2_benchmark"}:
                    run.status = (
                        "reconciling"
                        if uncertain
                        else "authorization_expired"
                        if error.code == "CAPABILITY_EXPIRED"
                        else "failed"
                    )
                    run.revision += 1
                event = self.ledger.append(
                    session,
                    run.project_id,
                    None,
                    "command_failed",
                    "command",
                    command.id,
                    {"code": error.code},
                )
                self.execution.record_failure(session, run, error.code, event.id)
                for operation in session.scalars(
                    select(m.RunOperation).where(m.RunOperation.command_id == command_id)
                ):
                    operation.status = "reconciling" if uncertain else "failed"
                    operation.error = error.safe_payload()
            raise
