from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from etl_agent.api.schemas import contracts as c
from etl_agent.config import Settings
from etl_agent.infrastructure.persistence import models as m
from etl_agent.modules.scheduling.service import ScheduleService
from etl_agent.shared.context import ActorContext
from etl_agent.shared.errors import ApplicationError

NOW = datetime(2026, 10, 23, 8, tzinfo=UTC)


class SessionDouble:
    def __init__(self, scalars, gets=None):
        self.values = iter(scalars)
        self.gets = gets or {}
        self.added = []

    def scalar(self, statement):
        return next(self.values)

    def get(self, model, key):
        return self.gets.get(key)

    def add(self, value):
        self.added.append(value)

    def flush(self):
        pass


class LedgerDouble:
    def __init__(self):
        self.events = []

    def append(self, *args):
        self.events.append(args)
        return SimpleNamespace(id=uuid4())


def objects():
    project, pipeline_id, version_id = uuid4(), uuid4(), uuid4()
    actor = ActorContext(uuid4(), uuid4(), {project: frozenset({"operator"})})
    pipeline = m.Pipeline(id=pipeline_id, project_id=project)
    version = m.PipelineVersion(
        id=version_id, project_id=project, pipeline_id=pipeline_id, is_immutable=True
    )
    schedule = m.ScheduleDefinition(
        id=uuid4(),
        project_id=project,
        pipeline_id=pipeline_id,
        version_id=version_id,
        kind="once",
        timezone="Asia/Shanghai",
        run_at=NOW,
        cron_expression=None,
        enabled=True,
        next_trigger_at=NOW,
        revision=1,
    )
    return actor, pipeline, version, schedule


def test_save_requires_frozen_matching_version_and_projects_server_next_time(monkeypatch):
    monkeypatch.setattr("etl_agent.modules.scheduling.service.utcnow", lambda: NOW)
    actor, pipeline, version, _ = objects()
    payload = c.ScheduleDefinitionRequest(
        pipeline_id=pipeline.id,
        version_id=version.id,
        kind="once",
        timezone="Asia/Shanghai",
        run_at=NOW + timedelta(hours=1),
        cron_expression=None,
        enabled=True,
    )
    session = SessionDouble([pipeline, version])
    result = ScheduleService(Settings(), LedgerDouble()).save(session, actor, payload)
    assert result["next_trigger_at"] == "2026-10-23T09:00:00Z"
    assert isinstance(session.added[0], m.ScheduleDefinition)
    version.is_immutable = False
    with pytest.raises(ApplicationError) as failure:
        ScheduleService(Settings(), LedgerDouble()).save(
            SessionDouble([pipeline, version]), actor, payload
        )
    assert failure.value.code == "STATE_CONFLICT"


def test_due_callback_and_trigger_record_share_session_and_never_create_execution(monkeypatch):
    monkeypatch.setattr("etl_agent.modules.scheduling.service.utcnow", lambda: NOW)
    _, pipeline, version, schedule = objects()
    prep = m.Preparation(
        id=uuid4(), project_id=pipeline.project_id, version_id=version.id, purpose="execute_etl"
    )
    session = SessionDouble(
        [None, schedule, pipeline, None, None], {prep.id: prep, schedule.id: schedule}
    )
    observed = []

    def prepare(db, row):
        observed.append((db, row.id))
        db.add(prep)
        return prep

    result = ScheduleService(Settings(), LedgerDouble()).on_due(session, schedule.id, NOW, prepare)
    assert observed == [(session, schedule.id)]
    assert result["result"] == "created" and result["preparation_id"] == str(prep.id)
    assert schedule.next_trigger_at is None
    assert any(isinstance(row, m.ScheduleTriggerRecord) for row in session.added)
    assert not any(isinstance(row, (m.ExecutionRun, m.ManagedCommand)) for row in session.added)


def test_missing_prepare_port_is_explicit_block_without_false_created_record(monkeypatch):
    monkeypatch.setattr("etl_agent.modules.scheduling.service.utcnow", lambda: NOW)
    _, pipeline, _, schedule = objects()
    session = SessionDouble([None, schedule, pipeline, None, None], {schedule.id: schedule})
    with pytest.raises(ApplicationError) as failure:
        ScheduleService(Settings(), LedgerDouble()).on_due(session, schedule.id, NOW)
    assert failure.value.code == "DEPENDENCY_UNAVAILABLE"
    assert session.added == []


def test_duplicate_due_reuses_existing_preparation_without_callback():
    _, pipeline, _, schedule = objects()
    previous = m.ScheduleTriggerRecord(id=uuid4(), preparation_id=uuid4(), result="created")
    session = SessionDouble([None, schedule, pipeline, previous], {schedule.id: schedule})
    result = ScheduleService(Settings(), LedgerDouble()).on_due(session, schedule.id, NOW)
    assert result["reason"] == "DUPLICATE_TRIGGER"
    assert result["preparation_id"] == str(previous.preparation_id)
    assert session.added == []


def test_overlap_skips_without_invoking_prepare(monkeypatch):
    monkeypatch.setattr("etl_agent.modules.scheduling.service.utcnow", lambda: NOW)
    _, pipeline, _, schedule = objects()
    session = SessionDouble([None, schedule, pipeline, None, uuid4()], {schedule.id: schedule})
    result = ScheduleService(Settings(), LedgerDouble()).on_due(session, schedule.id, NOW)
    assert result["result"] == "skipped" and result["reason"] == "OVERLAPPING_INTENT"


def test_edit_requires_expected_revision_before_mutation(monkeypatch):
    monkeypatch.setattr("etl_agent.modules.scheduling.service.utcnow", lambda: NOW)
    actor, pipeline, version, schedule = objects()
    schedule.run_at = NOW + timedelta(hours=1)
    payload = c.ScheduleDefinitionRequest(
        pipeline_id=pipeline.id,
        version_id=version.id,
        kind="once",
        timezone="Asia/Shanghai",
        run_at=NOW + timedelta(hours=2),
        cron_expression=None,
        enabled=True,
    )
    session = SessionDouble([pipeline, version, schedule])
    with pytest.raises(ApplicationError) as failure:
        ScheduleService(Settings(), LedgerDouble()).save(
            session, actor, payload, schedule_id=schedule.id, expected_revision=2
        )
    assert failure.value.code == "REVISION_MISMATCH"
    assert schedule.run_at == NOW + timedelta(hours=1)
