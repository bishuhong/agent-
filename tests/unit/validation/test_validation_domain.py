from copy import deepcopy

import pytest

from etl_agent.modules.validation.domain import (
    parse_safe_hocon,
    validate_candidate,
    validate_expression,
)


@pytest.fixture
def candidate():
    return {
        "field_mappings": [
            {
                "source_fields": ["amount"],
                "target_field": "amount",
                "expression": "identity(amount)",
                "target_type": "integer",
            }
        ],
        "nodes": [
            {"node_id": "src", "kind": "source", "resource_ref": "source"},
            {"node_id": "dst", "kind": "sink", "resource_ref": "target"},
        ],
        "edges": [{"from_node_id": "src", "to_node_id": "dst"}],
        "quality_contract": {
            "rules": [],
            "error_resource": {"resource_ref": "errors"},
            "max_rejection_ratio": "0",
            "allow_empty_input": False,
        },
        "supervision_contract": {
            "max_input_records": "100",
            "max_output_bytes": "1024",
            "max_duration_ms": "60000",
            "max_amplification_ratio": "10",
            "max_rejection_ratio": "0.05",
            "on_exceed": "kill",
        },
    }


HOCON = """env { parallelism = 1 }
source { Managed { resource_ref = "source" } }
sink { Managed { resource_ref = "target" } }
"""


def gate(candidate, hocon=HOCON, source=None, target=None, **kwargs):
    return validate_candidate(
        hocon,
        candidate,
        source or {"amount": "integer"},
        target or {"amount": "integer"},
        {"source", "target", "errors"},
        {"approved-secret"},
        **kwargs,
    )


def test_known_valid_candidate_passes_without_mutating_plan(candidate):
    original = deepcopy(candidate)
    result = gate(candidate)
    assert result.passed and result.issues == ()
    assert result.rule_version == "candidate-gate-v1"
    assert candidate == original


@pytest.mark.parametrize(
    "text",
    [
        'include file("C:/Windows/win.ini")',
        'include url("http://127.0.0.1:1/secret")',
        'include required("something")',
        "env { value=${AWS_SECRET_ACCESS_KEY} }",
        'env { value="${SECRET}" }',
        'env { command="powershell -Command whoami" }',
        'source { Jdbc { query="DROP TABLE target" } }',
        'source { Jdbc { password="plain-secret" } }',
        "source {",
    ],
)
def test_unsafe_config_is_rejected_before_any_file_or_network_call(text):
    with pytest.raises(Exception):
        parse_safe_hocon(text)


def test_include_word_in_comment_or_literal_is_not_an_include_directive():
    parsed = parse_safe_hocon('env { name="include documentation" } # include file("x")')
    assert parsed["env"]["name"] == "include documentation"


@pytest.mark.parametrize(
    "expression",
    [
        '__import__("os").system("whoami")',
        "amount.__class__",
        'open("file")',
        "amount[0]",
        "[x for x in amount]",
        "SELECT amount FROM source",
        "amount ** 10000000",
        "lambda: amount",
        "unknown + 1",
    ],
)
def test_expression_grammar_rejects_executable_and_undeclared_constructs(expression):
    with pytest.raises(ValueError):
        validate_expression(expression, frozenset({"amount"}))


def test_declared_dotted_field_is_a_path_not_a_live_attribute_lookup():
    validate_expression("coalesce(source.amount, 0) + 1", frozenset({"source.amount"}))


@pytest.mark.parametrize(
    "mutation,expected",
    [
        (lambda p: p["field_mappings"][0].update(target_field="missing"), "TARGET_FIELD_UNKNOWN"),
        (
            lambda p: p["field_mappings"][0].update(source_fields=["missing"]),
            "SOURCE_FIELD_UNKNOWN",
        ),
        (lambda p: p["field_mappings"][0].update(target_type="string"), "TARGET_TYPE_MISMATCH"),
        (lambda p: p["edges"].append({"from_node_id": "dst", "to_node_id": "src"}), "DAG_CYCLE"),
        (lambda p: p["nodes"][0].update(resource_ref="external"), "RESOURCE_NOT_AUTHORIZED"),
        (lambda p: p["edges"][0].update(to_node_id="missing"), "DAG_EDGE_INVALID"),
        (
            lambda p: p["quality_contract"].update(max_rejection_ratio="2"),
            "QUALITY_CONTRACT_INVALID",
        ),
        (
            lambda p: p["supervision_contract"].update(on_exceed="ignore"),
            "SUPERVISION_CONTRACT_INVALID",
        ),
    ],
)
def test_semantic_failures_have_stable_safe_error_codes(candidate, mutation, expected):
    mutation(candidate)
    result = gate(candidate)
    assert not result.passed
    assert expected in {issue.code for issue in result.issues}
    assert all(issue.location and issue.message for issue in result.issues)


def test_unknown_schema_and_expired_profile_fail_closed(candidate):
    result = gate(candidate, source={"amount": "unknown"}, profiles_valid=False)
    assert {"SCHEMA_UNKNOWN_TYPE", "PROFILE_INVALID"}.issubset(
        {issue.code for issue in result.issues}
    )


def test_secret_bindings_must_be_approved_aliases(candidate):
    text = HOCON.replace(
        'resource_ref = "source"', 'resource_ref = "source", secret_ref="unapproved"'
    )
    result = gate(candidate, text)
    assert not result.passed
    assert "SECRET_NOT_AUTHORIZED" in {issue.code for issue in result.issues}


def test_raw_parser_error_does_not_disclose_secret_configuration(candidate):
    result = gate(candidate, 'source { password="secret-never-log"')
    assert not result.passed
    assert "secret-never-log" not in repr(result)


def test_malformed_dag_input_becomes_a_report_not_an_unhandled_typeerror(candidate):
    candidate["nodes"][0]["resource_ref"] = {}
    candidate["edges"][0]["to_node_id"] = {}
    result = gate(candidate)
    assert not result.passed
