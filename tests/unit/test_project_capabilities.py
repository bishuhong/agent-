from uuid import uuid4

from etl_agent.config import Settings
from etl_agent.modules.audit.service import EvidenceLedger
from etl_agent.modules.identity_access.service import ProjectService
from etl_agent.shared.context import ActorContext


def test_project_capability_projection_exposes_supported_actions_without_granting_checker_privileges():
    project = uuid4()
    actor = ActorContext(uuid4(), uuid4(), {project: frozenset({"maker", "operator"})})
    service = ProjectService(EvidenceLedger(), Settings(_env_file=None))
    result = {item["action"]: item for item in service.actions(actor, project)}
    assert result["create_pipeline"]["allowed"] is True
    assert result["commit"]["allowed"] is True
    assert result["approve_security"]["allowed"] is False
    assert result["approve_security"]["reason_code"] == "FORBIDDEN"
    assert result["manage_project_members"]["allowed"] is False
    assert not any(item["allowed"] for item in service.actions(actor, uuid4()))
