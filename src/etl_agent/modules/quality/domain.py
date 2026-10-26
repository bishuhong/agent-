"""Deterministic quality rules; this module never runs a data-plane job.

The execution specification is an engine-neutral, typed plan. A connector must
translate and verify these operations before any real SeaTunnel submission.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any, Literal


@dataclass(frozen=True)
class QualityRule:
    rule_id: str
    field_name: str
    kind: Literal["not_null", "positive", "mask_email"]
    stage: Literal["validate", "transform"]
    error_code: str


@dataclass(frozen=True)
class QualityExecutionSpec:
    rules: tuple[QualityRule, ...]
    error_resource: Mapping[str, Any]
    max_rejection_ratio: Decimal
    allow_empty_input: bool
    operations: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    checked_records: int | None
    violating_records: int | None


@dataclass(frozen=True)
class ErrorCodeCount:
    error_code: str
    record_count: int


@dataclass(frozen=True)
class QualitySummary:
    result: Literal["passed", "failed", "unknown"]
    input_records: int | None
    valid_records: int | None
    error_records: int | None
    rejection_ratio: Decimal | None
    publish_eligible: bool
    rule_results: tuple[RuleResult, ...]
    error_code_distribution: tuple[ErrorCodeCount, ...]


@dataclass(frozen=True)
class Violation:
    record_index: int
    rule_id: str
    field_name: str
    error_code: str
    reason: str


@dataclass(frozen=True)
class ProtectedErrorRecord:
    """Internal data-plane value; never serialize this into a public response."""

    record_index: int
    record: Mapping[str, Any]
    error_codes: tuple[str, ...]


@dataclass(frozen=True)
class QualityEvaluation:
    valid_records: tuple[Mapping[str, Any], ...]
    protected_error_records: tuple[ProtectedErrorRecord, ...]
    violations: tuple[Violation, ...]
    summary: QualitySummary


def _decimal(value: object, name: str, *, ratio: bool = False) -> Decimal:
    if isinstance(value, (bool, float)) or not isinstance(value, (str, int, Decimal)):
        raise ValueError(f"{name} must be an exact decimal")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{name} is invalid") from error
    if not result.is_finite() or result < 0 or (ratio and result > 1):
        raise ValueError(f"{name} is outside its permitted range")
    return result


def _count(value: int | str | None, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"{name} must be a nonnegative integer")
    if isinstance(value, str) and (not value or not value.isascii() or not value.isdecimal()):
        raise ValueError(f"{name} must be a nonnegative integer")
    result = int(value)
    if result < 0:
        raise ValueError(f"{name} must be nonnegative")
    return result


def compile_quality_contract(
    contract: Mapping[str, Any], schema: Mapping[str, Any]
) -> QualityExecutionSpec:
    """Validate the frozen contract and compile safe operations, without SQL.

    A resource is a structured ResourceScope, never a raw URL/SQL fragment.
    Its ownership and authorization are checked by the caller's resource policy.
    """
    required = {"rules", "error_resource", "max_rejection_ratio", "allow_empty_input"}
    if not required.issubset(contract):
        raise ValueError("quality contract requires all frozen fields")
    if not isinstance(contract["allow_empty_input"], bool):
        raise ValueError("allow_empty_input must be boolean")
    resource = contract["error_resource"]
    if not isinstance(resource, Mapping) or not resource:
        raise ValueError("error_resource must be a nonempty managed ResourceScope")
    items = contract["rules"]
    if not isinstance(items, (list, tuple)):
        raise ValueError("rules must be an array")
    seen: set[str] = set()
    rules: list[QualityRule] = []
    operations: list[Mapping[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("each quality rule must be an object")
        for key in ("rule_id", "field_name", "kind", "stage", "error_code"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ValueError(f"quality rule {key} must be nonempty")
        rule_id, field_name, kind = item["rule_id"], item["field_name"], item["kind"]
        if rule_id in seen:
            raise ValueError("quality rule_id must be unique")
        seen.add(rule_id)
        if field_name not in schema:
            raise ValueError("quality field does not exist in the frozen schema")
        if kind not in {"not_null", "positive", "mask_email"}:
            raise ValueError("unknown quality rule kind")
        stage: Literal["validate", "transform"] = (
            "transform" if kind == "mask_email" else "validate"
        )
        if item["stage"] != stage:
            raise ValueError("quality rule stage does not match its kind")
        field = schema[field_name]
        logical_type = field.get("logical_type") if isinstance(field, Mapping) else field
        if not isinstance(logical_type, str) or logical_type == "unknown":
            raise ValueError("quality rule requires a known field type")
        if kind == "positive" and logical_type not in {"integer", "decimal"}:
            raise ValueError("positive requires an integer or decimal field")
        if kind == "mask_email" and logical_type != "string":
            raise ValueError("mask_email requires a string field")
        rule = QualityRule(rule_id, field_name, kind, stage, item["error_code"])
        rules.append(rule)
        operations.append(
            {
                "operation": kind,
                "stage": stage,
                "field_name": field_name,
                "rule_id": rule_id,
                "error_code": rule.error_code,
            }
        )
    return QualityExecutionSpec(
        tuple(rules),
        deepcopy(dict(resource)),
        _decimal(contract["max_rejection_ratio"], "max_rejection_ratio", ratio=True),
        contract["allow_empty_input"],
        tuple(operations),
    )


def mask_email(value: object) -> str | None:
    """Mask the complete local part; null remains null and is not a violation.

    Malformed strings fail rather than leaking the original value. This is a
    conservative transformation, not an RFC email-address validation service.
    """
    if value is None:
        return None
    if not isinstance(value, str) or value.count("@") != 1:
        raise ValueError("email cannot be safely masked")
    local, domain = value.split("@")
    if not local or not domain or any(char.isspace() for char in value):
        raise ValueError("email cannot be safely masked")
    return "***@" + domain


def summarize_quality(
    input_records: int | str | None,
    valid_records: int | str | None,
    error_records: int | str | None,
    contract: Mapping[str, Any],
    *,
    rule_results: Sequence[RuleResult] = (),
    error_code_distribution: Sequence[ErrorCodeCount] = (),
) -> QualitySummary:
    """Compute quality eligibility only; it grants no publication authority.

    General filtering/aggregation can change valid output cardinality, therefore
    input == valid + errors is intentionally NOT asserted.
    """
    input_count = _count(input_records, "input_records")
    valid_count = _count(valid_records, "valid_records")
    error_count = _count(error_records, "error_records")
    if "max_rejection_ratio" not in contract or "allow_empty_input" not in contract:
        raise ValueError("quality publication conditions must be explicitly frozen")
    threshold = _decimal(contract["max_rejection_ratio"], "max_rejection_ratio", ratio=True)
    allow_empty = contract["allow_empty_input"]
    if not isinstance(allow_empty, bool):
        raise ValueError("allow_empty_input must be boolean")
    if input_count is not None and error_count is not None and error_count > input_count:
        raise ValueError("deduplicated error_records cannot exceed input_records")
    for rule in rule_results:
        checked = _count(rule.checked_records, "checked_records")
        violated = _count(rule.violating_records, "violating_records")
        if checked is not None and violated is not None and violated > checked:
            raise ValueError("violating_records cannot exceed checked_records")
    for item in error_code_distribution:
        _count(item.record_count, "record_count")
    ratio = (
        Decimal(error_count) / Decimal(input_count)
        if input_count not in (None, 0) and error_count is not None
        else None
    )
    # A missing count is unknown, never a successful zero.
    if None in (input_count, valid_count, error_count):
        result: Literal["passed", "failed", "unknown"] = "unknown"
    elif input_count == 0:
        result = "passed" if allow_empty and valid_count == 0 else "failed"
    else:
        within_threshold = error_count is not None and Fraction(
            error_count, input_count
        ) <= Fraction(threshold)
        result = "passed" if within_threshold else "failed"
    return QualitySummary(
        result,
        input_count,
        valid_count,
        error_count,
        ratio,
        result == "passed",
        tuple(rule_results),
        tuple(error_code_distribution),
    )


def evaluate_records(
    records: Iterable[Mapping[str, Any]],
    contract: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> QualityEvaluation:
    """Reference rule evaluator for L0/L1 and deterministic connector tests.

    This is explicitly not a replacement for SeaTunnel execution; real execution
    statistics must come from its observed data path, not this evaluator.
    """
    spec = compile_quality_contract(contract, schema)
    valid: list[Mapping[str, Any]] = []
    protected: list[ProtectedErrorRecord] = []
    violations: list[Violation] = []
    checked: Counter[str] = Counter()
    violating: Counter[str] = Counter()
    codes: Counter[str] = Counter()
    total = 0
    # Validation always precedes transforms, independent of configuration order.
    ordered = sorted(spec.rules, key=lambda rule: rule.stage == "transform")
    for index, original in enumerate(records):
        if not isinstance(original, Mapping):
            raise ValueError("record must be a mapping")
        total += 1
        output = deepcopy(dict(original))
        hit_codes: set[str] = set()
        has_violation = False
        for rule in ordered:
            checked[rule.rule_id] += 1
            value = output.get(rule.field_name)
            failed = False
            if rule.kind == "not_null":
                failed = value is None
            elif rule.kind == "positive":
                try:
                    number = _decimal(value, "quality value")
                    failed = number <= 0
                except ValueError:
                    failed = True
            else:
                try:
                    output[rule.field_name] = mask_email(value)
                except ValueError:
                    failed = True
            if failed:
                has_violation = True
                violating[rule.rule_id] += 1
                hit_codes.add(rule.error_code)
                violations.append(
                    Violation(
                        index,
                        rule.rule_id,
                        rule.field_name,
                        rule.error_code,
                        "Record failed the frozen quality rule",
                    )
                )
        if has_violation:
            # The original is retained only in this explicitly protected channel.
            protected.append(
                ProtectedErrorRecord(index, deepcopy(dict(original)), tuple(sorted(hit_codes)))
            )
            codes.update(hit_codes)  # One record contributes once per error code.
        else:
            valid.append(output)
    summary = summarize_quality(
        total,
        len(valid),
        len(protected),
        contract,
        rule_results=tuple(
            RuleResult(rule.rule_id, checked[rule.rule_id], violating[rule.rule_id])
            for rule in spec.rules
        ),
        error_code_distribution=tuple(
            ErrorCodeCount(code, count) for code, count in sorted(codes.items())
        ),
    )
    return QualityEvaluation(tuple(valid), tuple(protected), tuple(violations), summary)
