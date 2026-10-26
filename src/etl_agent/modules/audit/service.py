"""Project-serialized, append-only evidence ledger."""

import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from etl_agent.infrastructure.persistence import models as m
from etl_agent.shared.canonical import canonical_bytes, digest
from etl_agent.shared.context import ActorContext, utcnow
from etl_agent.shared.errors import ApplicationError, unavailable
from etl_agent.shared.security import CapabilitySigner


def event_content(event: m.AuditEvent) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": event.id,
        "project_id": event.project_id,
        "actor_id": event.actor_id,
        "actor_kind": event.actor_kind,
        "event_type": event.event_type,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "payload_digest": event.payload_digest,
        "prev_event_hash": event.prev_event_hash,
        "sequence_no": event.sequence_no,
        "created_at": event.created_at,
    }


class EvidenceLedger:
    def append(
        self,
        session: Session,
        project_id: UUID,
        actor: ActorContext | None,
        event_type: str,
        resource_type: str,
        resource_id: UUID | str,
        facts: dict[str, Any] | None = None,
    ) -> m.AuditEvent:
        # A real project-row lock serializes all appends, including concurrent UoWs.
        project = session.scalar(
            select(m.Project).where(m.Project.id == project_id).with_for_update()
        )
        if project is None:
            raise ApplicationError("RESOURCE_NOT_FOUND", "项目不存在", 404)
        previous = session.scalar(
            select(m.AuditEvent)
            .where(m.AuditEvent.project_id == project_id)
            .order_by(m.AuditEvent.sequence_no.desc())
            .limit(1)
        )
        event = m.AuditEvent(
            id=uuid4(),
            project_id=project_id,
            actor_id=actor.user_id if actor else None,
            actor_kind="user" if actor else "system",
            event_type=event_type,
            resource_type=resource_type,
            resource_id=str(resource_id),
            payload_digest=digest(facts or {}),
            prev_event_hash=previous.event_hash if previous else None,
            sequence_no=previous.sequence_no + 1 if previous else 1,
            created_at=utcnow(),
        )
        event.event_hash = digest(event_content(event))
        session.add(event)
        session.flush()
        return event

    def verify(
        self,
        session: Session,
        project_id: UUID,
        signer: CapabilitySigner | None = None,
        anchor_root: Path | None = None,
    ) -> dict[str, Any]:
        events = session.scalars(
            select(m.AuditEvent)
            .where(m.AuditEvent.project_id == project_id)
            .order_by(m.AuditEvent.sequence_no)
        ).all()
        previous = None
        expected_sequence = 1
        valid = True
        for event in events:
            if (
                event.sequence_no != expected_sequence
                or event.prev_event_hash != previous
                or event.event_hash != digest(event_content(event))
            ):
                valid = False
                break
            previous = event.event_hash
            expected_sequence += 1
        checkpoint = session.scalar(
            select(m.LedgerCheckpoint)
            .where(m.LedgerCheckpoint.project_id == project_id)
            .order_by(m.LedgerCheckpoint.sequence_no.desc())
            .limit(1)
        )
        if checkpoint and (
            len(events) < checkpoint.sequence_no
            or events[checkpoint.sequence_no - 1].event_hash != checkpoint.event_hash
        ):
            valid = False
        if checkpoint:
            if signer is None or anchor_root is None:
                raise unavailable("检查点验签与独立锚读取")
            signed = {
                "purpose": "ledger_checkpoint",
                "project_id": str(project_id),
                "sequence_no": str(checkpoint.sequence_no),
                "event_hash": checkpoint.event_hash,
                "key_id": checkpoint.key_id,
                "signature": checkpoint.signature,
            }
            try:
                signer.verify_signature(signed)
                target = Path(checkpoint.anchor_ref).resolve()
                if target.parent != anchor_root.resolve() or target.name != f"{checkpoint.id}.json":
                    valid = False
                elif json.loads(target.read_bytes()) != signed:
                    valid = False
            except (OSError, ValueError, ApplicationError):
                valid = False
        return {
            "project_id": str(project_id),
            "checked_through_sequence": str(expected_sequence - 1),
            "result": "valid" if valid else "invalid",
            "checkpoint_id": str(checkpoint.id) if checkpoint else None,
            "verified_at": utcnow(),
        }

    def checkpoint(
        self, session: Session, project_id: UUID, signer: CapabilitySigner, anchor_root: Path | None
    ) -> m.LedgerCheckpoint:
        if anchor_root is None:
            raise unavailable("独立审计检查点锚")
        session.scalar(select(m.Project).where(m.Project.id == project_id).with_for_update())
        last = session.scalar(
            select(m.AuditEvent)
            .where(m.AuditEvent.project_id == project_id)
            .order_by(m.AuditEvent.sequence_no.desc())
            .limit(1)
        )
        if last is None:
            raise ApplicationError("STATE_CONFLICT", "尚无可固定的审计事件", 409)
        # Re-use the Ed25519 primitive with a checkpoint-specific payload, not a tool capability.
        if not signer.can_sign:
            raise unavailable("检查点签名密钥")
        payload = {
            "purpose": "ledger_checkpoint",
            "project_id": str(project_id),
            "sequence_no": str(last.sequence_no),
            "event_hash": last.event_hash,
        }
        signed = signer.sign(payload)
        anchor_root.mkdir(parents=True, exist_ok=True)
        checkpoint_id = uuid4()
        target = anchor_root / f"{checkpoint_id}.json"
        # Exclusive creation: an existing anchor is never silently overwritten.
        with target.open("xb") as stream:
            stream.write(canonical_bytes(signed))
        checkpoint = m.LedgerCheckpoint(
            id=checkpoint_id,
            project_id=project_id,
            sequence_no=last.sequence_no,
            event_hash=last.event_hash,
            key_id=signed["key_id"],
            signature=signed["signature"],
            anchor_ref=str(target),
        )
        session.add(checkpoint)
        session.flush()
        return checkpoint
