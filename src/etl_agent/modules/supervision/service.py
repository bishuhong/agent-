"""Persist budget observations and request approved operations through a port."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from etl_agent.api.schemas import contracts as c
from etl_agent.config import Settings
from etl_agent.infrastructure.persistence import models as m
from etl_agent.infrastructure.persistence.access import project_entity, public_record
from etl_agent.modules.audit.service import EvidenceLedger
from etl_agent.modules.supervision.domain import evaluate_budget
from etl_agent.shared.context import ActorContext, utcnow
from etl_agent.shared.errors import ApplicationError, invalid, unavailable

StopCallback = Callable[[Session, m.ExecutionRun], Any]


class SupervisionService:
    def __init__(self, settings: Settings, ledger: EvidenceLedger) -> None:
        self.settings = settings
        self.ledger = ledger

    def observe(
        self,
        session: Session,
        run_id: UUID,
        *,
        stop_callback: StopCallback | None = None,
    ) -> dict[str, Any]:
        run = session.scalar(
            select(m.ExecutionRun).where(m.ExecutionRun.id == run_id).with_for_update()
        )
        if run is None:
            raise ApplicationError("RESOURCE_NOT_FOUND", "运行不存在", 404)
        preparation = session.get(m.Preparation, run.preparation_id)
        if preparation is None or preparation.project_id != run.project_id:
            raise ApplicationError("STATE_CONFLICT", "运行缺少冻结预算", 409)
        if not isinstance(preparation.supervision_contract, dict):
            raise invalid("冻结预算必须为对象")
        now = utcnow()
        raw = dict(run.metrics) if isinstance(run.metrics, Mapping) else {}
        for name in ("input_records", "output_records", "error_records", "bytes_processed"):
            raw[name] = str(getattr(run, name)) if getattr(run, name) is not None else None
        # A queued job has no invented start time. Duration uses a trusted clock,
        # never a user-controlled page timer or a stale engine elapsed counter.
        if run.started_at is not None:
            finish = run.finished_at if run.finished_at is not None else now
            if finish < run.started_at:
                raise invalid("运行结束时间早于开始时间")
            delta = finish - run.started_at
            raw["duration_ms"] = str(
                (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000
            )
        else:
            raw["duration_ms"] = None
        raw["observed_at"] = now.isoformat().replace("+00:00", "Z")
        try:
            decision = evaluate_budget(preparation.supervision_contract, raw)
            metrics = c.RuntimeMetrics.model_validate(raw).model_dump(mode="json")
        except (ValueError, TypeError) as error:
            raise invalid("预算或实际运行指标无效") from error
        previous = session.scalar(
            select(m.RuntimeSupervisionSnapshot)
            .where(m.RuntimeSupervisionSnapshot.execution_run_id == run.id)
            .order_by(
                m.RuntimeSupervisionSnapshot.created_at.desc(),
                m.RuntimeSupervisionSnapshot.id.desc(),
            )
            .limit(1)
        )
        action = decision.action_taken
        repeated_stop = (
            previous is not None
            and previous.action_taken == "kill_requested"
            and previous.exceeded_metrics == list(decision.exceeded_metrics)
        )
        if (
            action == "kill_requested"
            and run.status
            not in {
                "succeeded",
                "failed",
                "cancelled",
                "authorization_expired",
                "cancel_requested",
            }
            and not repeated_stop
        ):
            if stop_callback is None:
                # Raising rolls back this UoW; no snapshot falsely claims that a
                # stop was requested. Caller records/retries operational failure.
                raise unavailable("受管预算停止命令端口")
            stop_callback(session, run)
        elif action == "kill_requested" and not repeated_stop:
            action = "none"  # Terminal jobs need no new stop command.
        snapshot = m.RuntimeSupervisionSnapshot(
            id=uuid4(),
            project_id=run.project_id,
            execution_run_id=run.id,
            metrics_json=metrics,
            decision=decision.decision,
            action_taken=action,
            exceeded_metrics=list(decision.exceeded_metrics),
            created_at=now,
            updated_at=now,
            revision=1,
        )
        session.add(snapshot)
        session.flush()
        self.ledger.append(
            session,
            run.project_id,
            None,
            "supervision.observed",
            "supervision_snapshot",
            snapshot.id,
            {
                "decision": decision.decision,
                "action_taken": action,
                "exceeded_metrics": list(decision.exceeded_metrics),
            },
        )
        return public_record(snapshot, c.SupervisionResponse, metrics=metrics)

    def get(self, session: Session, actor: ActorContext, run_id: UUID) -> dict[str, Any] | None:
        run = project_entity(session, m.ExecutionRun, run_id, actor)
        snapshot = session.scalar(
            select(m.RuntimeSupervisionSnapshot)
            .where(
                m.RuntimeSupervisionSnapshot.project_id == run.project_id,
                m.RuntimeSupervisionSnapshot.execution_run_id == run.id,
            )
            .order_by(
                m.RuntimeSupervisionSnapshot.created_at.desc(),
                m.RuntimeSupervisionSnapshot.id.desc(),
            )
            .limit(1)
        )
        return (
            public_record(snapshot, c.SupervisionResponse, metrics=snapshot.metrics_json)
            if snapshot is not None
            else None
        )
