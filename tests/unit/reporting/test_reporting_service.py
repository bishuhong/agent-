from datetime import UTC, datetime
from uuid import uuid4

import pytest

from etl_agent.config import Settings
from etl_agent.modules.reporting.service import HealthService, ReportingService
from etl_agent.shared.context import ActorContext
from etl_agent.shared.errors import ApplicationError


class RowsDouble:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class SessionDouble:
    def __init__(self, statuses=()):
        self.scalar_values = iter([2, 3, 2])
        self.executed_values = iter([statuses, [("postgresql", 2)]])

    def scalar(self, statement):
        return next(self.scalar_values)

    def execute(self, statement):
        return RowsDouble(next(self.executed_values))


def test_overview_success_rate_uses_only_confirmed_terminal_statuses_and_slots():
    project = uuid4()
    actor = ActorContext(uuid4(), uuid4(), {project: frozenset({"auditor"})})
    response = ReportingService(Settings()).overview(
        SessionDouble([("succeeded", 1), ("failed", 1), ("cancelled", 2)]),
        actor,
        project,
        period_end=datetime(2026, 10, 23, 8, tzinfo=UTC),
    )
    assert response["run_success_rate"] == "0.25"
    assert response["pending_approval_count"] == "2"
    assert response["connection_count"] == "2" and response["pipeline_count"] == "3"


def test_overview_zero_denominator_is_unknown_and_unauthorized_is_rejected():
    project = uuid4()
    actor = ActorContext(uuid4(), uuid4(), {project: frozenset({"maker"})})
    response = ReportingService(Settings()).overview(SessionDouble(), actor, project)
    assert response["run_success_rate"] is None
    with pytest.raises(ApplicationError):
        ReportingService(Settings()).overview(
            SessionDouble(), ActorContext(uuid4(), uuid4()), project
        )


def test_health_has_real_missing_dependency_states_without_endpoint_disclosure():
    service = HealthService(
        Settings(seatunnel_url="https://secret-internal.example"), checks={"database": lambda: True}
    )
    response = service.check(None, detailed=True)
    statuses = {component["name"]: component["status"] for component in response["components"]}
    assert response["status"] == "degraded"
    assert statuses == {
        "database": "ready",
        "redis": "not_ready",
        "engine": "unknown",
        "secret_provider": "not_ready",
    }
    assert "secret-internal.example" not in repr(response)
    assert service.check(None, detailed=False)["components"] == []


def test_database_failure_overrides_optional_readiness_and_callback_errors_are_safe():
    def broken():
        raise RuntimeError("postgres://user:password@host")

    response = HealthService(Settings(), checks={"database": broken}).check(None, detailed=True)
    assert response["status"] == "not_ready"
    assert "password" not in repr(response)


def test_verified_ports_are_checked_instead_of_assuming_config_means_success():
    response = HealthService(
        Settings(),
        checks={name: lambda: True for name in ("database", "redis", "engine", "secret_provider")},
    ).check(None, detailed=True)
    assert response["status"] == "ready"
