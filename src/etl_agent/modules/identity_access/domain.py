"""Project scoped permissions; no implicit administrator role."""

from uuid import UUID

from etl_agent.shared.context import ActorContext
from etl_agent.shared.errors import ApplicationError, forbidden

ROLE_ACTIONS: dict[str, frozenset[str]] = {
    "maker": frozenset(
        {
            "view",
            "create_connection",
            "edit_connection",
            "test_connection",
            "profile_connection",
            "upload_file",
            "create_pipeline",
            "generate",
            "answer_clarification",
            "freeze_version",
            "prepare",
            "run_benchmark",
        }
    ),
    "data_checker": frozenset({"view", "approve_data"}),
    "security_checker": frozenset(
        {"view", "approve_security", "authorize_evolution", "revoke_evolution"}
    ),
    "operator": frozenset(
        {
            "view",
            "create_connection",
            "edit_connection",
            "test_connection",
            "profile_connection",
            "rotate_secret",
            "prepare",
            "commit",
            "cancel",
            "pause",
            "resume",
            "rollback",
            "cleanup",
            "rerun",
            "run_benchmark",
            "manage_schedule",
        }
    ),
    "auditor": frozenset({"view"}),
}


def has_action(
    actor: ActorContext, project_id: UUID, action: str, managers: dict[str, list[str]] | None = None
) -> bool:
    roles = actor.project_roles.get(project_id, frozenset())
    if action == "manage_project_members":
        return bool(roles) and str(actor.user_id) in (managers or {}).get(str(project_id), [])
    if action == "approve_data" and "data" not in actor.project_slots.get(project_id, frozenset()):
        return False
    if action == "approve_security" and "security" not in actor.project_slots.get(
        project_id, frozenset()
    ):
        return False
    return any(action in ROLE_ACTIONS.get(role, frozenset()) for role in roles)


def assert_action(
    actor: ActorContext, project_id: UUID, action: str, managers: dict[str, list[str]] | None = None
) -> None:
    if not has_action(actor, project_id, action, managers):
        raise forbidden()


def assert_separation(
    maker_person_ref: UUID, checker_person_ref: UUID, other_checker_people: set[UUID]
) -> None:
    if maker_person_ref == checker_person_ref:
        raise ApplicationError("SELF_APPROVAL_DENIED", "申请人不能审批自己的准备单", 403)
    if checker_person_ref in other_checker_people:
        raise ApplicationError("DUTY_CONFLICT", "同一自然人不能占用两个必需审批槽", 403)
