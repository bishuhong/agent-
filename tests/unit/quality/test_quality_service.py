from types import SimpleNamespace
from uuid import uuid4

import pytest

from etl_agent.config import Settings
from etl_agent.infrastructure.persistence import models as m
from etl_agent.modules.quality.domain import ErrorCodeCount, RuleResult
from etl_agent.modules.quality.service import QualityService
from etl_agent.shared.errors import ApplicationError


class SessionDouble:
    def __init__(self, run, preparation, version):
        self.run, self.preparation, self.version = run, preparation, version
        self.added = []

    def scalar(self, statement):
        return self.run

    def get(self, model, resource_id):
        return self.preparation if model is m.Preparation else self.version

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


def setup_service():
    project, run_id, prep_id, version_id = uuid4(), uuid4(), uuid4(), uuid4()
    run = m.ExecutionRun(
        id=run_id,
        project_id=project,
        preparation_id=prep_id,
        version_id=version_id,
        input_records=10,
        output_records=7,
        error_records=3,
        status="succeeded",
    )
    prep = m.Preparation(
        id=prep_id,
        project_id=project,
        quality_contract={
            "max_rejection_ratio": "0",
            "allow_empty_input": False,
            "rules": [{"rule_id": "positive", "field_name": "amount", "error_code": "BAD_AMOUNT"}],
        },
    )
    version = m.PipelineVersion(id=version_id, project_id=project, artifact_digest="a" * 64)
    ledger = LedgerDouble()
    return QualityService(Settings(), ledger), SessionDouble(run, prep, version), ledger


def test_record_persists_real_counters_and_public_projection_excludes_raw_errors():
    service, session, ledger = setup_service()
    result = service.record(
        session,
        session.run.id,
        rule_results=[RuleResult("positive", 10, 3)],
        error_code_distribution=[ErrorCodeCount("BAD_AMOUNT", 3)],
        violations=[
            {
                "rule_id": "positive",
                "field_name": "amount",
                "error_code": "BAD_AMOUNT",
                "protected_record_ref": "internal/secret/path",
                "reason": "raw@example.com",
            }
        ],
    )
    assert result["valid_records"] == "7" and result["error_records"] == "3"
    assert result["publish_eligible"] is False
    assert result["rule_results"][0]["checked_records"] == "10"
    assert "raw@example.com" not in repr(session.added)
    assert "internal/secret/path" not in repr(result)
    violation = next(row for row in session.added if isinstance(row, m.QualityViolation))
    assert violation.reason == "记录未通过冻结的质量规则"
    assert ledger.events and "raw@example.com" not in repr(ledger.events)


def test_record_does_not_replace_unknown_count_with_zero():
    service, session, _ = setup_service()
    session.run.input_records = None
    result = service.record(session, session.run.id)
    assert result["result"] == "unknown" and result["publish_eligible"] is False


def test_unfrozen_rule_labels_fail_before_any_persistence():
    service, session, ledger = setup_service()
    with pytest.raises(ApplicationError) as failure:
        service.record(
            session,
            session.run.id,
            violations=[
                {
                    "rule_id": "other",
                    "field_name": "amount",
                    "error_code": "BAD_AMOUNT",
                    "protected_record_ref": "secret",
                }
            ],
        )
    assert failure.value.code == "VALIDATION_FAILED"
    assert session.added == [] and ledger.events == []


def test_cross_project_frozen_facts_fail_closed():
    service, session, _ = setup_service()
    session.preparation.project_id = uuid4()
    with pytest.raises(ApplicationError):
        service.record(session, session.run.id)
