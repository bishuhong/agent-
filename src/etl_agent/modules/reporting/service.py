"""Read-only project aggregates and honest, bounded dependency health checks."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from etl_agent.api.schemas import contracts as c
from etl_agent.config import Settings
from etl_agent.infrastructure.persistence import models as m
from etl_agent.modules.identity_access.domain import assert_action
from etl_agent.shared.context import ActorContext, utcnow
from etl_agent.shared.errors import invalid

HealthCheck = Callable[[], bool]


class ReportingService:
    def __init__(self, settings: Settings, ledger: Any = None) -> None:
        self.settings = settings

    def overview(
        self,
        session: Session,
        actor: ActorContext,
        project_id: UUID,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
    ) -> dict[str, Any]:
        assert_action(actor, project_id, "view")
        end = period_end if period_end is not None else utcnow()
        start = period_start if period_start is not None else end - timedelta(days=7)
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise invalid("统计区间必须包含时区且起始时间早于结束时间")
        connection_count = (
            session.scalar(
                select(func.count())
                .select_from(m.Connection)
                .where(m.Connection.project_id == project_id)
            )
            or 0
        )
        pipeline_count = (
            session.scalar(
                select(func.count())
                .select_from(m.Pipeline)
                .where(m.Pipeline.project_id == project_id)
            )
            or 0
        )
        # Count independent pending slots, not preparations.
        pending = (
            session.scalar(
                select(func.count())
                .select_from(m.ApprovalRequest)
                .join(m.Preparation, m.Preparation.id == m.ApprovalRequest.preparation_id)
                .where(
                    m.ApprovalRequest.project_id == project_id,
                    m.ApprovalRequest.status == "pending",
                    m.Preparation.expires_at > utcnow(),
                )
            )
            or 0
        )
        statuses = session.execute(
            select(m.ExecutionRun.status, func.count())
            .where(
                m.ExecutionRun.project_id == project_id,
                m.ExecutionRun.finished_at >= start,
                m.ExecutionRun.finished_at < end,
                m.ExecutionRun.status.in_({"succeeded", "failed", "cancelled"}),
            )
            .group_by(m.ExecutionRun.status)
        ).all()
        counts: dict[str, int] = {status: count for status, count in statuses}
        denominator = sum(counts.values())
        ratio = Decimal(counts.get("succeeded", 0)) / denominator if denominator else None
        distribution = session.execute(
            select(m.Connection.connector_type, func.count())
            .where(m.Connection.project_id == project_id)
            .group_by(m.Connection.connector_type)
            .order_by(m.Connection.connector_type)
        ).all()
        return c.ProjectOverviewResponse(
            project_id=project_id,
            connection_count=str(connection_count),
            pipeline_count=str(pipeline_count),
            pending_approval_count=str(pending),
            period_start=start,
            period_end=end,
            run_success_rate=str(ratio) if ratio is not None else None,
            resource_distribution=[
                c.ResourceCount(connector_type=kind, resource_count=str(count))
                for kind, count in distribution
            ],
        ).model_dump(mode="json")


class HealthService:
    def __init__(self, settings: Settings, checks: Mapping[str, HealthCheck] | None = None) -> None:
        self.settings = settings
        self.checks = dict(checks or {})

    def check(self, session: Session, *, detailed: bool = False) -> dict[str, Any]:
        """Real checks have finite driver/socket timeouts and never echo endpoints.

        Unconfigured optional data-plane dependencies degrade readiness without
        hiding already available read-only control-plane capabilities.
        """
        components: list[c.ComponentHealth] = []
        try:
            db_ready = (
                bool(self.checks["database"]())
                if "database" in self.checks
                else session.scalar(text("SELECT 1")) == 1
            )
        except Exception:
            db_ready = False
        components.append(
            c.ComponentHealth(
                name="database",
                status="ready" if db_ready else "not_ready",
                message=None if db_ready else "控制数据库不可用",
            )
        )
        for name, configured in (
            ("redis", bool(self.settings.redis_url.get_secret_value())),
            ("engine", bool(self.settings.seatunnel_url)),
            ("secret_provider", bool(self.settings.secret_provider_url)),
        ):
            if name in self.checks:
                try:
                    ready = bool(self.checks[name]())
                except Exception:
                    ready = False
                status = "ready" if ready else "not_ready"
                message = None if ready else "依赖健康检查失败"
            elif not configured:
                status, message = "not_ready", "依赖尚未配置"
            elif name == "redis":
                try:
                    from redis import Redis

                    client = Redis.from_url(
                        self.settings.redis_url.get_secret_value(),
                        socket_connect_timeout=2,
                        socket_timeout=2,
                    )
                    try:
                        ready = client.ping() is True
                    finally:
                        client.close()
                    status, message = (
                        ("ready", None) if ready else ("not_ready", "依赖健康检查失败")
                    )
                except Exception:
                    status, message = "not_ready", "依赖健康检查失败"
            else:
                # Configuration is not evidence of a working engine/Secret API.
                # Use the adapter's verified readiness check rather than guessing
                # a URL path or treating an arbitrary HTTP 200 as capability.
                status, message = "unknown", "尚未接入经过验证的健康检查端口"
            components.append(
                c.ComponentHealth.model_validate(
                    {"name": name, "status": status, "message": message}
                )
            )
        status = (
            "not_ready"
            if not db_ready
            else "ready"
            if all(item.status == "ready" for item in components)
            else "degraded"
        )
        return c.HealthResult.model_validate(
            {"status": status, "checked_at": utcnow(), "components": components if detailed else []}
        ).model_dump(mode="json")
