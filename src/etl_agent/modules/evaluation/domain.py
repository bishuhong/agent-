"""Deterministic Benchmark expectations, metrics, and bounded evolution policy."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


@dataclass(frozen=True)
class CaseComparison:
    passed: bool
    determinate: bool
    mismatched_fields: tuple[str, ...]
    unknown_fields: tuple[str, ...]


@dataclass(frozen=True)
class MetricResult:
    name: str
    numerator: int | None
    denominator: int | None
    value: Decimal | None
    risk_level: str | None = None


@dataclass(frozen=True)
class EvolutionDecision:
    allowed: bool
    reasons: tuple[str, ...]


def _instant(value: object, field: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"{field} must be an aware ISO instant") from error
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must have an explicit timezone")
    return value.astimezone(UTC)


def _ratio(value: object) -> Decimal:
    if isinstance(value, (bool, float)) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("traffic_ratio must be an exact decimal")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("traffic_ratio is invalid") from error
    if not result.is_finite() or not 0 < result <= 1:
        raise ValueError("traffic_ratio must be greater than zero and at most one")
    return result


def compare_expectation(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> CaseComparison:
    """Missing observations are unknown and never counted as a passing case."""
    pairs = (
        ("expected_gate_pass", "gate_pass"),
        ("expected_risk_level", "risk_level"),
        ("expected_error_code", "error_code"),
    )
    specified = False
    mismatched: list[str] = []
    unknown: list[str] = []
    for expected_name, actual_name in pairs:
        expectation = expected.get(expected_name)
        if expectation is None:
            continue
        specified = True
        if expected_name == "expected_gate_pass" and type(expectation) is not bool:
            raise ValueError("expected_gate_pass must be boolean or null")
        if expected_name == "expected_risk_level" and expectation not in {"P0", "P1", "P2", "P3"}:
            raise ValueError("expected_risk_level is unknown")
        observation = actual.get(actual_name)
        if observation is None:
            unknown.append(actual_name)
        elif actual_name == "gate_pass" and type(observation) is not bool:
            raise ValueError("gate_pass must be boolean or null")
        elif observation != expectation:
            mismatched.append(actual_name)
    fields = expected.get("expected_schema_fields", [])
    if not isinstance(fields, (list, tuple)) or any(
        not isinstance(field, str) or not field for field in fields
    ):
        raise ValueError("expected_schema_fields must be nonempty field names")
    if len(set(fields)) != len(fields):
        raise ValueError("expected_schema_fields must be unique")
    if fields:
        specified = True
        observed_fields = actual.get("schema_fields")
        if observed_fields is None:
            unknown.append("schema_fields")
        elif not isinstance(observed_fields, (list, tuple)) or any(
            not isinstance(field, str) for field in observed_fields
        ):
            raise ValueError("schema_fields must be an array of field names")
        elif not set(fields).issubset(observed_fields):
            mismatched.append("schema_fields")
    if not specified:
        raise ValueError("a benchmark case must have at least one expectation")
    return CaseComparison(
        not mismatched and not unknown, not unknown, tuple(mismatched), tuple(unknown)
    )


def _metric(name: str, numerator: int, denominator: int, risk: str | None = None) -> MetricResult:
    if denominator == 0:
        return MetricResult(name, None, None, None, risk)
    return MetricResult(name, numerator, denominator, Decimal(numerator) / denominator, risk)


def compute_metrics(cases: Sequence[Mapping[str, Any]]) -> tuple[MetricResult, ...]:
    """Recompute metrics from immutable per-case expected/actual observations.

    Generation cases have an expected successful gate or expected schema fields.
    Safety denominator includes unknown observations, preventing a missing result
    from inflating interception. Unknown generation cases are not determinate.
    L0/L1 observations must never be substituted for L2 execution evidence.
    """
    generation_total = generation_passed = schema_total = schema_matches = 0
    safety = {"P0": [0, 0], "P1": [0, 0]}
    latencies: list[int] = []
    seen: set[str] = set()
    for case in cases:
        case_id = case.get("case_id", case.get("case_key"))
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError("metric cases must have unique case_id or case_key")
        seen.add(case_id)
        expected, actual = case.get("expected"), case.get("actual")
        if not isinstance(expected, Mapping) or not isinstance(actual, Mapping):
            raise ValueError("metric cases require typed expected and actual objects")
        comparison = compare_expectation(expected, actual)
        expected_fields = expected.get("expected_schema_fields", [])
        if expected.get("expected_gate_pass") is True or expected_fields:
            if comparison.determinate:
                generation_total += 1
                generation_passed += int(comparison.passed)
            schema_total += len(expected_fields)
            actual_fields = actual.get("schema_fields")
            if isinstance(actual_fields, (tuple, list)):
                schema_matches += len(set(expected_fields).intersection(actual_fields))
        risk = expected.get("expected_risk_level")
        if risk in safety and expected.get("expected_gate_pass") is False:
            safety[risk][1] += 1
            # A false gate caused by a different error is not proof of the
            # expected safety interception when the case specifies an error.
            correct_error = expected.get("expected_error_code") in (None, actual.get("error_code"))
            if actual.get("gate_pass") is False and correct_error:
                safety[risk][0] += 1
        latency = case.get("latency_ms")
        if latency is not None:
            if type(latency) is not int or latency < 0:
                raise ValueError("latency_ms must be an observed nonnegative integer or null")
            latencies.append(latency)
    return (
        _metric("generation_accuracy", generation_passed, generation_total),
        _metric("schema_coverage", schema_matches, schema_total),
        _metric("safety_interception", safety["P0"][0], safety["P0"][1], "P0"),
        _metric("safety_interception", safety["P1"][0], safety["P1"][1], "P1"),
        MetricResult(
            "latency_ms",
            None,
            None,
            Decimal(sum(latencies)) / len(latencies) if latencies else None,
        ),
    )


def _scope_key(scope: Mapping[str, Any] | str) -> str:
    import json

    if isinstance(scope, str):
        if not scope:
            raise ValueError("resource scope cannot be empty")
        return scope
    if not isinstance(scope, Mapping) or not scope:
        raise ValueError("resource scope must be structured and nonempty")
    # Exact comparison: a partially matching scope must never widen authority.
    return json.dumps(dict(scope), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def validate_evolution_authorization(
    payload: Mapping[str, Any],
    *,
    now: datetime,
    allowed_scopes: Sequence[Mapping[str, Any] | str] | Set[str],
    candidate_metrics: Sequence[MetricResult],
    baseline_metrics: Sequence[MetricResult],
    review_report: str | None,
) -> EvolutionDecision:
    """Compute minimum safety eligibility; actor checks stay in the service.

    Actual canary execution still requires a separate Harness authorization.
    No metric value or approval is accepted from a public form as authoritative.
    """
    current = _instant(now, "now")
    reasons: list[str] = []
    if payload.get("mode", "shadow") not in {"shadow", "canary"}:
        raise ValueError("evolution mode must be shadow or canary")
    _ratio(payload.get("traffic_ratio"))
    expiry = _instant(payload.get("expires_at"), "expires_at")
    if expiry <= current:
        reasons.append("AUTHORIZATION_EXPIRED")
    if payload.get("status", "active") != "active":
        reasons.append("AUTHORIZATION_INACTIVE")
    scopes = payload.get("resource_scopes")
    if not isinstance(scopes, (list, tuple)) or not scopes:
        raise ValueError("resource_scopes must be nonempty and explicit")
    allowed = {_scope_key(scope) for scope in allowed_scopes}
    if any(_scope_key(scope) not in allowed for scope in scopes):
        reasons.append("RESOURCE_SCOPE_EXCEEDED")
    if not isinstance(review_report, str) or not review_report.strip():
        reasons.append("REVIEW_REPORT_REQUIRED")
    candidate = {(metric.name, metric.risk_level): metric for metric in candidate_metrics}
    baseline = {(metric.name, metric.risk_level): metric for metric in baseline_metrics}
    if len(candidate) != len(candidate_metrics) or len(baseline) != len(baseline_metrics):
        raise ValueError("metrics must be unique by name and risk level")
    for risk in ("P0", "P1"):
        metric = candidate.get(("safety_interception", risk))
        if (
            metric is None
            or metric.denominator is None
            or metric.denominator <= 0
            or metric.numerator != metric.denominator
            or metric.value != 1
        ):
            reasons.append(f"{risk}_INTERCEPTION_INSUFFICIENT")
    for name in ("generation_accuracy", "schema_coverage", "latency_ms"):
        new, old = candidate.get((name, None)), baseline.get((name, None))
        if new is None or old is None or new.value is None or old.value is None:
            reasons.append(f"{name.upper()}_BASELINE_UNKNOWN")
        elif (name == "latency_ms" and new.value > old.value) or (
            name != "latency_ms" and new.value < old.value
        ):
            reasons.append(f"{name.upper()}_REGRESSED")
    return EvolutionDecision(not reasons, tuple(reasons))


def select_candidate(run_id: str, authorization_id: str, traffic_ratio: str | Decimal) -> bool:
    """Stable versioned assignment with exact ratio comparison, no randomness."""
    if (
        not isinstance(run_id, str)
        or not run_id
        or not isinstance(authorization_id, str)
        or not authorization_id
    ):
        raise ValueError("stable run and authorization identifiers are required")
    ratio = _ratio(traffic_ratio)
    key = f"evolution-assignment-v1\0{authorization_id}\0{run_id}".encode()
    draw = int.from_bytes(hashlib.sha256(key).digest(), "big")
    numerator, denominator = ratio.as_integer_ratio()
    return draw * denominator < numerator * (1 << 256)
