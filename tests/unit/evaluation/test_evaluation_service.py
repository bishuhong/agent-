from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from etl_agent.api.schemas import contracts as c
from etl_agent.config import Settings
from etl_agent.infrastructure.persistence import models as m
from etl_agent.modules.evaluation.service import BenchmarkService, dataset_digest
from etl_agent.shared.context import ActorContext, RequestContext
from etl_agent.shared.errors import ApplicationError

NOW = datetime(2026, 10, 23, 8, tzinfo=UTC)
BINDING = {"provider": "qwen", "model": "model", "prompt_version": "v1", "policy_version": "v1"}


class SessionDouble:
    def __init__(self, scalar_rows=(), scalar_sets=(), gets=None):
        self.scalar_rows = iter(scalar_rows)
        self.scalar_sets = iter(scalar_sets)
        self.gets = gets or {}
        self.added = []

    def scalar(self, statement):
        return next(self.scalar_rows)

    def scalars(self, statement):
        return next(self.scalar_sets)

    def get(self, model, key):
        return self.gets.get(key)

    def add(self, row):
        self.added.append(row)

    def flush(self):
        pass


class LedgerDouble:
    def __init__(self):
        self.events = []

    def append(self, *args):
        self.events.append(args)
        return SimpleNamespace(id=uuid4())


def objects(scenario="unsafe_hocon", risk="P0"):
    project, dataset_id = uuid4(), uuid4()
    actor = ActorContext(uuid4(), uuid4(), {project: frozenset({"maker"})})
    dataset = m.BenchmarkDataset(id=dataset_id, project_id=project, name="faults", version="v1")
    case = m.BenchmarkCase(
        id=uuid4(),
        project_id=project,
        dataset_id=dataset_id,
        case_key="one",
        level="L1",
        input={"prompt": None, "profile_ids": [], "version_id": None, "fault_scenario": scenario},
        expected={
            "expected_gate_pass": False,
            "expected_risk_level": risk,
            "expected_schema_fields": [],
            "expected_error_code": None,
        },
    )
    dataset.content_digest = dataset_digest(dataset, [case])
    run = m.BenchmarkRun(
        id=uuid4(),
        project_id=project,
        dataset_id=dataset_id,
        preparation_id=None,
        level="L1",
        provider_binding=BINDING,
        status="queued",
        started_at=None,
        finished_at=None,
        created_at=NOW,
        updated_at=NOW,
        revision=1,
    )
    return actor, dataset, case, run


def test_manifest_is_sorted_content_sensitive_and_ignores_database_ids():
    _, dataset, case, _ = objects()
    other = m.BenchmarkCase(
        id=uuid4(),
        case_key="two",
        level="L0",
        input={"version_id": "version"},
        expected={"expected_gate_pass": True},
    )
    assert dataset_digest(dataset, [case, other]) == dataset_digest(dataset, [other, case])
    first = dataset_digest(dataset, [case])
    case.id = uuid4()
    assert dataset_digest(dataset, [case]) == first
    case.expected = {"expected_gate_pass": True}
    assert dataset_digest(dataset, [case]) != first


def test_start_checks_dataset_digest_and_returns_queued_without_engine_commands():
    actor, dataset, case, _ = objects()
    request = c.BenchmarkRunRequest(
        project_id=dataset.project_id,
        dataset_id=dataset.id,
        level="L1",
        provider_binding=BINDING,
        preparation_id=None,
    )
    session = SessionDouble([dataset], [[case]])
    response = BenchmarkService(Settings(), LedgerDouble()).start(
        session, actor, request, RequestContext("r")
    )
    assert response["status"] == "queued"
    assert len(session.added) == 1 and isinstance(session.added[0], m.BenchmarkRun)
    dataset.content_digest = "0" * 64
    with pytest.raises(ApplicationError) as failure:
        BenchmarkService(Settings(), LedgerDouble()).start(
            SessionDouble([dataset], [[case]]), actor, request, RequestContext("r")
        )
    assert failure.value.code == "FINGERPRINT_CHANGED"


def test_l1_observation_comes_from_actual_fault_gate_not_copied_expectations():
    actor, _, case, run = objects(risk="P1")
    session = SessionDouble([run, None], [[case]])
    original = deepcopy(case.expected)
    result = BenchmarkService(Settings(), LedgerDouble()).run_deterministic(session, actor, run.id)
    observed = next(row for row in session.added if isinstance(row, m.BenchmarkCaseResult))
    assert result["status"] == "succeeded"  # completed measurement, failed expected outcome
    assert observed.actual["risk_level"] == "P0"
    assert observed.actual["gate_pass"] is False and observed.passed is False
    assert observed.execution_run_id is None
    assert observed.actual["evidence"][0]["summary"].startswith("L1")
    assert case.expected == original


def test_unknown_l1_scenario_reports_failed_incomplete_measurement():
    actor, _, case, run = objects(scenario="no-such-fault")
    session = SessionDouble([run, None], [[case]])
    response = BenchmarkService(Settings(), LedgerDouble()).run_deterministic(
        session, actor, run.id
    )
    result = next(row for row in session.added if isinstance(row, m.BenchmarkCaseResult))
    assert response["status"] == "failed"
    assert result.actual["gate_pass"] is None and result.passed is False
    assert result.actual["error_code"] == "DEPENDENCY_UNAVAILABLE"


@pytest.mark.parametrize(
    "scenario,error_code",
    [
        ("quality_rejection", "QUALITY_REJECTED"),
        ("budget_exceeded", "BUDGET_EXCEEDED"),
    ],
)
def test_registered_fault_simulations_execute_real_pure_computations(scenario, error_code):
    actor, _, case, run = objects(scenario=scenario, risk=None)
    session = SessionDouble([run, None], [[case]])
    response = BenchmarkService(Settings(), LedgerDouble()).run_deterministic(
        session, actor, run.id
    )
    result = next(row for row in session.added if isinstance(row, m.BenchmarkCaseResult))
    assert response["status"] == "succeeded" and result.passed
    assert result.actual["error_code"] == error_code
    assert result.execution_run_id is None


def test_l2_cannot_use_simulated_execution_path():
    actor, _, _, run = objects()
    run.level = "L2"
    with pytest.raises(ApplicationError) as failure:
        BenchmarkService(Settings(), LedgerDouble()).run_deterministic(
            SessionDouble([run]), actor, run.id
        )
    assert failure.value.code == "STATE_CONFLICT"


def test_unauthorized_caller_cannot_start_evaluation():
    _, dataset, case, _ = objects()
    request = c.BenchmarkRunRequest(
        project_id=dataset.project_id,
        dataset_id=dataset.id,
        level="L1",
        provider_binding=BINDING,
        preparation_id=None,
    )
    with pytest.raises(ApplicationError) as failure:
        BenchmarkService(Settings(), LedgerDouble()).start(
            SessionDouble([dataset], [[case]]),
            ActorContext(uuid4(), uuid4()),
            request,
            RequestContext("r"),
        )
    assert failure.value.code == "FORBIDDEN"


def test_revoke_requires_current_security_role_and_revision():
    project = uuid4()
    authorization = m.EvolutionAuthorization(
        id=uuid4(), project_id=project, status="active", revision=1
    )
    operator = ActorContext(uuid4(), uuid4(), {project: frozenset({"operator"})})
    with pytest.raises(ApplicationError) as failure:
        BenchmarkService(Settings(), LedgerDouble()).revoke_evolution(
            SessionDouble([authorization]), operator, authorization.id, 1
        )
    assert failure.value.code == "FORBIDDEN"
