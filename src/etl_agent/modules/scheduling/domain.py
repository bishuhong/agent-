"""Timezone-explicit schedule calculations; due intents never execute ETL."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class ScheduleDefinition:
    pipeline_id: str
    version_id: str
    kind: Literal["once", "recurring"]
    timezone: str
    run_at: datetime | None
    cron_expression: str | None
    enabled: bool


@dataclass(frozen=True)
class TriggerDecision:
    action: Literal["prepare", "skipped"]
    reason: str
    trigger_at: datetime


def _utc(value: object, name: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"{name} must be an aware ISO instant") from error
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include an explicit timezone")
    return value.astimezone(UTC)


def _validate_cron(expression: object) -> str:
    if not isinstance(expression, str) or len(expression) > 256:
        raise ValueError("cron_expression must be a bounded five-field string")
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("cron_expression must have exactly five fields")
    # Restrict extended croniter features: no seconds/year, @aliases, L, #, ?, or
    # hash/random schedules. Standard month/day names are explicitly accepted.
    month_names = {
        "JAN",
        "FEB",
        "MAR",
        "APR",
        "MAY",
        "JUN",
        "JUL",
        "AUG",
        "SEP",
        "OCT",
        "NOV",
        "DEC",
    }
    weekday_names = {"SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"}
    for index, field in enumerate(fields):
        word = ""
        for char in field.upper() + ",":
            if "A" <= char <= "Z":
                word += char
                continue
            if word:
                allowed_names = (
                    month_names if index == 3 else weekday_names if index == 4 else set()
                )
                if word not in allowed_names:
                    raise ValueError("cron uses an unsupported extension or field name")
                word = ""
            if char not in "0123456789*,-/":
                raise ValueError("cron uses an unsupported extension")
    from croniter import croniter

    if not croniter.is_valid(expression):
        raise ValueError("cron_expression is invalid")
    return " ".join(fields)


def validate_schedule(definition: Mapping[str, Any], now: datetime) -> ScheduleDefinition:
    from uuid import UUID

    current = _utc(now, "now")
    for key in ("pipeline_id", "version_id", "kind", "timezone"):
        if not isinstance(definition.get(key), str) or not definition[key]:
            raise ValueError(f"{key} is required")
    for key in ("pipeline_id", "version_id"):
        try:
            if str(UUID(definition[key])) != definition[key]:
                raise ValueError("not canonical")
        except ValueError as error:
            raise ValueError(f"{key} must be a canonical UUID") from error
    try:
        ZoneInfo(definition["timezone"])
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("timezone must be a valid IANA zone") from error
    enabled = definition.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be boolean")
    kind = definition["kind"]
    run_at = None
    cron = None
    if kind == "once":
        if definition.get("cron_expression") is not None:
            raise ValueError("once schedules require cron_expression=null")
        run_at = _utc(definition.get("run_at"), "run_at")
        if run_at <= current:
            raise ValueError("a new one-time trigger must be in the future")
    elif kind == "recurring":
        if definition.get("run_at") is not None:
            raise ValueError("recurring schedules require run_at=null")
        cron = _validate_cron(definition.get("cron_expression"))
    else:
        raise ValueError("kind must be once or recurring")
    return ScheduleDefinition(
        definition["pipeline_id"],
        definition["version_id"],
        kind,
        definition["timezone"],
        run_at,
        cron,
        enabled,
    )


def _first_local_occurrence(local: datetime, zone: ZoneInfo) -> datetime | None:
    """Return the first real occurrence; nonexistent local minutes are skipped."""
    candidates: list[datetime] = []
    for fold in (0, 1):
        aware = local.replace(tzinfo=zone, fold=fold)
        utc = aware.astimezone(UTC)
        # The round trip detects spring-forward nonexistent wall clock times.
        if utc.astimezone(zone).replace(tzinfo=None) == local:
            candidates.append(utc)
    return min(candidates) if candidates else None


def next_trigger(definition: ScheduleDefinition, after: datetime) -> datetime | None:
    """Find the next future occurrence, never backfill historical trigger points.

    Repeated DST wall minutes choose their first UTC occurrence exactly once;
    the persistence layer must additionally enforce (schedule_id, trigger_at).
    """
    current = _utc(after, "after")
    if not definition.enabled:
        return None
    if definition.kind == "once":
        return (
            definition.run_at
            if definition.run_at is not None and definition.run_at > current
            else None
        )
    from croniter import CroniterBadDateError, croniter

    if definition.cron_expression is None:
        raise ValueError("recurring schedules require a cron expression")
    expression = _validate_cron(definition.cron_expression)
    zone = ZoneInfo(definition.timezone)
    local_after = current.astimezone(zone).replace(tzinfo=None)
    iterator = croniter(expression, local_after, max_years_between_matches=8)
    for _ in range(600_000):
        try:
            local = iterator.get_next(datetime)
        except CroniterBadDateError:
            return None
        occurrence = _first_local_occurrence(local, zone)
        if occurrence is not None and occurrence > current:
            return occurrence
    raise ValueError("cron exceeds the bounded schedule search limit")


def evaluate_due(
    definition: ScheduleDefinition,
    trigger_at: datetime,
    now: datetime,
    *,
    has_open_intent: bool,
    already_triggered: bool,
    max_lateness_seconds: int = 60,
) -> TriggerDecision:
    """Decide whether to create this due preparation, never a run or capability.

    max_lateness_seconds is an explicit scheduler delivery tolerance, not a
    backfill window. Persist the unique trigger record in the same transaction
    as preparation creation; recheck enabled and overlap while holding its lock.
    """
    if type(max_lateness_seconds) is not int or max_lateness_seconds < 0:
        raise ValueError("max_lateness_seconds must be an explicit nonnegative integer")
    trigger = _utc(trigger_at, "trigger_at")
    current = _utc(now, "now")
    reason = "DUE_PREPARATION_REQUIRED"
    if not definition.enabled:
        reason = "DISABLED"
    elif already_triggered:
        reason = "DUPLICATE_TRIGGER"
    elif trigger > current:
        reason = "NOT_DUE"
    elif current - trigger > timedelta(seconds=max_lateness_seconds):
        reason = "MISSED_TRIGGER"
    elif has_open_intent:
        reason = "OVERLAPPING_INTENT"
    elif definition.kind == "once" and trigger != definition.run_at:
        reason = "TRIGGER_MISMATCH"
    elif definition.kind == "recurring":
        from croniter import croniter

        zone = ZoneInfo(definition.timezone)
        local = trigger.astimezone(zone).replace(tzinfo=None)
        if (
            trigger.second != 0
            or trigger.microsecond != 0
            or not croniter.match(definition.cron_expression, local)
            or _first_local_occurrence(local, zone) != trigger
        ):
            reason = "TRIGGER_MISMATCH"
    return TriggerDecision(
        "prepare" if reason == "DUE_PREPARATION_REQUIRED" else "skipped", reason, trigger
    )
