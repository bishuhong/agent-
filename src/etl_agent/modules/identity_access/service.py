"""Local sessions and project grants backed by PostgreSQL facts."""

import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from etl_agent.adapters.identity.local_password import PasswordHasher
from etl_agent.config import Settings
from etl_agent.infrastructure.persistence import models as m
from etl_agent.infrastructure.persistence.database import Database
from etl_agent.modules.audit.service import EvidenceLedger
from etl_agent.modules.identity_access.domain import ROLE_ACTIONS, assert_action, has_action
from etl_agent.shared.canonical import digest
from etl_agent.shared.context import ActorContext
from etl_agent.shared.errors import ApplicationError, forbidden
from etl_agent.shared.security import require_key, session_csrf, token_digest


@dataclass(frozen=True)
class AuthenticatedSession:
    actor: ActorContext
    user: m.User
    session_id: UUID
    expires_at: object
    csrf_token: str


def actor_from_user(session: Session, user: m.User) -> ActorContext:
    roles: dict[UUID, set[str]] = {}
    slots: dict[UUID, set[str]] = {}
    for row in session.scalars(
        select(m.ProjectMembership).where(m.ProjectMembership.user_id == user.id)
    ):
        roles.setdefault(row.project_id, set()).add(row.role)
    for grant in session.scalars(
        select(m.ProjectRoleGrant).where(m.ProjectRoleGrant.user_id == user.id)
    ):
        slots.setdefault(grant.project_id, set()).add(grant.role_slot)
    return ActorContext(
        user.id,
        user.person_ref,
        {key: frozenset(value) for key, value in roles.items()},
        {key: frozenset(value) for key, value in slots.items()},
    )


def user_response(user: m.User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "status": user.status,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
        "revision": user.revision,
    }


class LocalAuthService:
    def __init__(self, database: Database, settings: Settings):
        self.database, self.settings = database, settings
        self.hasher = PasswordHasher(
            settings.scrypt_n, settings.scrypt_r, settings.scrypt_p, settings.password_parallelism
        )
        self._dummy = self.hasher.hash(secrets.token_urlsafe(32))

    def login(self, username: str, password: str, source: str) -> tuple[str, dict]:
        require_key(self.settings.session_key.get_secret_value(), "会话密钥")
        username = username.lower()
        throttle_key = digest({"account": username, "source": source})
        # Reserve a failed attempt before expensive password work. The short transaction
        # commits even when the later authentication result is 401.
        with self.database.session_factory.begin() as session:
            now = session.execute(select(func.clock_timestamp())).scalar_one()
            session.execute(
                insert(m.LoginThrottle)
                .values(key_hash=throttle_key, failures=0, window_started_at=now, updated_at=now)
                .on_conflict_do_nothing()
            )
            throttle = session.scalars(
                select(m.LoginThrottle)
                .where(m.LoginThrottle.key_hash == throttle_key)
                .with_for_update()
            ).one()
            assert throttle is not None
            if (
                now - throttle.window_started_at
            ).total_seconds() >= self.settings.login_window_seconds:
                throttle.failures = 0
                throttle.window_started_at = now
            if throttle.failures >= self.settings.login_failures:
                raise ApplicationError("RATE_LIMITED", "登录尝试过多，请稍后再试", 429)
            throttle.failures += 1
            throttle.updated_at = now
            row = session.execute(
                select(m.User, m.LocalCredential.password_hash)
                .outerjoin(m.LocalCredential, m.LocalCredential.user_id == m.User.id)
                .where(m.User.username == username)
            ).first()
            user_id = row[0].id if row else None
            encoded = row[1] if row and row[1] else self._dummy
        matches = self.hasher.verify(password, encoded)
        if not matches or user_id is None:
            raise ApplicationError("AUTH_REQUIRED", "账号或密码不正确", 401)
        with self.database.session_factory.begin() as session:
            user = session.get(m.User, user_id)
            if user is None or user.status != "enabled":
                raise ApplicationError("AUTH_REQUIRED", "账号或密码不正确", 401)
            now = session.execute(select(func.clock_timestamp())).scalar_one()
            raw = secrets.token_urlsafe(32)
            csrf = session_csrf(raw, self.settings.session_key.get_secret_value())
            record = m.AuthSession(
                id=uuid4(),
                user_id=user.id,
                token_digest=token_digest(raw),
                csrf_digest=token_digest(csrf),
                created_at=now,
                last_seen_at=now,
                expires_at=now + timedelta(hours=8),
                revoked_at=None,
            )
            session.add(record)
            success_throttle = session.get(m.LoginThrottle, throttle_key, with_for_update=True)
            if success_throttle:
                success_throttle.failures = 0
            session.flush()
            return raw, {
                "user": user_response(user),
                "csrf_token": csrf,
                "expires_at": record.expires_at,
            }

    def authenticate(
        self, session: Session, raw: str | None, *, touch: bool = True
    ) -> AuthenticatedSession:
        if not raw:
            raise ApplicationError("AUTH_REQUIRED", "请先登录", 401)
        record = session.scalars(
            select(m.AuthSession)
            .where(m.AuthSession.token_digest == token_digest(raw))
            .with_for_update()
        ).one()
        now = session.execute(select(func.clock_timestamp())).scalar_one()
        if (
            record is None
            or record.revoked_at is not None
            or now >= record.expires_at
            or now - record.last_seen_at >= timedelta(minutes=30)
        ):
            raise ApplicationError("AUTH_REQUIRED", "会话已失效，请重新登录", 401)
        user = session.get(m.User, record.user_id)
        if user is None or user.status != "enabled":
            raise ApplicationError("AUTH_REQUIRED", "会话已失效，请重新登录", 401)
        csrf = session_csrf(raw, self.settings.session_key.get_secret_value())
        if not hmac.compare_digest(token_digest(csrf), record.csrf_digest):
            raise ApplicationError("AUTH_REQUIRED", "会话密钥已变化，请重新登录", 401)
        if touch:
            record.last_seen_at = now
        return AuthenticatedSession(
            actor_from_user(session, user), user, record.id, record.expires_at, csrf
        )

    def validate_csrf(self, identity: AuthenticatedSession, supplied: str | None) -> None:
        if not supplied or not hmac.compare_digest(supplied, identity.csrf_token):
            raise forbidden("CSRF校验失败")

    def current(self, session: Session, identity: AuthenticatedSession) -> dict:
        actor = identity.actor
        memberships = session.scalars(
            select(m.ProjectMembership).where(m.ProjectMembership.user_id == actor.user_id)
        ).all()
        return {
            "user": user_response(identity.user),
            "project_memberships": [
                {
                    "id": row.id,
                    "project_id": row.project_id,
                    "user_id": row.user_id,
                    "role": row.role,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                    "revision": row.revision,
                }
                for row in memberships
            ],
            "role_grants": [
                {
                    "id": row.id,
                    "project_id": row.project_id,
                    "user_id": row.user_id,
                    "role_slot": row.role_slot,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                    "revision": row.revision,
                }
                for row in session.scalars(
                    select(m.ProjectRoleGrant).where(m.ProjectRoleGrant.user_id == actor.user_id)
                )
            ],
            "available_actions": [],
            "expires_at": identity.expires_at,
            "csrf_token": identity.csrf_token,
        }

    def logout(self, session: Session, identity: AuthenticatedSession) -> dict:
        record = session.get(m.AuthSession, identity.session_id, with_for_update=True)
        assert record is not None
        record.revoked_at = session.execute(select(func.clock_timestamp())).scalar_one()
        return {"logged_out": True}


class ProjectService:
    def __init__(self, ledger: EvidenceLedger, settings: Settings):
        self.ledger, self.settings = ledger, settings

    def actions(
        self, actor: ActorContext, project_id: UUID, actions: list[str] | None = None
    ) -> list[dict]:
        if actions is None:
            actions = sorted(
                {action for values in ROLE_ACTIONS.values() for action in values}
                | {"manage_project_members"}
            )
        return [
            {
                "action": action,
                "allowed": has_action(actor, project_id, action, self.settings.project_managers),
                "reason_code": None
                if has_action(actor, project_id, action, self.settings.project_managers)
                else "FORBIDDEN",
                "reason_message": None
                if has_action(actor, project_id, action, self.settings.project_managers)
                else "当前项目未授予此操作",
            }
            for action in actions
        ]

    def grant(
        self,
        session: Session,
        actor: ActorContext,
        project_id: UUID,
        user_id: UUID,
        role: str | None,
        role_slot: str | None,
        expected_revision: int | None = None,
    ) -> m.ProjectMembership | m.ProjectRoleGrant:
        assert_action(actor, project_id, "manage_project_members", self.settings.project_managers)
        if session.get(m.User, user_id) is None:
            raise ApplicationError("RESOURCE_NOT_FOUND", "用户不存在", 404)
        project = session.scalars(
            select(m.Project).where(m.Project.id == project_id).with_for_update()
        ).one()
        if expected_revision is not None and project.revision != expected_revision:
            raise ApplicationError("REVISION_MISMATCH", "项目成员授权集合已变化", 412)
        row: m.ProjectMembership | m.ProjectRoleGrant | None
        if role:
            if role not in ROLE_ACTIONS:
                raise forbidden("未知角色")
            row = session.scalar(
                select(m.ProjectMembership).where(
                    m.ProjectMembership.project_id == project_id,
                    m.ProjectMembership.user_id == user_id,
                    m.ProjectMembership.role == role,
                )
            )
            if row is None:
                row = m.ProjectMembership(project_id=project_id, user_id=user_id, role=role)
                session.add(row)
        else:
            needed = {"data": "data_checker", "security": "security_checker"}.get(role_slot or "")
            membership = session.scalar(
                select(m.ProjectMembership).where(
                    m.ProjectMembership.project_id == project_id,
                    m.ProjectMembership.user_id == user_id,
                    m.ProjectMembership.role == needed,
                )
            )
            if membership is None:
                raise forbidden("授予职责槽前必须有对应项目角色")
            row = session.scalar(
                select(m.ProjectRoleGrant).where(
                    m.ProjectRoleGrant.project_id == project_id,
                    m.ProjectRoleGrant.user_id == user_id,
                    m.ProjectRoleGrant.role_slot == role_slot,
                )
            )
            if row is None:
                row = m.ProjectRoleGrant(
                    project_id=project_id, user_id=user_id, role_slot=role_slot
                )
                session.add(row)
        session.flush()
        project.revision += 1
        assert row is not None
        self.ledger.append(session, project_id, actor, "member_granted", "membership", row.id)
        return row
