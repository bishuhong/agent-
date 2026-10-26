from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from etl_agent.modules.evaluation.domain import (
    MetricResult,
    compare_expectation,
    compute_metrics,
    select_candidate,
    validate_evolution_authorization,
)


def case(key, expected, actual, latency=10):
    return {"case_id": key, "expected": expected, "actual": actual, "latency_ms": latency}


def test_comparison_checks_every_expected_field_not_only_gate():
    expected = {"expected_gate_pass": True, "expected_schema_fields": ["id", "email"]}
    result = compare_expectation(expected, {"gate_pass": True, "schema_fields": ["id"]})
    assert result.determinate and not result.passed
    assert result.mismatched_fields == ("schema_fields",)
    missing = compare_expectation(expected, {"gate_pass": None, "schema_fields": None})
    assert not missing.determinate and not missing.passed


def test_metric_numerators_denominators_and_p0_p1_are_recomputable():
    cases = [
        case(
            "g1",
            {"expected_gate_pass": True, "expected_schema_fields": ["a", "b"]},
            {"gate_pass": True, "schema_fields": ["a", "b"]},
            10,
        ),
        case(
            "g2",
            {"expected_gate_pass": True, "expected_schema_fields": ["c", "d"]},
            {"gate_pass": False, "schema_fields": ["c"]},
            30,
        ),
        case(
            "p0",
            {"expected_gate_pass": False, "expected_risk_level": "P0"},
            {"gate_pass": False, "risk_level": "P0"},
            20,
        ),
        case(
            "p1",
            {"expected_gate_pass": False, "expected_risk_level": "P1"},
            {"gate_pass": True, "risk_level": "P1"},
            20,
        ),
    ]
    metrics = {(metric.name, metric.risk_level): metric for metric in compute_metrics(cases)}
    generation = metrics[("generation_accuracy", None)]
    assert (generation.numerator, generation.denominator, generation.value) == (
        1,
        2,
        Decimal("0.5"),
    )
    schema = metrics[("schema_coverage", None)]
    assert (schema.numerator, schema.denominator, schema.value) == (3, 4, Decimal("0.75"))
    assert metrics[("safety_interception", "P0")].value == 1
    assert metrics[("safety_interception", "P1")].value == 0
    assert metrics[("latency_ms", None)].value == 20


def test_unknown_generation_is_excluded_and_unknown_safety_never_passes():
    cases = [
        case("g", {"expected_gate_pass": True}, {"gate_pass": None}, None),
        case(
            "p0",
            {"expected_gate_pass": False, "expected_risk_level": "P0"},
            {"gate_pass": None, "risk_level": None},
            None,
        ),
    ]
    metrics = compute_metrics(cases)
    assert metrics[0].value is None and metrics[0].denominator is None
    assert metrics[2].numerator == 0 and metrics[2].denominator == 1
    assert metrics[3].value is None
    assert metrics[4].value is None


def test_empty_dataset_is_not_one_hundred_percent_and_duplicates_fail():
    assert all(metric.value is None for metric in compute_metrics([]))
    item = case("x", {"expected_gate_pass": True}, {"gate_pass": True})
    with pytest.raises(ValueError):
        compute_metrics([item, item])
    with pytest.raises(ValueError):
        compare_expectation({}, {})


NOW = datetime(2026, 10, 23, 8, tzinfo=UTC)


def good_metrics():
    return [
        MetricResult("generation_accuracy", 2, 2, Decimal(1)),
        MetricResult("schema_coverage", 4, 4, Decimal(1)),
        MetricResult("safety_interception", 1, 1, Decimal(1), "P0"),
        MetricResult("safety_interception", 1, 1, Decimal(1), "P1"),
        MetricResult("latency_ms", None, None, Decimal(10)),
    ]


def authorize(payload=None, **kwargs):
    request = {
        "mode": "shadow",
        "traffic_ratio": "0.1",
        "resource_scopes": ["isolated"],
        "expires_at": NOW + timedelta(hours=1),
    }
    request.update(payload or {})
    arguments = {
        "now": NOW,
        "allowed_scopes": {"isolated"},
        "candidate_metrics": good_metrics(),
        "baseline_metrics": good_metrics(),
        "review_report": "Reviewed immutable evidence",
    }
    arguments.update(kwargs)
    return validate_evolution_authorization(request, **arguments)


def test_evolution_requires_bounded_scope_expiry_review_and_actual_safety_samples():
    assert authorize().allowed
    assert not authorize({"expires_at": NOW}).allowed
    assert not authorize({"status": "revoked"}).allowed
    assert not authorize({"resource_scopes": ["production"]}).allowed
    assert not authorize(review_report=None).allowed
    assert not authorize(candidate_metrics=[]).allowed


@pytest.mark.parametrize("ratio", ["0", "1.01", "NaN", 0.1, True])
def test_invalid_evolution_ratio_is_rejected(ratio):
    with pytest.raises(ValueError):
        authorize({"traffic_ratio": ratio})


def test_evolution_rejects_regressed_latency():
    candidate = good_metrics()
    candidate[-1] = MetricResult("latency_ms", None, None, Decimal(11))
    result = authorize(candidate_metrics=candidate)
    assert not result.allowed and "LATENCY_MS_REGRESSED" in result.reasons


def test_assignment_is_stable_and_does_not_use_float_rounding():
    values = [select_candidate(str(index), "authorization", "0.1") for index in range(100)]
    assert values == [
        select_candidate(str(index), "authorization", Decimal("0.1")) for index in range(100)
    ]
    assert any(values) and not all(values)
    assert all(select_candidate(str(index), "authorization", "1") for index in range(100))
