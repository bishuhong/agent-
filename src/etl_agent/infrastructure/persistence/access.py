"""Narrow project-aware record lookup and explicit DTO projection helpers."""

from decimal import Decimal
from typing import Any, TypeVar
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from etl_agent.modules.identity_access.domain import assert_action, has_action
from etl_agent.shared.context import ActorContext
from etl_agent.shared.errors import ApplicationError

T = TypeVar("T")
P = TypeVar("P", bound=BaseModel)


def required_record(session: Session, model: type[T], resource_id: UUID) -> T:
    """Required internal association; fail explicitly rather than dereferencing None."""
    row = session.get(model, resource_id)
    if row is None:
        raise ApplicationError("STATE_CONFLICT", "必要关联记录缺失，无法继续该操作", 409)
    return row


ALIASES = {"required_slot": "required_role", "execution_status": "status", "fields": "schema_json"}
COUNT_FIELDS = {
    "file_size",
    "input_records",
    "output_records",
    "error_records",
    "valid_records",
    "bytes_processed",
    "output_bytes",
    "duration_ms",
    "latency_ms",
    "sequence_no",
    "expected_generation",
    "published_generation",
    "generation",
}


def project_entity(
    session: Session,
    model: type[T],
    resource_id: UUID,
    actor: ActorContext,
    action: str = "view",
    *,
    lock: bool = False,
) -> T:
    statement = select(model).where(model.id == resource_id)  # type: ignore[attr-defined]
    statement = statement.where(model.project_id.in_(list(actor.project_roles)))  # type: ignore[attr-defined]
    if lock:
        statement = statement.with_for_update()
    row = session.scalar(statement)
    if row is None or not has_action(actor, getattr(row, "project_id"), "view"):
        raise ApplicationError("RESOURCE_NOT_FOUND", "资源不存在或不可访问", 404)
    assert_action(actor, getattr(row, "project_id"), action)
    return row


def project_allowed(actor: ActorContext, project_id: UUID, action: str = "view") -> None:
    assert_action(actor, project_id, action)


def public_record(row: Any, schema: type[P], **overrides: Any) -> dict[str, Any]:
    """Project by DTO allowlist. Never serialize an ORM object's __dict__."""
    values: dict[str, Any] = {}
    for name in schema.model_fields:
        if name in overrides:
            values[name] = overrides[name]
            continue
        attribute = ALIASES.get(name, name)
        # MetadataProfile.fields is already a public field; FileAsset stores schema_json.
        if name == "fields" and hasattr(row, "fields"):
            attribute = "fields"
        if hasattr(row, attribute):
            value = getattr(row, attribute)
            if value is not None and (name in COUNT_FIELDS or isinstance(value, Decimal)):
                value = str(value)
            values[name] = value
    return schema.model_validate(values).model_dump(mode="json", by_alias=True)


def compare_revision(actual: int, expected: int) -> None:
    if actual != expected:
        raise ApplicationError("REVISION_MISMATCH", "对象修订已变化，请重新读取", 412)
