"""Actual prepare/dual approval/commit/worker consumption, with controlled engine I/O."""

from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import func, select
from test_generation import ControlledProvider, setup_data
from test_generation import generation_db as generation_db

from etl_agent.api.schemas import contracts as c
from etl_agent.bootstrap import BackendRuntime
from etl_agent.infrastructure.persistence import models as m
from etl_agent.infrastructure.persistence.database import Database
from etl_agent.modules.execution.service import EngineObservation
from etl_agent.modules.generation.service import GenerationService
from etl_agent.modules.identity_access.service import actor_from_user
from etl_agent.modules.publication.service import TargetObservation
from etl_agent.shared.canonical import encode_url
from etl_agent.shared.context import RequestContext, WorkerContext
from etl_agent.shared.errors import ApplicationError
from etl_agent.workers.tasks import dispatch_outbox, scheduled_preparation

pytestmark = pytest.mark.integration


@pytest.fixture()
def control(generation_db):
    settings, factory = generation_db
    actor, source, target, version_id, connection_id = setup_data(factory)
    key = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    settings = settings.model_copy(
        update={
            "session_key": c.SecretStr("s" * 40),
            "cursor_key": c.SecretStr("c" * 40),
            "capability_private_key": c.SecretStr(encode_url(key)),
            "classification_risks": {"unclassified": "P3", "public": "P3"},
        }
    )
    database = object.__new__(Database)
    database.engine = factory.kw["bind"]
    database.session_factory = factory
    database.sessionmaker = factory
    rt = BackendRuntime(settings, database=database)
    provider = ControlledProvider(source, target, connection_id)
    generation = GenerationService(settings, provider=provider)
    with factory.begin() as session:
        result = generation.start(
            session,
            actor,
            version_id,
            c.GenerationRequest(
                prompt="copy", source_profile_ids=[source], target_profile_id=target
            ),
            1,
        )
    generation.process(UUID(result["id"]), factory)
    with factory.begin() as session:
        design = rt.pipelines.design(session, actor, version_id)
        rt.pipelines.freeze(
            session,
            actor,
            c.FreezeVersionRequest(
                version_id=version_id,
                content_digest=design["validation_report"]["content_digest"],
                validation_report_id=design["validation_report"]["id"],
                expected_revision=design["version"]["revision"],
            ),
        )
        users = {}
        for role, slot in (
            ("operator", None),
            ("data_checker", "data"),
            ("security_checker", "security"),
        ):
            user = m.User(
                username=role + uuid4().hex,
                subject_ref="local:" + uuid4().hex,
                person_ref=uuid4(),
                display_name=role,
                status="enabled",
            )
            session.add(user)
            session.flush()
            session.add(
                m.ProjectMembership(
                    project_id=next(iter(actor.project_roles)), user_id=user.id, role=role
                )
            )
            if slot:
                session.add(
                    m.ProjectRoleGrant(
                        project_id=next(iter(actor.project_roles)), user_id=user.id, role_slot=slot
                    )
                )
            session.flush()
            users[role] = actor_from_user(session, user)
        prep = rt.governance.prepare(
            session,
            actor,
            version_id,
            c.PrepareRequest(
                purpose="execute_etl",
                environment="test",
                execution_run_id=None,
                evaluation_binding=None,
                supervision_contract={
                    "max_input_records": "100",
                    "max_output_bytes": "1024",
                    "max_duration_ms": "60000",
                    "max_amplification_ratio": "10",
                    "max_rejection_ratio": "0.05",
                    "on_exceed": "kill",
                },
            ),
        )
    return rt, actor, users, prep


def approve(rt, users, prep):
    with rt.database.session_factory.begin() as session:
        for approval in prep["approvals"]:
            role = "data_checker" if approval["required_slot"] == "data" else "security_checker"
            rt.governance.decide(
                session,
                users[role],
                UUID(approval["id"]),
                c.ApprovalDecisionRequest(decision="approve"),
                approval["revision"],
            )


def test_commit_requires_both_approvals(control):
    rt, maker, users, prep = control
    with pytest.raises(ApplicationError) as exc, rt.database.session_factory.begin() as session:
        rt.commits.commit(
            session,
            users["operator"],
            UUID(prep["preparation"]["id"]),
            c.CommitRequest(expected_input_fingerprint=prep["preparation"]["input_fingerprint"]),
            RequestContext("test", "key"),
        )
    assert exc.value.code == "STATE_CONFLICT"
    assert exc.value.status_code == 409
    assert "必需审批" in exc.value.message
    with rt.database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(m.ExecutionRun)) == 0
        assert session.scalar(select(func.count()).select_from(m.OutboxMessage)) == 0


def test_commit_is_atomic_idempotent_and_worker_consumes_once(control):
    rt, maker, users, prep = control
    approve(rt, users, prep)
    payload = c.CommitRequest(expected_input_fingerprint=prep["preparation"]["input_fingerprint"])
    with rt.database.session_factory.begin() as session:
        result = rt.commits.commit(
            session,
            users["operator"],
            UUID(prep["preparation"]["id"]),
            payload,
            RequestContext("test", "same-key"),
        )
    with rt.database.session_factory.begin() as session:
        duplicate = rt.commits.commit(
            session,
            users["operator"],
            UUID(prep["preparation"]["id"]),
            payload,
            RequestContext("retry", "same-key"),
        )
        assert duplicate == result
        assert session.scalar(select(func.count()).select_from(m.CapabilityConsumption)) == 0
        assert session.scalar(select(func.count()).select_from(m.OutboxMessage)) == 1
        command = session.scalar(select(m.ManagedCommand))
        command_id = command.id
        assert command.status == "pending"
    with rt.database.session_factory.begin() as session:
        claimed = rt.replay.consume(session, command_id, WorkerContext("worker-1"))
        assert not claimed.recovering
    with pytest.raises(ApplicationError) as exc, rt.database.session_factory.begin() as session:
        rt.replay.consume(session, command_id, WorkerContext("worker-2"))
    assert exc.value.code == "CAPABILITY_REPLAYED"
    with rt.database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(m.CapabilityConsumption)) == 1
        assert session.get(m.ExecutionRun, UUID(result["execution_run_id"])).status == "authorizing"


def test_changed_connection_invalidates_approval_basis(control):
    rt, maker, users, prep = control
    approve(rt, users, prep)
    with rt.database.session_factory.begin() as session:
        connection = session.scalar(select(m.Connection))
        connection.revision += 1
    with pytest.raises(ApplicationError) as exc, rt.database.session_factory.begin() as session:
        rt.commits.commit(
            session,
            users["operator"],
            UUID(prep["preparation"]["id"]),
            c.CommitRequest(expected_input_fingerprint=prep["preparation"]["input_fingerprint"]),
            RequestContext("test", "changed"),
        )
    assert exc.value.code == "FINGERPRINT_CHANGED"


def test_observed_quality_and_receipt_publication(control):
    rt, maker, users, prep = control
    approve(rt, users, prep)

    class ControlledEngine:
        calls = 0

        def submit(self, spec):
            self.calls += 1
            assert any("__etl_" in scope["resource_name"] for scope in spec["resource_scopes"])
            return EngineObservation(
                "verified-test-job",
                "succeeded",
                {
                    "input_records": "10",
                    "output_records": "10",
                    "error_records": "0",
                    "bytes_processed": "100",
                    "output_bytes": "100",
                },
            )

    class ControlledTarget:
        calls = 0

        def execute(self, spec):
            self.calls += 1
            assert spec["tool"] in {"publish", "rollback"}
            return TargetObservation(
                UUID(spec["command_id"]),
                spec["expected_generation"] + 1,
                spec["retained_resource"],
                True,
            )

    engine, target = ControlledEngine(), ControlledTarget()
    rt.executor.engine, rt.publications.adapter = engine, target
    with rt.database.session_factory.begin() as session:
        rt.commits.commit(
            session,
            users["operator"],
            UUID(prep["preparation"]["id"]),
            c.CommitRequest(expected_input_fingerprint=prep["preparation"]["input_fingerprint"]),
            RequestContext("test", "publish"),
        )
        initial_id = session.scalar(select(m.ManagedCommand.id))
    rt.executor.execute(initial_id, "worker-initial")
    with rt.database.session_factory() as session:
        report = session.scalar(select(m.QualityReport))
        assert report.result == "passed" and report.publish_eligible
        publication = session.scalar(select(m.PublicationRecord))
        assert publication.status == "not_published"
        publish_id = publication.command_id
        assert publish_id != initial_id
    rt.executor.execute(publish_id, "worker-publish")
    rt.executor.execute(publish_id, "redelivery")
    with rt.database.session_factory() as session:
        row = session.scalar(select(m.PublicationRecord))
        assert row.status == "published" and row.published_generation == 1
        assert row.retained_resource == prep["preparation"]["rollback_plan"]["retained_resource"]
        assert session.scalar(select(m.TargetHead.generation)) == 1
        assert session.scalar(select(func.count()).select_from(m.CapabilityConsumption)) == 2
    assert engine.calls == target.calls == 1
    with rt.database.session_factory.begin() as session:
        run = session.scalar(select(m.ExecutionRun))
        publication = session.scalar(select(m.PublicationRecord))
        restore_prep = rt.governance.prepare(
            session,
            users["operator"],
            run.version_id,
            c.PrepareRequest(
                purpose="rollback",
                environment="test",
                execution_run_id=run.id,
                evaluation_binding=None,
                supervision_contract=prep["preparation"]["supervision_contract"],
            ),
        )
        run_id, publication_id = run.id, publication.id
    approve(rt, users, restore_prep)
    with rt.database.session_factory.begin() as session:
        run = session.get(m.ExecutionRun, run_id)
        operation = rt.commits.operation(
            session,
            users["operator"],
            run.id,
            "rollback",
            run.revision,
            RequestContext("restore", "restore-key"),
            preparation_id=UUID(restore_prep["preparation"]["id"]),
            publication_id=publication_id,
        )
        restore_command = operation.command_id
    rt.executor.execute(restore_command, "worker-restore")
    rt.executor.execute(restore_command, "restore-redelivery")
    with rt.database.session_factory() as session:
        assert session.scalar(select(m.TargetHead.generation)) == 2
        assert session.scalar(select(m.RunOperation.status)) == "succeeded"
        assert session.scalar(select(m.TargetHead.current_publication_id)) is None
    assert engine.calls == 1 and target.calls == 2


def committed_command(rt, users, prep, key):
    approve(rt, users, prep)
    with rt.database.session_factory.begin() as session:
        rt.commits.commit(
            session,
            users["operator"],
            UUID(prep["preparation"]["id"]),
            c.CommitRequest(expected_input_fingerprint=prep["preparation"]["input_fingerprint"]),
            RequestContext("test", key),
        )
        return session.scalar(select(m.ManagedCommand.id))


def test_unknown_submission_never_blindly_resubmits(control):
    rt, maker, users, prep = control
    command_id = committed_command(rt, users, prep, "unknown")

    class UncertainEngine:
        calls = 0

        def submit(self, spec):
            self.calls += 1
            raise ApplicationError("EXECUTION_UNKNOWN", "受控测试：提交超时，结果未知", 409)

    engine = UncertainEngine()
    rt.executor.engine = engine
    for worker in ("first", "recovery"):
        with pytest.raises(ApplicationError) as failure:
            rt.executor.execute(command_id, worker)
        assert failure.value.code == "EXECUTION_UNKNOWN"
    with rt.database.session_factory() as session:
        assert session.scalar(select(m.ExecutionRun.status)) == "reconciling"
        assert session.scalar(select(func.count()).select_from(m.CapabilityConsumption)) == 1
    assert engine.calls == 1


def test_duplicate_worker_cannot_corrupt_live_claim(control):
    rt, maker, users, prep = control
    command_id = committed_command(rt, users, prep, "claim")
    with rt.database.session_factory.begin() as session:
        rt.replay.consume(session, command_id, WorkerContext("owner"))
    with pytest.raises(ApplicationError) as failure:
        rt.executor.execute(command_id, "duplicate")
    assert failure.value.code == "STATE_CONFLICT"
    with rt.database.session_factory() as session:
        command = session.get(m.ManagedCommand, command_id)
        assert command.claim_owner == "owner" and command.status == "claimed"
        assert session.get(m.ExecutionRun, command.execution_run_id).status == "authorizing"


def test_outbox_broker_failure_does_not_lose_command(control):
    rt, maker, users, prep = control
    command_id = committed_command(rt, users, prep, "outbox")

    def failed_send(*args, **kwargs):
        raise ConnectionError("controlled broker failure")

    with pytest.raises(ConnectionError):
        dispatch_outbox(rt, failed_send)
    with rt.database.session_factory() as session:
        assert session.scalar(select(m.OutboxMessage.delivery_status)) == "pending"
    messages = []
    assert dispatch_outbox(rt, lambda task, **kwargs: messages.append((task, kwargs))) == 1
    assert (
        dispatch_outbox(
            rt, lambda *args, **kwargs: pytest.fail("must not redeliver a sent outbox row")
        )
        == 0
    )
    assert messages == [("etl_agent.command", {"args": [str(command_id)]})]
    with rt.database.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(m.CapabilityConsumption)) == 0


def test_scheduled_occurrence_creates_new_pending_approvals_only(control, monkeypatch):
    from datetime import timedelta

    from etl_agent.shared.context import utcnow

    rt, maker, users, prep = control
    due = utcnow() + timedelta(seconds=2)
    with rt.database.session_factory.begin() as session:
        version = session.get(m.PipelineVersion, UUID(prep["preparation"]["version_id"]))
        schedule = rt.schedules.save(
            session,
            users["operator"],
            c.ScheduleDefinitionRequest(
                pipeline_id=version.pipeline_id,
                version_id=version.id,
                kind="once",
                timezone="Asia/Shanghai",
                run_at=due,
                cron_expression=None,
                enabled=True,
            ),
        )
    monkeypatch.setattr("etl_agent.modules.scheduling.service.utcnow", lambda: due)
    with rt.database.session_factory.begin() as session:
        result = rt.schedules.on_due(
            session,
            UUID(schedule["id"]),
            due,
            lambda unit, row: UUID(scheduled_preparation(rt, unit, row)),
        )
        assert result["result"] == "created"
        assert result["preparation_id"] != prep["preparation"]["id"]
    with rt.database.session_factory() as session:
        approvals = list(
            session.scalars(
                select(m.ApprovalRequest).where(
                    m.ApprovalRequest.preparation_id == UUID(result["preparation_id"])
                )
            )
        )
        assert len(approvals) == 2 and all(row.status == "pending" for row in approvals)
        assert session.scalar(select(func.count()).select_from(m.ExecutionRun)) == 0
        assert session.scalar(select(func.count()).select_from(m.CapabilityAuthorization)) == 0


def test_versioned_catalog_rejects_overwrite_and_worker_runs_l0(control):
    from etl_agent.modules.evaluation.catalog import DatasetImport, EvaluationCatalog

    rt, maker, users, prep = control
    payload = DatasetImport.model_validate(
        {
            "project_id": prep["preparation"]["project_id"],
            "name": "controlled-frozen-version",
            "version": "v1",
            "cases": [
                {
                    "case_key": "gate",
                    "level": "L0",
                    "input": {"version_id": prep["preparation"]["version_id"]},
                    "expected": {"expected_gate_pass": True},
                }
            ],
        }
    )
    with rt.database.session_factory.begin() as session:
        result = EvaluationCatalog(rt.ledger).import_dataset(session, maker, payload)
    with pytest.raises(ApplicationError) as failure, rt.database.session_factory.begin() as session:
        EvaluationCatalog(rt.ledger).import_dataset(session, maker, payload)
    assert failure.value.code == "STATE_CONFLICT"
    with rt.database.session_factory.begin() as session:
        benchmark = rt.benchmarks.start(
            session,
            maker,
            c.BenchmarkRunRequest(
                project_id=payload.project_id,
                dataset_id=result["id"],
                level="L0",
                preparation_id=None,
                provider_binding={
                    "provider": "qwen",
                    "model": "controlled-test",
                    "prompt_version": "v1",
                    "policy_version": "v1",
                },
            ),
            RequestContext("benchmark"),
        )
        outcome = rt.benchmarks.run_deterministic(session, maker, UUID(benchmark["id"]))
        assert outcome["status"] == "succeeded"
    with rt.database.session_factory() as session:
        assert session.scalar(select(m.BenchmarkCaseResult.passed)) is True
        assert session.scalar(select(func.count()).select_from(m.ExecutionRun)) == 0
