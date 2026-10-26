"""Pure comparison of actual engine observations and frozen runtime budgets."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any, Literal


@dataclass(frozen=True)
class BudgetDecision:
    decision: Literal["within_budget", "exceeded", "unknown"]
    action_taken: Literal["none", "warn", "kill_requested", "isolate_alert"]
    exceeded_metrics: tuple[str, ...]
    unknown_metrics: tuple[str, ...]


def _exact(value: object, field: str, *, count: bool = False) -> Decimal:
    if isinstance(value, (bool, float)) or not isinstance(value, (str, int, Decimal)):
        raise ValueError(f"{field} requires an exact nonnegative value")
    if (
        count
        and isinstance(value, str)
        and (not value or not value.isascii() or not value.isdecimal())
    ):
        raise ValueError(f"{field} requires a decimal integer string")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"{field} is invalid") from error
    if not result.is_finite() or result < 0 or (count and result != result.to_integral_value()):
        raise ValueError(f"{field} is outside its permitted range")
    return result


def validate_budget(contract: Mapping[str, Any]) -> None:
    for name in ("max_input_records", "max_output_bytes", "max_duration_ms"):
        _exact(contract.get(name), name, count=True)
    _exact(contract.get("max_amplification_ratio"), "max_amplification_ratio")
    if _exact(contract.get("max_rejection_ratio"), "max_rejection_ratio") > 1:
        raise ValueError("max_rejection_ratio must be between zero and one")
    if contract.get("on_exceed") not in {"warn", "kill", "isolate_alert"}:
        raise ValueError("on_exceed is required and must be a known action")


def evaluate_budget(contract: Mapping[str, Any], metrics: Mapping[str, Any]) -> BudgetDecision:
    """Strictly greater exceeds a budget; equality does not trigger an action.

    Unknown values remain unknown. A known violation takes precedence even if
    other metrics are unavailable. This function returns an action intent only;
    callers must use Harness/Worker and confirm an actual stop separately.
    """
    validate_budget(contract)
    exceeded: list[str] = []
    unknown: list[str] = []

    def compare(budget: str, observed: object, *, count: bool = False) -> None:
        if observed is None:
            unknown.append(budget)
        elif _exact(observed, budget, count=count) > _exact(contract[budget], budget):
            exceeded.append(budget)

    compare("max_input_records", metrics.get("input_records"), count=True)
    compare("max_output_bytes", metrics.get("output_bytes"), count=True)
    compare("max_duration_ms", metrics.get("duration_ms"), count=True)
    raw_input = metrics.get("input_records")
    raw_output = metrics.get("output_records")
    raw_error = metrics.get("error_records")
    input_count = _exact(raw_input, "input_records", count=True) if raw_input is not None else None
    output_count = (
        _exact(raw_output, "output_records", count=True) if raw_output is not None else None
    )
    error_count = _exact(raw_error, "error_records", count=True) if raw_error is not None else None
    if input_count is not None and error_count is not None and error_count > input_count:
        raise ValueError("deduplicated error_records cannot exceed input_records")
    if input_count is None or output_count is None:
        unknown.append("max_amplification_ratio")
    elif input_count == 0:
        if output_count > 0:
            exceeded.append("max_amplification_ratio")
        else:
            unknown.append("max_amplification_ratio")
    else:
        if Fraction(output_count) / Fraction(input_count) > Fraction(
            _exact(contract["max_amplification_ratio"], "max_amplification_ratio")
        ):
            exceeded.append("max_amplification_ratio")
    if input_count in (None, 0) or error_count is None:
        unknown.append("max_rejection_ratio")
    else:
        if Fraction(error_count) / Fraction(input_count) > Fraction(
            _exact(contract["max_rejection_ratio"], "max_rejection_ratio")
        ):
            exceeded.append("max_rejection_ratio")
    decision: Literal["within_budget", "exceeded", "unknown"] = (
        "exceeded" if exceeded else "unknown" if unknown else "within_budget"
    )
    action: Literal["none", "warn", "kill_requested", "isolate_alert"] = "none"
    if exceeded:
        action = "kill_requested" if contract["on_exceed"] == "kill" else contract["on_exceed"]
    return BudgetDecision(decision, action, tuple(exceeded), tuple(unknown))
