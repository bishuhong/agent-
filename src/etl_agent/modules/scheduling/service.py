"""Project-scoped schedule maintenance and transactional due preparations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
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
from etl_agent.modules.scheduling.domain import (
    ScheduleDefinition,
    evaluate_due,
    next_trigger,
    validate_schedule,
)
from etl_agent.shared.context import ActorContext, utcnow
from etl_agent.shared.errors import ApplicationError, invalid, unavailable

PrepareCallback = Callable[[Session, m.ScheduleDefinition], m.Preparation | UUID | dict[str, Any]]


def _definition(row: m.ScheduleDefinition) -> ScheduleDefinition:
    kind = row.kind
    if kind not in {"once", "recurring"}:
        raise invalid("持久化调度类型无效")
    from typing import Literal, cast

    return ScheduleDefinition(
        str(row.pipeline_id),
        str(row.version_id),
        cast(Literal["once", "recurring"], kind),
        row.timezone,
        row.run_at,
        row.cron_expression,
        row.enabled,
    )


class ScheduleService:
    def __init__(self, settings: Settings, ledger: EvidenceLedger) -> None:
        self.settings = settings
        self.ledger = ledger

    def save(
        self,
        session: Session,
        actor: ActorContext,
        payload: c.ScheduleDefinitionRequest,
        *,
        schedule_id: UUID | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        pipeline = project_entity(
            session, m.Pipeline, payload.pipeline_id, actor, "manage_schedule", lock=True
        )
        version = project_entity(
            session, m.PipelineVersion, payload.version_id, actor, "manage_schedule"
        )
        if version.project_id != pipeline.project_id or version.pipeline_id != pipeline.id:
            raise invalid("调度版本必须属于该流水线")
        if not version.is_immutable:
            raise ApplicationError("STATE_CONFLICT", "调度仅能绑定明确的冻结版本", 409)
        now = utcnow()
        try:
            definition = validate_schedule(payload.model_dump(mode="json"), now)
            upcoming = next_trigger(definition, now)
        except ValueError as error:
            raise invalid("调度时刻、时区或Cron表达式无效") from error
        if schedule_id is None:
            row = m.ScheduleDefinition(
                id=uuid4(),
                project_id=pipeline.project_id,
                pipeline_id=pipeline.id,
                version_id=version.id,
                kind=definition.kind,
                timezone=definition.timezone,
                run_at=definition.run_at,
                cron_expression=definition.cron_expression,
                enabled=definition.enabled,
                next_trigger_at=upcoming,
                created_at=now,
                updated_at=now,
                revision=1,
            )
            session.add(row)
            event = "schedule.created"
        else:
            row = project_entity(
                session, m.ScheduleDefinition, schedule_id, actor, "manage_schedule", lock=True
            )
            if row.project_id != pipeline.project_id:
                raise invalid("调度不能移到其他项目")
            if expected_revision is None:
                raise ApplicationError("REVISION_MISMATCH", "修改调度必须提供实体版本", 412)
            compare_revision(row.revision, expected_revision)
            for name in ("kind", "timezone", "run_at", "cron_expression", "enabled"):
                setattr(row, name, getattr(definition, name))
            row.pipeline_id, row.version_id = pipeline.id, version.id
            row.next_trigger_at = upcoming
            row.revision += 1
            row.updated_at = now
            event = "schedule.updated"
        session.flush()
        self.ledger.append(
            session,
            row.project_id,
            actor,
            event,
            "schedule",
            row.id,
            {"version_id": str(row.version_id), "enabled": row.enabled},
        )
        return public_record(row, c.ScheduleDefinitionResponse)

    def get(self, session: Session, actor: ActorContext, schedule_id: UUID) -> dict[str, Any]:
        return public_record(
            project_entity(session, m.ScheduleDefinition, schedule_id, actor),
            c.ScheduleDefinitionResponse,
        )

    def on_due(
        self,
        session: Session,
        schedule_id: UUID,
        trigger_at: datetime,
        prepare_callback: PrepareCallback | None = None,
    ) -> dict[str, Any]:
        """Caller must commit this record and callback-created preparation together.

        Pipeline lock serializes different schedules sharing the same pipeline.
        No Celery execution, Capability, or engine API is invoked here.
        """
        snapshot = session.get(m.ScheduleDefinition, schedule_id)
        if snapshot is None:
            raise ApplicationError("RESOURCE_NOT_FOUND", "调度不存在", 404)
        session.scalar(
            select(m.Project).where(m.Project.id == snapshot.project_id).with_for_update()
        )
        row = session.scalar(
            select(m.ScheduleDefinition)
            .where(m.ScheduleDefinition.id == schedule_id)
            .with_for_update()
        )
        if row is None:
            raise ApplicationError("RESOURCE_NOT_FOUND", "调度不存在", 404)
        session.scalar(select(m.Pipeline).where(m.Pipeline.id == row.pipeline_id).with_for_update())
        previous = session.scalar(
            select(m.ScheduleTriggerRecord).where(
                m.ScheduleTriggerRecord.schedule_id == schedule_id,
                m.ScheduleTriggerRecord.trigger_at == trigger_at,
            )
        )
        if previous is not None:
            return {
                "schedule_id": str(schedule_id),
                "trigger_at": trigger_at,
                "result": previous.result,
                "preparation_id": str(previous.preparation_id) if previous.preparation_id else None,
                "reason": "DUPLICATE_TRIGGER",
            }
        now = utcnow()
        active_statuses = {
            "queued",
            "authorizing",
            "running",
            "cancel_requested",
            "paused",
            "reconciling",
        }
        open_intent = session.scalar(
            select(m.ScheduleTriggerRecord.id)
            .join(
                m.ScheduleDefinition, m.ScheduleDefinition.id == m.ScheduleTriggerRecord.schedule_id
            )
            .join(m.Preparation, m.Preparation.id == m.ScheduleTriggerRecord.preparation_id)
            .outerjoin(m.ExecutionRun, m.ExecutionRun.preparation_id == m.Preparation.id)
            .where(
                m.ScheduleDefinition.pipeline_id == row.pipeline_id,
                m.ScheduleTriggerRecord.result == "created",
                or_(
                    m.ExecutionRun.status.in_(active_statuses),
                    and_(m.ExecutionRun.id.is_(None), m.Preparation.expires_at > now),
                ),
            )
            .limit(1)
        )
        try:
            decision = evaluate_due(
                _definition(row),
                trigger_at,
                now,
                has_open_intent=open_intent is not None,
                already_triggered=False,
            )
        except ValueError as error:
            raise invalid("调度触发事实无效") from error
        # Stale messages from an edited schedule cannot prepare a different time.
        if decision.action == "prepare" and row.next_trigger_at != decision.trigger_at:
            result, reason = "skipped", "TRIGGER_MISMATCH"
        else:
            result = "created" if decision.action == "prepare" else "skipped"
            reason = decision.reason
        preparation_id = None
        if result == "created":
            if prepare_callback is None:
                raise unavailable("调度准备单创建端口")
            created = prepare_callback(session, row)
            if isinstance(created, m.Preparation):
                preparation_id = created.id
            elif isinstance(created, UUID):
                preparation_id = created
            else:
                body = created.get("preparation", created)
                preparation_id = UUID(str(body.get("id")))
            preparation = session.get(m.Preparation, preparation_id)
            if (
                preparation is None
                or preparation.project_id != row.project_id
                or preparation.version_id != row.version_id
                or preparation.purpose != "execute_etl"
            ):
                raise ApplicationError("STATE_CONFLICT", "调度回调返回了不匹配的准备单", 409)
        trigger = m.ScheduleTriggerRecord(
            id=uuid4(),
            schedule_id=row.id,
            trigger_at=decision.trigger_at,
            preparation_id=preparation_id,
            result=result,
            created_at=now,
            updated_at=now,
            revision=1,
        )
        session.add(trigger)
        # Only advance the recorded due point. An arbitrary stale message cannot
        # erase the current future trigger; missed points are not replayed.
        if row.next_trigger_at == decision.trigger_at and decision.trigger_at <= now:
            row.next_trigger_at = next_trigger(_definition(row), now)
            row.revision += 1
            row.updated_at = now
        session.flush()
        self.ledger.append(
            session,
            row.project_id,
            None,
            "schedule.triggered",
            "schedule",
            row.id,
            {
                "result": result,
                "reason": reason,
                "trigger_at": decision.trigger_at,
                "preparation_id": str(preparation_id) if preparation_id else None,
            },
        )
        return {
            "schedule_id": str(row.id),
            "trigger_at": decision.trigger_at,
            "result": result,
            "preparation_id": str(preparation_id) if preparation_id else None,
            "reason": reason,
        }
