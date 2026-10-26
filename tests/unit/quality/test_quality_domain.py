from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal

import pytest

from etl_agent.modules.quality.domain import (
    compile_quality_contract,
    evaluate_records,
    mask_email,
    summarize_quality,
)


@pytest.fixture
def schema():
    return {"amount": "decimal", "email": "string"}


@pytest.fixture
def contract():
    return {
        "rules": [
            {
                "rule_id": "required",
                "field_name": "amount",
                "kind": "not_null",
                "stage": "validate",
                "error_code": "AMOUNT_REQUIRED",
            },
            {
                "rule_id": "positive",
                "field_name": "amount",
                "kind": "positive",
                "stage": "validate",
                "error_code": "AMOUNT_POSITIVE",
            },
            {
                "rule_id": "mask",
                "field_name": "email",
                "kind": "mask_email",
                "stage": "transform",
                "error_code": "EMAIL_MASK_FAILED",
            },
        ],
        "error_resource": {"resource_ref": "protected-errors"},
        "max_rejection_ratio": "0",
        "allow_empty_input": False,
    }


def test_seven_valid_three_error_records_are_reproducible(contract, schema):
    records = [{"amount": str(i + 1), "email": "person@example.com"} for i in range(7)]
    records += [
        {"amount": None, "email": "secret@example.com"},
        {"amount": "-1", "email": "person@example.com"},
        {"amount": "1", "email": "not-an-email"},
    ]
    original = deepcopy(records)
    evaluated = evaluate_records(records, contract, schema)
    assert len(evaluated.valid_records) == 7
    assert len(evaluated.protected_error_records) == 3
    assert evaluated.summary.error_records == 3
    assert evaluated.summary.rejection_ratio == Decimal("0.3")
    assert not evaluated.summary.publish_eligible
    assert evaluated.summary.result == "failed"
    assert len(evaluated.violations) == 4  # null fails two rules but is one record
    assert all(record["email"] == "***@example.com" for record in evaluated.valid_records)
    assert records == original
    assert "secret@example.com" not in repr(asdict(evaluated.summary))
    assert "secret@example.com" not in repr(evaluated.violations)
    assert evaluated.protected_error_records[0].record["email"] == "secret@example.com"


def test_identical_error_codes_are_counted_once_per_record(contract, schema):
    contract["rules"][0]["error_code"] = "BAD_AMOUNT"
    contract["rules"][1]["error_code"] = "BAD_AMOUNT"
    result = evaluate_records([{"amount": None, "email": None}], contract, schema)
    assert result.summary.error_records == 1
    assert result.summary.error_code_distribution[0].record_count == 1
    assert len(result.violations) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"field_name": "missing"},
        {"kind": "run_shell"},
        {"stage": "transform"},
        {"error_code": ""},
        {"rule_id": "positive"},
    ],
)
def test_rule_contract_rejects_invalid_fields_stages_and_duplicates(contract, schema, change):
    contract["rules"][0].update(change)
    with pytest.raises(ValueError):
        compile_quality_contract(contract, schema)


def test_mask_only_transforms_and_null_stays_null(contract, schema):
    contract["rules"] = contract["rules"][-1:]
    result = evaluate_records([{"email": "a@domain.test"}, {"email": None}], contract, schema)
    assert result.summary.error_records == 0
    assert result.summary.publish_eligible
    assert result.valid_records == ({"email": "***@domain.test"}, {"email": None})


@pytest.mark.parametrize("value", ["bad", "@example.com", "x@", "x y@example.com", 1])
def test_mask_never_falls_back_to_returning_raw_malformed_input(value):
    with pytest.raises(ValueError):
        mask_email(value)


def test_empty_and_unknown_counts_are_distinct(contract):
    assert summarize_quality(0, 0, 0, contract).result == "failed"
    contract["allow_empty_input"] = True
    empty = summarize_quality(0, 0, 0, contract)
    assert empty.publish_eligible and empty.rejection_ratio is None
    unknown = summarize_quality(None, 0, 0, contract)
    assert unknown.result == "unknown" and not unknown.publish_eligible


def test_frozen_threshold_equality_and_filters_are_supported(contract):
    contract["max_rejection_ratio"] = "0.3"
    assert summarize_quality(10, 2, 3, contract).publish_eligible
    assert not summarize_quality(10, 2, 4, contract).publish_eligible
    with pytest.raises(ValueError):
        summarize_quality(10, 1, 11, contract)


@pytest.mark.parametrize("count", [True, "-1", "1.0", "１２", -1, 1.0])
def test_invalid_count_never_coerces_to_success(count, contract):
    with pytest.raises(ValueError):
        summarize_quality(count, 0, 0, contract)


def test_exact_decimal_and_explicit_frozen_fields(contract, schema):
    contract["max_rejection_ratio"] = 0.3
    with pytest.raises(ValueError):
        compile_quality_contract(contract, schema)


def test_publication_threshold_uses_exact_ratio_not_rounded_display(contract):
    contract["max_rejection_ratio"] = "0.3333333333333333333333333333"
    result = summarize_quality(3, 2, 1, contract)
    assert not result.publish_eligible
    del contract["allow_empty_input"]
    with pytest.raises(ValueError):
        compile_quality_contract(contract, schema)
