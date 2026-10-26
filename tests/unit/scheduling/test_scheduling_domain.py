from datetime import UTC, datetime, timedelta

import pytest

from etl_agent.modules.scheduling.domain import evaluate_due, next_trigger, validate_schedule

NOW = datetime(2026, 10, 23, 8, tzinfo=UTC)


def recurring(**kwargs):
    payload = {
        "pipeline_id": "00000000-0000-0000-0000-000000000001",
        "version_id": "00000000-0000-0000-0000-000000000002",
        "kind": "recurring",
        "timezone": "Asia/Shanghai",
        "run_at": None,
        "cron_expression": "0 9 * * *",
        "enabled": True,
    }
    payload.update(kwargs)
    return validate_schedule(payload, NOW)


def test_once_requires_future_aware_instant_and_never_repeats():
    schedule = recurring(kind="once", cron_expression=None, run_at=NOW + timedelta(hours=1))
    assert next_trigger(schedule, NOW) == NOW + timedelta(hours=1)
    assert next_trigger(schedule, NOW + timedelta(hours=1)) is None
    with pytest.raises(ValueError):
        recurring(kind="once", cron_expression=None, run_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError):
        recurring(kind="once", cron_expression=None, run_at=NOW)


def test_recurring_uses_iana_timezone_and_disabled_has_no_next_trigger():
    assert next_trigger(recurring(), NOW) == datetime(2026, 10, 24, 1, tzinfo=UTC)
    assert next_trigger(recurring(enabled=False), NOW) is None


@pytest.mark.parametrize(
    "change",
    [
        {"timezone": "unknown/city"},
        {"cron_expression": "* * * * * *"},
        {"cron_expression": "@daily"},
        {"cron_expression": "0 25 * * *"},
        {"cron_expression": "0 1 L * *"},
        {"cron_expression": "0 1 * * MON#2"},
        {"run_at": NOW},
        {"enabled": "false"},
        {"pipeline_id": "not-a-uuid"},
    ],
)
def test_schedule_rejects_invalid_or_extended_contracts(change):
    with pytest.raises(ValueError):
        recurring(**change)


def test_standard_cron_names_remain_supported():
    assert recurring(cron_expression="0 9 * JAN MON").cron_expression == "0 9 * JAN MON"


def test_dst_nonexistent_wall_time_is_skipped():
    schedule = recurring(timezone="America/New_York", cron_expression="30 2 * * *")
    # New York skips 02:30 on 2026-03-08.
    before = datetime(2026, 3, 8, 5, tzinfo=UTC)
    assert next_trigger(schedule, before) == datetime(2026, 3, 9, 6, 30, tzinfo=UTC)


def test_dst_repeated_wall_time_triggers_only_first_occurrence():
    schedule = recurring(timezone="America/New_York", cron_expression="30 1 * * *")
    before = datetime(2026, 11, 1, 4, tzinfo=UTC)
    first = datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    repeated = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)
    assert next_trigger(schedule, before) == first
    assert next_trigger(schedule, first) == datetime(2026, 11, 2, 6, 30, tzinfo=UTC)
    assert (
        evaluate_due(
            schedule, repeated, repeated, has_open_intent=False, already_triggered=False
        ).reason
        == "TRIGGER_MISMATCH"
    )


def test_due_generates_only_preparation_and_skips_duplicates_overlaps_and_missed():
    schedule = recurring()
    trigger = datetime(2026, 10, 24, 1, tzinfo=UTC)
    result = evaluate_due(
        schedule, trigger, trigger, has_open_intent=False, already_triggered=False
    )
    assert result.action == "prepare"
    assert result.reason == "DUE_PREPARATION_REQUIRED"
    assert (
        evaluate_due(
            schedule, trigger, trigger, has_open_intent=False, already_triggered=True
        ).reason
        == "DUPLICATE_TRIGGER"
    )
    assert (
        evaluate_due(
            schedule, trigger, trigger, has_open_intent=True, already_triggered=False
        ).reason
        == "OVERLAPPING_INTENT"
    )
    late = trigger + timedelta(seconds=61)
    assert (
        evaluate_due(schedule, trigger, late, has_open_intent=False, already_triggered=False).reason
        == "MISSED_TRIGGER"
    )


def test_due_not_due_and_non_schedule_minutes_are_rejected():
    schedule = recurring()
    trigger = datetime(2026, 10, 24, 1, tzinfo=UTC)
    assert (
        evaluate_due(
            schedule,
            trigger,
            trigger - timedelta(seconds=1),
            has_open_intent=False,
            already_triggered=False,
        ).reason
        == "NOT_DUE"
    )
    invalid = trigger + timedelta(minutes=1)
    assert (
        evaluate_due(
            schedule, invalid, invalid, has_open_intent=False, already_triggered=False
        ).reason
        == "TRIGGER_MISMATCH"
    )
