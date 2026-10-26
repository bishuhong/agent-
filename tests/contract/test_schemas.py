"""Contract boundaries independent of any live database or external service."""

import inspect
from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from etl_agent.api.schemas import contracts as c


def endpoint():
    return {
        "kind": "database",
        "host": "localhost",
        "port": 5432,
        "database": "etl",
        "schema": None,
    }


def connection_fields():
    return {
        "name": "source",
        "connector_type": "postgresql",
        "endpoint": endpoint(),
        "environment": "test",
    }


def budget():
    return {
        "max_input_records": "1000000",
        "max_output_bytes": "1073741824",
        "max_duration_ms": "3600000",
        "max_amplification_ratio": "10",
        "max_rejection_ratio": "0.05",
        "on_exceed": "kill",
    }


def provider():
    return {
        "provider": "qwen",
        "model": "configured-model",
        "prompt_version": "p1",
        "policy_version": "s1",
    }


@pytest.mark.parametrize("value", [0, 1.2, True, "-1", "01", "1.0", "1e3", "1,000", "", None])
def test_count_rejects_non_contract_values(value):
    with pytest.raises(ValidationError):
        TypeAdapter(c.Count).validate_python(value)


def test_count_retains_large_integer_precision():
    value = "9007199254740993123456789"
    metrics = c.RuntimeMetrics(input_records=value)
    assert metrics.model_dump(mode="json")["input_records"] == value
    assert metrics.model_dump(mode="json")["output_records"] is None


@pytest.mark.parametrize(
    "value", [1.5, 1, True, "NaN", "Infinity", "1e-4", "1,000.2", "01.0", "+1", " 1"]
)
def test_decimal_is_finite_plain_text(value):
    with pytest.raises(ValidationError):
        TypeAdapter(c.DecimalString).validate_python(value)


def test_decimal_scale_retained():
    assert TypeAdapter(c.DecimalString).validate_python("123.4500") == "123.4500"


@pytest.mark.parametrize("value", ["-0.01", "1.01", 0.5])
def test_ratio_boundaries(value):
    with pytest.raises(ValidationError):
        TypeAdapter(c.Ratio).validate_python(value)


@pytest.mark.parametrize("value", ["0", "1", "0.00000000001"])
def test_ratio_accepted(value):
    assert TypeAdapter(c.Ratio).validate_python(value) == value


@pytest.mark.parametrize(
    "value", ["2026-10-23T08:30:00", "2026-10-23", 1792734600, datetime(2026, 10, 23)]
)
def test_instant_rejects_naive_and_numeric(value):
    with pytest.raises(ValidationError):
        TypeAdapter(c.Instant).validate_python(value)


def test_instant_normalizes_offset_to_utc_z():
    metrics = c.RuntimeMetrics(observed_at="2026-10-23T08:30:00+08:00")
    assert metrics.model_dump(mode="json")["observed_at"] == "2026-10-23T00:30:00Z"


def test_user_projection_cannot_accept_internal_identity():
    data = {
        "id": str(uuid4()),
        "created_at": "2026-10-23T00:00:00Z",
        "updated_at": "2026-10-23T00:00:00Z",
        "revision": 1,
        "username": "maker",
        "display_name": "Maker",
        "email": None,
        "status": "enabled",
    }
    user = c.UserResponse.model_validate(data)
    assert "subject_ref" not in user.model_dump()
    for key in ("subject_ref", "person_ref", "password_hash"):
        with pytest.raises(ValidationError):
            c.UserResponse.model_validate({**data, key: "internal"})


def test_login_preserves_password_and_masks_representation():
    raw = "  a-Strong-password  "
    request = c.LocalLoginRequest(username="Maker_A", password=raw)
    assert request.username == "maker_a"
    assert request.password.get_secret_value() == raw
    assert raw not in repr(request)
    assert raw not in request.model_dump_json()


@pytest.mark.parametrize("value", ["short", "x" * 129, 123456789012, True])
def test_login_password_invalid(value):
    with pytest.raises(ValidationError):
        c.LocalLoginRequest(username="maker", password=value)


def test_credentials_discriminated_and_redacted():
    raw = "secret-only-provider-may-read"
    request = c.ConnectionCreateRequest(
        project_id=uuid4(),
        **connection_fields(),
        credentials={"mode": "inline_secret", "secret_values": {"password": raw}},
    )
    assert raw not in repr(request)
    assert raw not in request.model_dump_json()
    assert isinstance(request.credentials, c.InlineCredentialInput)
    assert request.credentials.reveal_for_secret_provider() == {"password": raw}
    with pytest.raises(ValidationError):
        TypeAdapter(c.CredentialInput).validate_python(
            {
                "mode": "managed_ref",
                "secret_ref_id": str(uuid4()),
                "secret_values": {"password": raw},
            }
        )


def test_put_credentials_missing_differs_from_null():
    request = c.ConnectionReplaceRequest(**connection_fields())
    assert "credentials" not in request.model_fields_set
    assert "credentials" not in request.model_dump(exclude_unset=True)
    with pytest.raises(ValidationError):
        c.ConnectionReplaceRequest(**connection_fields(), credentials=None)


def test_endpoint_alias_survives_json_dump():
    request = c.ConnectionCreateRequest(project_id=uuid4(), **connection_fields())
    assert request.model_dump(mode="json")["endpoint"]["schema"] is None
    assert "schema_name" not in request.model_dump(mode="json")["endpoint"]


@pytest.mark.parametrize(
    "change",
    [
        {"port": "5432"},
        {"port": True},
        {"port": 0},
        {"port": 65536},
        {"host": "postgres://user:password@host"},
        {"host": "host:5432"},
    ],
)
def test_database_endpoint_rejects_invalid_address_or_port(change):
    with pytest.raises(ValidationError):
        c.DatabaseEndpoint(**{**endpoint(), **change})


@pytest.mark.parametrize(
    "base_url", ["http://u:pw@host", "https://host?token=x", "ftp://host", "https://host/#secret"]
)
def test_endpoint_url_never_contains_credentials(base_url):
    with pytest.raises(ValidationError):
        c.HttpEndpoint(kind="http", base_url=base_url)


def test_connector_endpoint_mismatch_rejected():
    with pytest.raises(ValidationError):
        c.ConnectionCreateRequest(
            project_id=uuid4(), **{**connection_fields(), "connector_type": "s3"}
        )


@pytest.mark.parametrize("sample_limit", [-1, 101, "100", True, None])
def test_profile_sample_bounds(sample_limit):
    with pytest.raises(ValidationError):
        c.ProfileCreateRequest(resource_names=["records"], sample_limit=sample_limit)


def test_profile_zero_samples_and_default():
    assert c.ProfileCreateRequest(resource_names=["records"]).sample_limit == 100
    assert c.ProfileCreateRequest(resource_names=["records"], sample_limit=0).sample_limit == 0


def test_answers_nonempty_and_unique():
    with pytest.raises(ValidationError):
        c.ClarificationAnswerRequest(answers=[])
    with pytest.raises(ValidationError):
        c.ClarificationAnswerRequest(answers=[{"question_key": "q", "answer": "yes"}] * 2)


def test_prepare_purpose_bindings():
    default = c.PrepareRequest(environment="test", supervision_contract=budget())
    assert default.purpose == c.PreparationPurpose.execute_etl
    assert default.evaluation_binding is None
    with pytest.raises(ValidationError):
        c.PrepareRequest(environment="test", purpose="rollback", supervision_contract=budget())
    with pytest.raises(ValidationError):
        c.PrepareRequest(
            environment="test", purpose="run_l2_benchmark", supervision_contract=budget()
        )
    valid = c.PrepareRequest(
        environment="isolated",
        purpose="run_l2_benchmark",
        supervision_contract=budget(),
        evaluation_binding={
            "dataset_id": str(uuid4()),
            "dataset_digest": "a" * 64,
            "provider_binding": provider(),
        },
    )
    assert valid.evaluation_binding is not None
    with pytest.raises(ValidationError):
        c.PrepareRequest(
            environment="test",
            purpose="execute_etl",
            supervision_contract=budget(),
            evaluation_binding=valid.evaluation_binding,
        )


@pytest.mark.parametrize("level,preparation", [("L0", "id"), ("L1", "id"), ("L2", None)])
def test_benchmark_level_authorization(level, preparation):
    with pytest.raises(ValidationError):
        c.BenchmarkRunRequest(
            project_id=uuid4(),
            dataset_id=uuid4(),
            level=level,
            provider_binding=provider(),
            preparation_id=uuid4() if preparation else None,
        )


def test_nullable_required_rollback_publication():
    with pytest.raises(ValidationError):
        c.RollbackRequest(expected_revision=1, preparation_id=uuid4())
    assert (
        c.RollbackRequest(
            expected_revision=1, preparation_id=uuid4(), publication_id=None
        ).publication_id
        is None
    )


def test_runtime_metrics_unknown_is_not_zero_and_all_fields_present():
    data = c.RuntimeMetrics().model_dump(mode="json")
    assert len(data) == 11
    assert all(value is None for value in data.values())


def test_quality_rule_stage_not_arbitrary():
    with pytest.raises(ValidationError):
        c.QualityRule(
            rule_id="r",
            field_name="email",
            kind="mask_email",
            stage="validate",
            error_code="BAD_EMAIL",
        )


def test_confirmed_diagnosis_needs_evidence():
    with pytest.raises(ValidationError):
        c.DiagnosticFinding(certainty="confirmed", explanation="No evidence", evidence=[])
    c.DiagnosticFinding(certainty="insufficient", explanation="Metrics missing", evidence=[])


def test_schedule_mutually_exclusive_and_valid_timezone():
    common = {
        "pipeline_id": uuid4(),
        "version_id": uuid4(),
        "kind": "once",
        "timezone": "Asia/Shanghai",
        "run_at": "2026-10-23T00:00:00Z",
        "cron_expression": None,
    }
    assert not c.ScheduleDefinitionRequest(**common).enabled
    with pytest.raises(ValidationError):
        c.ScheduleDefinitionRequest(**{**common, "cron_expression": "* * * * *"})
    with pytest.raises(ValidationError):
        c.ScheduleDefinitionRequest(**{**common, "timezone": "not/a-zone"})


def test_cursor_no_null_and_stable_defaults():
    assert c.CursorQuery().limit == 20
    for value in (None, ""):
        with pytest.raises(ValidationError):
            c.CursorQuery(cursor=value)
    with pytest.raises(ValidationError):
        c.CursorPage[int](items=[], next_cursor=None, has_more=True, request_id="req")


def test_grant_union():
    c.MemberGrantRequest(user_id=uuid4(), role="maker", expected_revision=1)
    with pytest.raises(ValidationError):
        c.MemberGrantRequest(user_id=uuid4(), role="maker", role_slot="data", expected_revision=1)


def test_no_schema_accepts_untracked_fields_and_all_are_json_schema_compatible():
    models = {
        model
        for model in vars(c).values()
        if inspect.isclass(model) and issubclass(model, c.ContractModel)
    }
    assert len(models) >= 120
    for model in models:
        schema = model.model_json_schema()
        assert schema.get("additionalProperties") is False, model.__name__
        for field in (
            "provider_locator",
            "file_path",
            "checkpoint_ref",
            "protected_record_ref",
            "person_ref",
            "subject_ref",
        ):
            if model.__name__.endswith("Response"):
                assert field not in schema.get("properties", {}), (model.__name__, field)


def test_error_has_no_sensitive_input_field():
    error = c.SafeError(code="VALIDATION_FAILED", message="Invalid request")
    data = c.ErrorEnvelope(error=error, request_id="request").model_dump(mode="json")
    assert set(data["error"]) == {"code", "message", "field_errors", "retryable"}
    with pytest.raises(ValidationError):
        c.FieldError(path="password", code="invalid", message="Invalid", input="secret")


def test_closed_enums_and_readonly_input_rejection():
    with pytest.raises(ValidationError):
        c.PipelineCreateRequest(project_id=uuid4(), name="a", code="a", actor_id=uuid4())
    with pytest.raises(ValidationError):
        c.GenerationRequest(
            prompt="move",
            source_profile_ids=[uuid4()],
            target_profile_id=uuid4(),
            provider="arbitrary_url",
        )
    assert c.ActionCode.create_connection.value == "create_connection"
    assert c.ActionCode.manage_schedule.value == "manage_schedule"
    assert c.ActionCode.revoke_evolution.value == "revoke_evolution"


def test_json_nan_not_accepted_in_samples():
    with pytest.raises(ValidationError):
        c.MaskedSample(values={"value": float("nan")}, masked_fields=[])
