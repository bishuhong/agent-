"""Real PostgreSQL/LangGraph tests; model responses alone use a controlled stub."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from etl_agent.api.schemas import contracts as c
from etl_agent.config import Settings
from etl_agent.infrastructure.persistence import models as m
from etl_agent.modules.generation.ports import CandidateArtifacts, ClarificationOutput
from etl_agent.modules.generation.service import GenerationService
from etl_agent.modules.identity_access.service import actor_from_user
from etl_agent.modules.pipelines.service import PipelineService

pytestmark = pytest.mark.integration


@pytest.fixture()
def generation_db():
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("TEST_DATABASE_URL absent: PostgreSQL generation integration is not configured")
    url = make_url(raw).set(drivername="postgresql+psycopg")
    if url.get_backend_name() != "postgresql":
        pytest.fail("Real PostgreSQL is required")
    schema = "generation_test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    options = f"-c search_path={schema} -c timezone=UTC"
    engine = create_engine(url, connect_args={"options": options})
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "migrations")
    )
    try:
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        configured = url.update_query_dict({"options": options}).render_as_string(
            hide_password=False
        )
        settings = Settings(environment="test", database_url=configured)
        yield settings, sessionmaker(engine, expire_on_commit=False)
    finally:
        engine.dispose()
        assert (
            schema.startswith("generation_test_")
            and schema.removeprefix("generation_test_").isalnum()
        )
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def setup_data(factory):
    with factory() as session, session.begin():
        uid = uuid4()
        user = m.User(
            id=uid,
            subject_ref="local:" + str(uid),
            username="maker_" + uid.hex,
            person_ref=uuid4(),
            display_name="Maker",
            status="enabled",
        )
        project = m.Project(id=uuid4(), name="Generation", code=uuid4().hex)
        session.add_all([user, project])
        session.flush()
        session.add(
            m.ProjectMembership(id=uuid4(), project_id=project.id, user_id=uid, role="maker")
        )
        connection = m.Connection(
            id=uuid4(),
            project_id=project.id,
            name="source",
            connector_type="postgresql",
            endpoint={
                "kind": "database",
                "host": "localhost",
                "port": 5432,
                "database": "test",
                "schema": None,
            },
            environment="test",
        )
        session.add(connection)
        session.flush()
        fields = [
            {
                "field_name": "amount",
                "ordinal": 0,
                "native_type": "integer",
                "logical_type": "integer",
                "nullable": False,
                "is_primary_key": False,
                "precision": None,
                "scale": None,
                "sensitivity": "public",
            }
        ]
        profiles = []
        for name in ("source", "target"):
            profile = m.MetadataProfile(
                id=uuid4(),
                project_id=project.id,
                connection_id=connection.id,
                resource_name=name,
                fields=fields,
                statistics={"row_count": "10", "is_approximate": True},
                masked_samples=[],
                profile_digest=("a" if name == "source" else "b") * 64,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            )
            session.add(profile)
            profiles.append(profile)
        session.flush()
        actor = actor_from_user(session, user)
        created = PipelineService(Settings()).create(
            session,
            actor,
            c.PipelineCreateRequest(project_id=project.id, name="Pipeline", code="test"),
        )
        return (
            actor,
            profiles[0].id,
            profiles[1].id,
            UUID(created["candidate_version_id"]),
            connection.id,
        )


class ControlledProvider:
    """Only external model I/O is controlled. Graph, checkpoint, DB and gates are real."""

    def __init__(self, source_id, target_id, connection_id, *, clarify=False, broken=False):
        self.source_id, self.target_id, self.connection_id = source_id, target_id, connection_id
        self.clarify, self.broken, self.calls = clarify, broken, 0

    def resolve_binding(self, provider):
        return c.ProviderBinding(
            provider="qwen", model="controlled-test", prompt_version="v1", policy_version="v1"
        )

    def generate(self, binding, context):
        self.calls += 1
        if self.clarify and not context.answers:
            return ClarificationOutput(
                kind="clarification",
                questions=[{"question_key": "field", "question": "Confirm amount mapping"}],
            )
        plan = {
            "source_profile_ids": [str(self.source_id)],
            "target_profile_id": str(self.target_id),
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
                "error_resource": {
                    "connection_id": str(self.connection_id),
                    "file_asset_id": None,
                    "resource_name": "errors",
                    "operations": ["execute_etl"],
                    "environment": "test",
                },
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
        hocon = 'env { parallelism=1 }\nsource { Managed { resource_ref="source" } }\nsink { Managed { resource_ref="target" } }'
        if self.broken:
            hocon = 'env { parallelism=1 }\nsource { Managed { resource_ref="source" } }'
        return CandidateArtifacts(kind="candidate", etl_plan=plan, hocon_text=hocon)


def test_generation_freeze_and_version_integrity(generation_db):
    settings, factory = generation_db
    actor, source, target, version_id, connection_id = setup_data(factory)
    provider = ControlledProvider(source, target, connection_id)
    service = GenerationService(settings, provider=provider)
    with factory() as session, session.begin():
        response = service.start(
            session,
            actor,
            version_id,
            c.GenerationRequest(
                prompt="copy amount", source_profile_ids=[source], target_profile_id=target
            ),
            1,
        )
        run_id = UUID(response["id"])
        assert response["status"] == "queued"
        assert provider.calls == 0
    service.process(run_id, factory)
    with factory() as session, session.begin():
        run = session.get(m.AgentRun, run_id)
        assert run.status == "succeeded" and run.repair_count == 0
        assert session.scalar(select(func.count()).select_from(m.ExecutionRun)) == 0
        design = PipelineService(settings).design(session, actor, version_id)
        assert design["validation_report"]["passed"]
        payload = c.FreezeVersionRequest(
            version_id=version_id,
            content_digest=design["validation_report"]["content_digest"],
            validation_report_id=UUID(design["validation_report"]["id"]),
            expected_revision=design["version"]["revision"],
        )
        frozen = PipelineService(settings).freeze(session, actor, payload)
        assert frozen["is_immutable"]
        assert session.scalar(select(func.count()).select_from(m.PipelineArtifact)) == 5
        import hashlib

        for artifact in session.scalars(select(m.PipelineArtifact)):
            assert isinstance(artifact.content, str)
            assert (
                hashlib.sha256(artifact.content.encode("utf-8")).hexdigest()
                == artifact.artifact_digest
            )
    service.process(run_id, factory)
    assert provider.calls == 1


def test_checkpoint_clarification_survives_service_restart(generation_db):
    settings, factory = generation_db
    actor, source, target, version_id, connection_id = setup_data(factory)
    provider = ControlledProvider(source, target, connection_id, clarify=True)
    with factory() as session, session.begin():
        response = GenerationService(settings, provider=provider).start(
            session,
            actor,
            version_id,
            c.GenerationRequest(
                prompt="copy", source_profile_ids=[source], target_profile_id=target
            ),
            1,
        )
        run_id = UUID(response["id"])
    GenerationService(settings, provider=provider).process(run_id, factory)
    with factory() as session, session.begin():
        assert session.get(m.AgentRun, run_id).status == "waiting_input"
        GenerationService(settings, provider=provider).answer(
            session,
            actor,
            run_id,
            c.ClarificationAnswerRequest(
                answers=[{"question_key": "field", "answer": "confirmed"}]
            ),
        )
    # New workflow/service instance reads PostgreSQL checkpoint and resumes the same thread.
    GenerationService(settings, provider=provider).process(run_id, factory)
    with factory() as session:
        assert session.get(m.AgentRun, run_id).status == "succeeded"
        assert session.scalar(select(func.count()).select_from(m.AgentRun)) == 1
    assert provider.calls == 2


def test_automatic_repairs_stop_at_three_across_restarts(generation_db):
    settings, factory = generation_db
    actor, source, target, version_id, connection_id = setup_data(factory)
    provider = ControlledProvider(source, target, connection_id, broken=True)
    service = GenerationService(settings, provider=provider)
    with factory() as session, session.begin():
        response = service.start(
            session,
            actor,
            version_id,
            c.GenerationRequest(
                prompt="copy", source_profile_ids=[source], target_profile_id=target
            ),
            1,
        )
        run_id = UUID(response["id"])
    service.process(run_id, factory)
    with factory() as session:
        run = session.get(m.AgentRun, run_id)
        assert run.status == "failed" and run.repair_count == 3
        assert not session.get(m.PipelineVersion, version_id).is_immutable
    assert provider.calls == 4
    GenerationService(settings, provider=provider).process(run_id, factory)
    assert provider.calls == 4
