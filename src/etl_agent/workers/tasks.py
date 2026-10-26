"""At-least-once dispatch. The database command/consumption remains authoritative."""

from datetime import datetime
from functools import lru_cache
from uuid import UUID

from celery import Celery
from sqlalchemy import func, or_, select

from etl_agent.api.schemas import contracts as c
from etl_agent.bootstrap import BackendRuntime
from etl_agent.config import Settings
from etl_agent.infrastructure.persistence import models as m
from etl_agent.modules.identity_access.service import actor_from_user
from etl_agent.shared.errors import ApplicationError, unavailable

settings = Settings()
celery_app = Celery("etl_agent", broker=settings.redis_url.get_secret_value() or None)
celery_app.conf.update(
    broker_connection_timeout=3,
    broker_transport_options={
        "socket_connect_timeout": 3,
        "socket_timeout": 3,
        "visibility_timeout": 300,
    },
    task_publish_retry=False,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    beat_schedule={"dispatch-persisted-work": {"task": "etl_agent.scan", "schedule": 10.0}},
)


@lru_cache(maxsize=1)
def runtime() -> BackendRuntime:
    value = BackendRuntime(Settings())
    if value.database is None:
        raise unavailable("Worker PostgreSQL")
    return value


@celery_app.task(name="etl_agent.command", bind=True)
def execute_command(task, command_id: str) -> None:
    # Known business failures are persisted by the executor. No automatic retry
    # can bypass a consumed capability or blindly repeat an unknown submission.
    runtime().executor.execute(UUID(command_id), str(task.request.id))


@celery_app.task(name="etl_agent.asset")
def execute_asset(kind: str, resource_id: str) -> None:
    rt = runtime()
    if kind == "test":
        rt.assets.process_test(rt.required_database, UUID(resource_id))
    elif kind == "profile":
        rt.assets.process_profile(rt.required_database, UUID(resource_id))
    else:
        raise ValueError("Unsupported asset task")


@celery_app.task(name="etl_agent.generation")
def execute_generation(run_id: str) -> None:
    rt = runtime()
    rt.generation.process(UUID(run_id), rt.required_database.session_factory)


def scheduled_preparation(rt: BackendRuntime, session, schedule):
    """Use the explicitly frozen plan budget and the current target environment.

    Each occurrence creates new facts and pending approvals, never copies an
    earlier approval or queues an execution command.
    """
    event = session.scalar(
        select(m.AuditEvent)
        .where(
            m.AuditEvent.project_id == schedule.project_id,
            m.AuditEvent.resource_id == str(schedule.id),
            m.AuditEvent.event_type.in_(["schedule.created", "schedule.updated"]),
        )
        .order_by(m.AuditEvent.sequence_no.desc())
        .limit(1)
    )
    user = session.get(m.User, event.actor_id) if event and event.actor_id else None
    if user is None or user.status != "enabled":
        raise ApplicationError("FORBIDDEN", "调度维护主体已失效", 403)
    version = session.get(m.PipelineVersion, schedule.version_id)
    if version is None:
        raise ApplicationError("STATE_CONFLICT", "调度冻结版本不存在", 409)
    plan = c.EtlPlan.model_validate(version.etl_plan_json)
    profile = session.get(m.MetadataProfile, plan.target_profile_id)
    connection = (
        session.get(m.Connection, profile.connection_id)
        if profile and profile.connection_id
        else None
    )
    if connection is None:
        raise unavailable("调度受管目标环境")
    return rt.governance.prepare(
        session,
        actor_from_user(session, user),
        version.id,
        c.PrepareRequest(
            purpose=c.PreparationPurpose.execute_etl,
            environment=connection.environment,
            supervision_contract=plan.supervision_contract,
            execution_run_id=None,
            evaluation_binding=None,
        ),
    )["preparation"]["id"]


@celery_app.task(name="etl_agent.schedule")
def execute_schedule(schedule_id: str, trigger_at: str) -> None:
    rt = runtime()
    with rt.required_database.session_factory.begin() as session:
        rt.schedules.on_due(
            session,
            UUID(schedule_id),
            datetime.fromisoformat(trigger_at.replace("Z", "+00:00")),
            lambda unit, row: UUID(scheduled_preparation(rt, unit, row)),
        )


@celery_app.task(name="etl_agent.benchmark")
def execute_benchmark(run_id: str) -> None:
    rt = runtime()
    with rt.required_database.session_factory.begin() as session:
        run = session.get(m.BenchmarkRun, UUID(run_id))
        if run is None or run.status in {"succeeded", "failed"}:
            return
        event = session.scalar(
            select(m.AuditEvent)
            .where(
                m.AuditEvent.project_id == run.project_id,
                m.AuditEvent.resource_id == run_id,
                m.AuditEvent.actor_id.is_not(None),
            )
            .order_by(m.AuditEvent.sequence_no)
            .limit(1)
        )
        user = session.get(m.User, event.actor_id) if event else None
        if user is None or user.status != "enabled":
            raise ApplicationError("FORBIDDEN", "评测发起主体不可用", 403)
        actor = actor_from_user(session, user)
        if run.level == "L2":
            rt.benchmarks.aggregate_l2(session, actor, run.id)
        else:
            rt.benchmarks.run_deterministic(session, actor, run.id)


def dispatch_outbox(rt: BackendRuntime, send) -> int:
    """A crash after send/before commit causes redelivery, never message loss.

    Dispatch locks are short and bounded by the broker's transport timeouts.
    Workers independently claim and verify each durable command.
    """
    count = 0
    with rt.required_database.session_factory.begin() as session:
        rows = session.scalars(
            select(m.OutboxMessage)
            .where(
                m.OutboxMessage.delivery_status == "pending",
                m.OutboxMessage.next_attempt_at <= func.clock_timestamp(),
            )
            .order_by(m.OutboxMessage.created_at, m.OutboxMessage.id)
            .with_for_update(skip_locked=True)
            .limit(50)
        ).all()
        for row in rows:
            send("etl_agent.command", args=[str(row.command_id)])
            row.delivery_status = "sent"
            row.delivery_attempts += 1
            count += 1
    return count


@celery_app.task(name="etl_agent.scan")
def scan() -> None:
    rt = runtime()
    if not rt.settings.redis_url.get_secret_value():
        raise unavailable("Redis任务代理")
    dispatch_outbox(rt, celery_app.send_task)
    with rt.required_database.session_factory() as session:
        queries = [
            (
                "etl_agent.asset",
                "test",
                select(m.ConnectionTestResult.id).where(m.ConnectionTestResult.status == "running"),
            ),
            (
                "etl_agent.asset",
                "profile",
                select(m.ProfileTask.id).where(m.ProfileTask.status.in_(["queued", "running"])),
            ),
            (
                "etl_agent.generation",
                None,
                select(m.AgentRun.id).where(
                    m.AgentRun.status.in_(["queued", "running", "validating", "repairing"])
                ),
            ),
            (
                "etl_agent.benchmark",
                None,
                select(m.BenchmarkRun.id).where(m.BenchmarkRun.status.in_(["queued", "running"])),
            ),
            (
                "etl_agent.command",
                None,
                select(m.ManagedCommand.id).where(
                    m.ManagedCommand.status.in_(["pending", "claimed", "started", "reconciling"]),
                    or_(
                        m.ManagedCommand.lease_until.is_(None),
                        m.ManagedCommand.lease_until < func.clock_timestamp(),
                    ),
                ),
            ),
        ]
        pending = [
            (task, prefix, list(session.scalars(statement.limit(100))))
            for task, prefix, statement in queries
        ]
        due_schedules = [
            (str(row.id), row.next_trigger_at.isoformat())
            for row in session.scalars(
                select(m.ScheduleDefinition)
                .where(
                    m.ScheduleDefinition.enabled.is_(True),
                    m.ScheduleDefinition.next_trigger_at <= func.clock_timestamp(),
                )
                .limit(100)
            )
            if row.next_trigger_at is not None
        ]
    for task, prefix, ids in pending:
        for resource_id in ids:
            celery_app.send_task(task, args=([prefix] if prefix else []) + [str(resource_id)])
    for schedule_id, trigger_at in due_schedules:
        celery_app.send_task("etl_agent.schedule", args=[schedule_id, trigger_at])
