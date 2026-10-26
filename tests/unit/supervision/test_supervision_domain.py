from copy import deepcopy

import pytest

from etl_agent.modules.supervision.domain import evaluate_budget


@pytest.fixture
def budget():
    return {
        "max_input_records": "100",
        "max_output_bytes": "1000",
        "max_duration_ms": "1000",
        "max_amplification_ratio": "10",
        "max_rejection_ratio": "0.05",
        "on_exceed": "kill",
    }


@pytest.fixture
def metrics():
    return {
        "input_records": "100",
        "output_records": "1000",
        "output_bytes": "1000",
        "duration_ms": "1000",
        "error_records": "5",
    }


def test_all_five_equal_boundaries_do_not_trigger(budget, metrics):
    original = deepcopy(metrics)
    result = evaluate_budget(budget, metrics)
    assert result.decision == "within_budget"
    assert result.action_taken == "none"
    assert result.exceeded_metrics == ()
    assert metrics == original


@pytest.mark.parametrize(
    "field,value,budget_name",
    [
        ("input_records", "101", "max_input_records"),
        ("output_bytes", "1001", "max_output_bytes"),
        ("duration_ms", "1001", "max_duration_ms"),
        ("output_records", "1001", "max_amplification_ratio"),
        ("error_records", "6", "max_rejection_ratio"),
    ],
)
def test_each_budget_strictly_exceeded_requests_but_does_not_claim_stop(
    budget, metrics, field, value, budget_name
):
    metrics[field] = value
    result = evaluate_budget(budget, metrics)
    assert result.decision == "exceeded"
    assert budget_name in result.exceeded_metrics
    assert result.action_taken == "kill_requested"


def test_zero_input_positive_output_is_excess_not_division_by_zero(budget, metrics):
    metrics.update(input_records="0", output_records="1", error_records="0")
    result = evaluate_budget(budget, metrics)
    assert result.decision == "exceeded"
    assert "max_amplification_ratio" in result.exceeded_metrics
    assert "max_rejection_ratio" in result.unknown_metrics


def test_unknown_metrics_are_not_zero_and_known_excess_takes_precedence(budget):
    result = evaluate_budget(budget, {})
    assert result.decision == "unknown" and result.action_taken == "none"
    assert len(result.unknown_metrics) == 5
    result = evaluate_budget(budget, {"duration_ms": "1001"})
    assert result.decision == "exceeded" and result.action_taken == "kill_requested"


@pytest.mark.parametrize("action,expected", [("warn", "warn"), ("isolate_alert", "isolate_alert")])
def test_frozen_actions_are_preserved(budget, metrics, action, expected):
    budget["on_exceed"] = action
    metrics["duration_ms"] = "1001"
    assert evaluate_budget(budget, metrics).action_taken == expected


def test_unknown_actions_and_inconsistent_error_counts_are_rejected(budget, metrics):
    budget["on_exceed"] = "ignore"
    with pytest.raises(ValueError):
        evaluate_budget(budget, metrics)


def test_budget_ratio_uses_exact_fraction_even_beyond_decimal_context(budget, metrics):
    budget["max_rejection_ratio"] = "0.3333333333333333333333333333"
    metrics.update(input_records="3", output_records="1", error_records="1")
    assert "max_rejection_ratio" in evaluate_budget(budget, metrics).exceeded_metrics
    budget["on_exceed"] = "kill"
    metrics["error_records"] = "101"
    with pytest.raises(ValueError):
        evaluate_budget(budget, metrics)
