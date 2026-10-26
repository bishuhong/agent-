from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from etl_agent.config import Settings
from etl_agent.infrastructure.persistence import models as m
from etl_agent.modules.supervision.service import SupervisionService
from etl_agent.shared.errors import ApplicationError

NOW = datetime(2026, 10, 23, 8, tzinfo=UTC)


class SessionDouble:
    def __init__(self, run, prep, previous=None):
        self.results = iter([run, previous])
        self.prep = prep
        self.added = []

    def scalar(self, statement):
        return next(self.results)

    def get(self, model, key):
        return self.prep

    def add(self, row):
        self.added.append(row)

    def flush(self):
        pass


class LedgerDouble:
    def append(self, *args):
        return SimpleNamespace(id=uuid4())


def setup_service(monkeypatch, previous=None):
    monkeypatch.setattr("etl_agent.modules.supervision.service.utcnow", lambda: NOW)
    project = uuid4()
    prep = m.Preparation(
        id=uuid4(),
        project_id=project,
        supervision_contract={
            "max_input_records": "100",
            "max_output_bytes": "1000",
            "max_duration_ms": "1000",
            "max_amplification_ratio": "10",
            "max_rejection_ratio": "0.05",
            "on_exceed": "kill",
        },
    )
    run = m.ExecutionRun(
        id=uuid4(),
        project_id=project,
        preparation_id=prep.id,
        status="running",
        input_records=100,
        output_records=100,
        error_records=0,
        bytes_processed=500,
        started_at=NOW - timedelta(seconds=2),
        finished_at=None,
        metrics={"output_bytes": "1000", "duration_ms": "0"},
    )
    return SupervisionService(Settings(), LedgerDouble()), SessionDouble(run, prep, previous), run


def test_stop_is_requested_once_through_callback_and_not_claimed_completed(monkeypatch):
    service, session, run = setup_service(monkeypatch)
    calls = []
    response = service.observe(session, run.id, stop_callback=lambda db, row: calls.append(row.id))
    assert calls == [run.id]
    assert response["action_taken"] == "kill_requested"
    assert response["metrics"]["duration_ms"] == "2000"  # trusted clock defeats stale engine 0
    assert run.status == "running"  # does not assert an actual completed stop


def test_missing_stop_port_fails_without_writing_false_stop_snapshot(monkeypatch):
    service, session, run = setup_service(monkeypatch)
    with pytest.raises(ApplicationError) as failure:
        service.observe(session, run.id)
    assert failure.value.code == "DEPENDENCY_UNAVAILABLE"
    assert session.added == []


def test_identical_prior_stop_intent_does_not_issue_another_command(monkeypatch):
    previous = SimpleNamespace(action_taken="kill_requested", exceeded_metrics=["max_duration_ms"])
    service, session, run = setup_service(monkeypatch, previous)
    response = service.observe(session, run.id)
    assert response["action_taken"] == "kill_requested"
    assert len(session.added) == 1


def test_terminal_run_does_not_receive_a_stop_command(monkeypatch):
    service, session, run = setup_service(monkeypatch)
    run.status, run.finished_at = "succeeded", NOW
    response = service.observe(session, run.id)
    assert response["decision"] == "exceeded"
    assert response["action_taken"] == "none"


def test_queued_run_does_not_fabricate_elapsed_duration(monkeypatch):
    service, session, run = setup_service(monkeypatch)
    run.started_at, run.status = None, "queued"
    response = service.observe(session, run.id)
    assert response["metrics"]["duration_ms"] is None
    assert response["decision"] == "unknown"
