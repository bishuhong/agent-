from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ActorContext:
    user_id: UUID
    person_ref: UUID
    project_roles: dict[UUID, frozenset[str]] = field(default_factory=dict)
    project_slots: dict[UUID, frozenset[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class RequestContext:
    request_id: str
    idempotency_key: str | None = None
    if_match: str | None = None


@dataclass(frozen=True)
class WorkerContext:
    worker_id: str
