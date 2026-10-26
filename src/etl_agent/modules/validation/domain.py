"""Fail-closed, deterministic candidate validation, with no plugin execution."""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    location: str
    severity: str
    message: str
    repairable: bool = False


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    issues: tuple[ValidationIssue, ...]
    rule_version: str = "candidate-gate-v1"


# These are parser capabilities, not evidence of a deployed connector capability.
EXPRESSION_FUNCTIONS = frozenset(
    {
        "identity",
        "cast",
        "concat",
        "coalesce",
        "lower",
        "upper",
        "trim",
        "mask_email",
    }
)
LOGICAL_TYPES = frozenset(
    {
        "string",
        "integer",
        "decimal",
        "boolean",
        "date",
        "timestamp",
        "binary",
        "object",
        "array",
    }
)
_FORBIDDEN_HOCON_KEYS = frozenset(
    {
        "command",
        "shell",
        "script",
        "exec",
        "sql",
        "query",
        "password",
        "passwd",
        "access_key",
        "secret_key",
        "token",
        "private_key",
    }
)


def _check_hocon_lexically(text: str) -> None:
    """Inspect tokens BEFORE pyhocon can encounter an include directive.

    A regex over the complete document is insufficient: comments and quoted
    strings have different grammar. No filesystem/network parser entrypoint is
    invoked, and parse_string(resolve=False) disables substitution resolution.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("HOCON must be nonempty text")
    if len(text.encode("utf-8")) > 1024 * 1024:
        raise ValueError("HOCON exceeds the parser resource limit")
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "#" or text.startswith("//", index):
            end = text.find("\n", index)
            index = length if end < 0 else end + 1
            continue
        if text.startswith("${", index):
            raise ValueError("HOCON substitutions are forbidden")
        if char == '"':
            triple = text.startswith('"""', index)
            marker = '"""' if triple else '"'
            index += len(marker)
            while index < length:
                if text.startswith("${", index):
                    raise ValueError("HOCON substitutions are forbidden")
                if text.startswith(marker, index):
                    index += len(marker)
                    break
                if not triple and text[index] == "\\":
                    index += 2
                else:
                    index += 1
            else:
                raise ValueError("HOCON string is unterminated")
            continue
        if char.isalpha() or char == "_":
            end = index + 1
            while end < length and (text[end].isalnum() or text[end] in "_-."):
                end += 1
            if text[index:end] == "include":
                raise ValueError("HOCON include directives are forbidden")
            index = end
            continue
        index += 1


def parse_safe_hocon(text: str) -> Mapping[str, Any]:
    _check_hocon_lexically(text)
    from pyhocon import ConfigFactory

    parsed = ConfigFactory.parse_string(text, resolve=False)
    if not isinstance(parsed, Mapping):
        raise ValueError("HOCON root must be an object")

    def check(value: Any, depth: int = 0) -> None:
        if depth > 64:
            raise ValueError("HOCON exceeds the nesting resource limit")
        if isinstance(value, Mapping):
            for key, child in value.items():
                if str(key).casefold() in _FORBIDDEN_HOCON_KEYS:
                    raise ValueError("HOCON contains a prohibited executable or credential field")
                check(child, depth + 1)
        elif isinstance(value, (list, tuple)):
            for child in value:
                check(child, depth + 1)
        elif isinstance(value, str) and "${" in value:
            raise ValueError("HOCON substitutions are forbidden")
        elif not isinstance(value, (str, bool, int, float, type(None))):
            raise ValueError("HOCON contains unresolved or unsupported configuration values")

    check(parsed)
    return parsed


def _field_reference(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _field_reference(node.value)
        if parent is not None and not node.attr.startswith("_"):
            return parent + "." + node.attr
    return None


def validate_expression(expression: str, declared_fields: Set[str]) -> None:
    """Validate a versioned expression grammar; never use eval/compile.

    SQL, shell, attribute method calls, comprehensions and executable Python
    constructs are rejected. Dotted identifiers are field paths only and must
    exactly match the supplied schema; they are never resolved on live objects.
    """
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 4096:
        raise ValueError("expression must be nonempty and within the resource limit")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, RecursionError) as error:
        raise ValueError("expression is outside the restricted grammar") from error
    visited = 0

    def check(node: ast.AST, depth: int = 0) -> None:
        nonlocal visited
        visited += 1
        if visited > 256 or depth > 32:
            raise ValueError("expression exceeds the parser resource limit")
        reference = _field_reference(node)
        if reference is not None:
            if reference not in declared_fields or any(
                p.startswith("_") for p in reference.split(".")
            ):
                raise ValueError("expression references an undeclared field")
        elif isinstance(node, ast.Constant):
            if not isinstance(node.value, (str, int, bool, type(None))) or isinstance(
                node.value, complex
            ):
                raise ValueError("expression literal is not permitted")
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in EXPRESSION_FUNCTIONS:
                raise ValueError("expression function is not allowlisted")
            if node.keywords or not 1 <= len(node.args) <= 32:
                raise ValueError("expression function arguments are not permitted")
            for argument in node.args:
                check(argument, depth + 1)
        elif isinstance(node, ast.BinOp) and isinstance(
            node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod)
        ):
            check(node.left, depth + 1)
            check(node.right, depth + 1)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            check(node.operand, depth + 1)
        else:
            raise ValueError("expression contains an unsupported operation")

    check(tree.body)


def validate_candidate(
    hocon: str,
    plan: Mapping[str, Any],
    source_schema: Mapping[str, Any],
    target_schema: Mapping[str, Any],
    allowed_resources: Set[str],
    allowed_secret_refs: Set[str],
    *,
    profiles_valid: bool = True,
) -> ValidationResult:
    """Validate independent gates and accumulate stable safe error locations.

    Caller must establish profile ownership and expiry before setting
    profiles_valid=True. allowed_resources/secret_refs are server-derived.
    Passing does not assert a live connector version supports this candidate.
    """
    issues: list[ValidationIssue] = []

    def add(code: str, location: str, message: str, repairable: bool = False) -> None:
        issues.append(ValidationIssue(code, location, "error", message, repairable))

    if not profiles_valid:
        add("PROFILE_INVALID", "plan.source_profile_ids", "Profile is expired or not authorized")
    config: Mapping[str, Any] | None = None
    try:
        config = parse_safe_hocon(hocon)
        for section in ("env", "source", "sink"):
            if section not in config or not config[section]:
                add(
                    "HOCON_SECTION_REQUIRED",
                    f"hocon.{section}",
                    "Required engine section is missing",
                    True,
                )
        for key, value in config.items():
            if str(key) not in {"env", "source", "transform", "sink"}:
                add("HOCON_SECTION_UNKNOWN", "hocon", "Unrecognized root engine section")
            if not isinstance(value, (Mapping, list, tuple)):
                add(
                    "HOCON_SECTION_INVALID",
                    f"hocon.{key}",
                    "Engine section must be an object or array",
                    True,
                )
    except ImportError:
        add("DEPENDENCY_UNAVAILABLE", "hocon", "HOCON parser is unavailable")
    except Exception:
        # Parser messages can contain configuration values and physical paths.
        add("HOCON_INVALID", "hocon", "HOCON is unsafe or cannot be parsed", True)

    if not source_schema or not target_schema:
        add("SCHEMA_REQUIRED", "profiles", "Known source and target schemas are required")

    def logical_type(value: Any) -> str | None:
        return value.get("logical_type") if isinstance(value, Mapping) else value

    for group, schema in (("source", source_schema), ("target", target_schema)):
        for name, definition in schema.items():
            field_type = logical_type(definition)
            if not isinstance(field_type, str) or field_type not in LOGICAL_TYPES:
                add(
                    "SCHEMA_UNKNOWN_TYPE", f"profiles.{group}.{name}", "Field type must be explicit"
                )
            if isinstance(definition, Mapping):
                if definition.get("nullable") is None:
                    add(
                        "SCHEMA_NULLABILITY_UNKNOWN",
                        f"profiles.{group}.{name}",
                        "Field nullability must be explicit",
                    )
                if definition.get("logical_type") == "decimal":
                    precision, scale = definition.get("precision"), definition.get("scale")
                    if (
                        type(precision) is not int
                        or type(scale) is not int
                        or not 1 <= precision
                        or not 0 <= scale <= precision
                    ):
                        add(
                            "SCHEMA_DECIMAL_INVALID",
                            f"profiles.{group}.{name}",
                            "Decimal precision and scale are required",
                        )
    mappings = plan.get("field_mappings")
    mapped: set[str] = set()
    if not isinstance(mappings, Sequence) or isinstance(mappings, (str, bytes)) or not mappings:
        add("MAPPING_REQUIRED", "plan.field_mappings", "Target field mappings are required", True)
        mappings = ()
    for index, mapping in enumerate(mappings):
        location = f"plan.field_mappings[{index}]"
        if not isinstance(mapping, Mapping):
            add("MAPPING_INVALID", location, "Mapping must be an object", True)
            continue
        target = mapping.get("target_field")
        if not isinstance(target, str) or target not in target_schema:
            add(
                "TARGET_FIELD_UNKNOWN",
                location + ".target_field",
                "Target field is not in the known schema",
                True,
            )
        elif target in mapped:
            add(
                "TARGET_MAPPING_DUPLICATED",
                location + ".target_field",
                "Target field has duplicate mappings",
                True,
            )
        else:
            mapped.add(target)
            if mapping.get("target_type") != logical_type(target_schema[target]):
                add(
                    "TARGET_TYPE_MISMATCH",
                    location + ".target_type",
                    "Mapping type does not match target schema",
                    True,
                )
        fields = mapping.get("source_fields")
        if not isinstance(fields, (list, tuple)) or any(
            not isinstance(field, str) or field not in source_schema for field in fields
        ):
            add(
                "SOURCE_FIELD_UNKNOWN",
                location + ".source_fields",
                "Source fields must exist in the frozen schema",
                True,
            )
            fields = ()
        try:
            expression = mapping.get("expression")
            if not isinstance(expression, str):
                raise ValueError("expression must be text")
            validate_expression(expression, frozenset(fields))
        except ValueError:
            add(
                "EXPRESSION_NOT_ALLOWED",
                location + ".expression",
                "Expression is outside the restricted field grammar",
                True,
            )
    for name in target_schema:
        if name not in mapped:
            add(
                "TARGET_FIELD_UNMAPPED",
                "plan.field_mappings",
                "A required target field is unmapped",
                True,
            )

    nodes = plan.get("nodes")
    edges = plan.get("edges")
    adjacency: dict[str, list[str]] = {}
    if not isinstance(nodes, (list, tuple)) or not nodes:
        add("DAG_NODES_REQUIRED", "plan.nodes", "DAG requires typed nodes", True)
        nodes = ()
    for index, node in enumerate(nodes):
        location = f"plan.nodes[{index}]"
        if not isinstance(node, Mapping):
            add("DAG_NODE_INVALID", location, "DAG node must be an object", True)
            continue
        node_id = node.get("node_id")
        if not isinstance(node_id, str) or not node_id or node_id in adjacency:
            add(
                "DAG_NODE_INVALID",
                location,
                "DAG node identifiers must be nonempty and unique",
                True,
            )
            continue
        adjacency[node_id] = []
        kind = node.get("kind")
        if not isinstance(kind, str) or kind not in {"source", "transform", "sink", "error_sink"}:
            add("DAG_NODE_KIND_INVALID", location + ".kind", "Unknown DAG node kind", True)
        resource = node.get("resource_ref")
        if kind != "transform" and (
            not isinstance(resource, str) or resource not in allowed_resources
        ):
            add(
                "RESOURCE_NOT_AUTHORIZED",
                location + ".resource_ref",
                "Resource is outside the managed scope",
            )
        if (
            kind == "transform"
            and resource is not None
            and (not isinstance(resource, str) or resource not in allowed_resources)
        ):
            add(
                "RESOURCE_NOT_AUTHORIZED",
                location + ".resource_ref",
                "Resource is outside the managed scope",
            )
    if not isinstance(edges, (list, tuple)):
        add("DAG_EDGES_INVALID", "plan.edges", "DAG edges must be an array", True)
        edges = ()
    for index, edge in enumerate(edges):
        source = edge.get("from_node_id") if isinstance(edge, Mapping) else None
        target = edge.get("to_node_id") if isinstance(edge, Mapping) else None
        if (
            not isinstance(source, str)
            or not isinstance(target, str)
            or source not in adjacency
            or target not in adjacency
            or source == target
        ):
            add(
                "DAG_EDGE_INVALID",
                f"plan.edges[{index}]",
                "DAG edge endpoints must be distinct known nodes",
                True,
            )
        else:
            adjacency[source].append(target)
    indegree = {node: 0 for node in adjacency}
    for targets in adjacency.values():
        for target in targets:
            indegree[target] += 1
    ready = [node for node, degree in indegree.items() if degree == 0]
    visited = 0
    while ready:
        node = ready.pop()
        visited += 1
        for target in adjacency[node]:
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
    if visited != len(adjacency):
        add("DAG_CYCLE", "plan.edges", "DAG must be acyclic", True)

    # Secret references live in HOCON binding fields, not in a new EtlPlan API
    # property. Validate exact server-approved aliases before materialization.
    def check_bindings(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                location = path + "." + str(key)
                if key == "secret_ref" and (
                    not isinstance(child, str) or child not in allowed_secret_refs
                ):
                    add(
                        "SECRET_NOT_AUTHORIZED",
                        location,
                        "Secret reference is outside the managed scope",
                    )
                elif key == "resource_ref" and (
                    not isinstance(child, str) or child not in allowed_resources
                ):
                    add(
                        "RESOURCE_NOT_AUTHORIZED", location, "Resource is outside the managed scope"
                    )
                check_bindings(child, location)
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                check_bindings(child, f"{path}[{index}]")

    if config is not None:
        check_bindings(config, "hocon")
    from etl_agent.modules.quality.domain import compile_quality_contract
    from etl_agent.modules.supervision.domain import validate_budget

    try:
        compile_quality_contract(plan.get("quality_contract", {}), target_schema)
    except (ValueError, TypeError, KeyError):
        add(
            "QUALITY_CONTRACT_INVALID",
            "plan.quality_contract",
            "Quality contract is invalid or incomplete",
            True,
        )
    try:
        validate_budget(plan.get("supervision_contract", {}))
    except (ValueError, TypeError, KeyError):
        add(
            "SUPERVISION_CONTRACT_INVALID",
            "plan.supervision_contract",
            "Runtime budgets must be explicit and valid",
            True,
        )
    return ValidationResult(not issues, tuple(issues))
