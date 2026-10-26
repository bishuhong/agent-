"""Short transactional generation requests and restart-safe background processing."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import asdict
from typing import Any
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from etl_agent.adapters.models.providers import ModelProviderRouter, safe_prompt
from etl_agent.adapters.workflow.langgraph import LangGraphWorkflow
from etl_agent.api.schemas import contracts as c
from etl_agent.config import Settings
from etl_agent.infrastructure.persistence import models as m
from etl_agent.infrastructure.persistence.access import (
    compare_revision,
    project_entity,
    public_record,
)
from etl_agent.modules.audit.service import EvidenceLedger
from etl_agent.modules.generation.ports import (
    ClarificationOutput,
    ModelContext,
    ModelOutput,
    ModelProvider,
    PersistentWorkflow,
)
from etl_agent.modules.identity_access.domain import assert_action
from etl_agent.modules.identity_access.service import actor_from_user
from etl_agent.modules.pipelines.service import artifacts_for, profile_context, validate_materials
from etl_agent.shared.canonical import artifact_package
from etl_agent.shared.context import ActorContext, utcnow
from etl_agent.shared.errors import ApplicationError, invalid, unavailable

ACTIVE = ("queued", "running", "waiting_input", "validating", "repairing")


class GenerationService:
    def __init__(
        self,
        settings: Settings,
        ledger: EvidenceLedger | None = None,
        provider: ModelProvider | None = None,
        workflow: PersistentWorkflow | None = None,
    ):
        self.settings = settings
        self.ledger = ledger or EvidenceLedger()
        self.provider = provider or ModelProviderRouter(settings)
        self.workflow = workflow or LangGraphWorkflow(settings, self.provider)

    def start(
        self,
        session: Session,
        actor: ActorContext,
        version_id: UUID,
        payload: c.GenerationRequest,
        expected_revision: int,
    ) -> dict[str, Any]:
        version = project_entity(
            session, m.PipelineVersion, version_id, actor, "generate", lock=True
        )
        compare_revision(version.revision, expected_revision)
        if version.is_immutable:
            raise ApplicationError("STATE_CONFLICT", "冻结版本不能重新生成", 409)
        if session.scalar(
            select(m.AgentRun.id).where(
                m.AgentRun.version_id == version.id, m.AgentRun.status.in_(ACTIVE)
            )
        ):
            raise ApplicationError("STATE_CONFLICT", "候选已有未结束的生成任务", 409)
        sources, target = profile_context(
            session,
            actor,
            payload.source_profile_ids,
            payload.target_profile_id,
            version.project_id,
        )
        binding = self.provider.resolve_binding(payload.provider)
        self.workflow.ensure_ready()
        conversation = m.Conversation(
            id=uuid4(),
            project_id=version.project_id,
            pipeline_id=version.pipeline_id,
            creator_id=actor.user_id,
        )
        session.add(conversation)
        session.flush()
        run_id = uuid4()
        context = {
            "schema_version": 1,
            "source_profile_ids": [str(profile.id) for profile in sources],
            "target_profile_id": str(target.id),
            "profile_digests": {
                str(profile.id): profile.profile_digest for profile in [*sources, target]
            },
        }
        run = m.AgentRun(
            id=run_id,
            project_id=version.project_id,
            version_id=version.id,
            conversation_id=conversation.id,
            thread_id="agent:" + str(run_id),
            prompt=safe_prompt(payload.prompt),
            provider_binding=binding.model_dump(mode="json"),
            status="queued",
            step_count=0,
            repair_count=0,
            error_message=None,
            checkpoint_ref=None,
            request_context=context,
        )
        session.add(run)
        session.add(
            m.ConversationMessage(
                id=uuid4(),
                project_id=version.project_id,
                conversation_id=conversation.id,
                role="user",
                content=run.prompt,
                seq_no=1,
            )
        )
        version.revision += 1
        version.updated_at = utcnow()
        self.ledger.append(
            session, version.project_id, actor, "generation_requested", "agent_run", run.id
        )
        session.flush()
        return public_record(run, c.AgentRunResponse)

    def answer(
        self,
        session: Session,
        actor: ActorContext,
        run_id: UUID,
        payload: c.ClarificationAnswerRequest,
    ) -> dict[str, Any]:
        run = project_entity(session, m.AgentRun, run_id, actor, "answer_clarification", lock=True)
        if run.status != "waiting_input":
            raise ApplicationError("STATE_CONFLICT", "生成任务当前不等待回答", 409)
        pending = session.scalars(
            select(m.Clarification)
            .where(m.Clarification.agent_run_id == run.id, m.Clarification.answer.is_(None))
            .with_for_update()
        ).all()
        by_key = {question.question_key: question for question in pending}
        if any(answer.question_key not in by_key for answer in payload.answers):
            raise ApplicationError("STATE_CONFLICT", "问题不存在、已回答或不属于当前任务", 409)
        for answer in payload.answers:
            question = by_key[answer.question_key]
            question.answer = safe_prompt(answer.answer)
            question.answered_by = actor.user_id
            question.answered_at = utcnow()
            question.updated_at = utcnow()
            question.revision += 1
        if len(payload.answers) == len(pending):
            run.status = "queued"
        run.revision += 1
        run.updated_at = utcnow()
        # Content is already credential-redacted and never contains API secrets.
        next_seq = (
            session.scalar(
                select(func.max(m.ConversationMessage.seq_no)).where(
                    m.ConversationMessage.conversation_id == run.conversation_id
                )
            )
            or 0
        ) + 1
        session.add(
            m.ConversationMessage(
                id=uuid4(),
                project_id=run.project_id,
                conversation_id=run.conversation_id,
                role="user",
                seq_no=next_seq,
                content="\n".join(
                    answer.question_key + ": " + safe_prompt(answer.answer)
                    for answer in payload.answers
                ),
            )
        )
        self.ledger.append(
            session, run.project_id, actor, "clarification_answered", "agent_run", run.id
        )
        session.flush()
        return public_record(run, c.AgentRunResponse)

    def _context(
        self, session: Session, run: m.AgentRun
    ) -> tuple[ActorContext, ModelContext, dict[str, str]]:
        conversation = session.get(m.Conversation, run.conversation_id)
        user = session.get(m.User, conversation.creator_id) if conversation else None
        if user is None or user.status != "enabled":
            raise ApplicationError("FORBIDDEN", "原生成主体已不可用", 403)
        actor = actor_from_user(session, user)
        assert_action(actor, run.project_id, "generate")
        binding = c.ProviderBinding.model_validate(run.provider_binding)
        initial = run.request_context
        if (
            not isinstance(initial, dict)
            or not initial.get("source_profile_ids")
            or not initial.get("target_profile_id")
        ):
            raise unavailable("生成请求持久化上下文")
        sources, target = profile_context(
            session,
            actor,
            [UUID(value) for value in initial["source_profile_ids"]],
            UUID(initial["target_profile_id"]),
            run.project_id,
        )
        if any(
            initial["profile_digests"].get(str(profile.id)) != profile.profile_digest
            for profile in [*sources, target]
        ):
            raise ApplicationError("FINGERPRINT_CHANGED", "已选Profile内容已变化", 409)

        # No masked sample rows or credentials are needed in a generation prompt.
        def projection(profile: m.MetadataProfile) -> dict[str, Any]:
            result = {
                "id": str(profile.id),
                "resource_name": profile.resource_name,
                "fields": profile.fields,
                "profile_digest": profile.profile_digest,
            }
            if profile.connection_id:
                connection = project_entity(session, m.Connection, profile.connection_id, actor)
                if connection.secret_ref_id:
                    secret = project_entity(
                        session, m.SecretReference, connection.secret_ref_id, actor
                    )
                    result["secret_alias"] = secret.public_alias
                    result["secret_ref_id"] = str(secret.id)
            return result

        questions = session.scalars(
            select(m.Clarification).where(m.Clarification.agent_run_id == run.id)
        ).all()
        answers = {
            question.question_key: question.answer
            for question in questions
            if question.answer is not None
        }
        version = session.get(m.PipelineVersion, run.version_id)
        report = (
            session.get(m.ValidationReport, version.validation_report_id)
            if version and version.validation_report_id
            else None
        )
        issues = report.issues if report and not report.passed else []
        if not isinstance(issues, list):
            raise invalid("校验报告结构无效")
        context = ModelContext(
            prompt=run.prompt,
            provider_binding=binding,
            source_profiles=[projection(profile) for profile in sources],
            target_profile=projection(target),
            answers=answers,
            validation_issues=issues,
            repair_count=run.repair_count,
        )
        return actor, context, answers

    def process(self, run_id: UUID, session_factory: Callable[[], Session]) -> None:
        """Worker entry point. PostgreSQL session advisory lock serializes one run.

        The guard connection holds no row transaction while the provider is called.
        All business sessions are closed before external model/checkpoint work.
        """
        with session_factory() as probe:
            bind = probe.get_bind()
            if bind.dialect.name != "postgresql":
                raise unavailable("PostgreSQL生成任务认领")
            engine = bind.engine
        lock_id = int.from_bytes(
            hashlib.sha256(b"generation:" + run_id.bytes).digest()[:8], "big", signed=True
        )
        with engine.connect() as guard:
            locked = guard.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_id})
            guard.commit()
            if not locked:
                return
            try:
                self._process_locked(run_id, session_factory)
            except Exception as exc:
                # Do not persist/log model response, credentials or arbitrary exception text.
                message = (
                    exc.message
                    if isinstance(exc, ApplicationError)
                    else "生成流程失败，未形成可执行版本"
                )
                with session_factory() as session, session.begin():
                    run = session.scalar(
                        select(m.AgentRun).where(m.AgentRun.id == run_id).with_for_update()
                    )
                    if run and run.status not in {"succeeded", "failed"}:
                        run.status = "failed"
                        run.error_message = message
                        run.revision += 1
                        run.updated_at = utcnow()
                        self.ledger.append(
                            session,
                            run.project_id,
                            None,
                            "generation_failed",
                            "agent_run",
                            run.id,
                            {
                                "code": exc.code
                                if isinstance(exc, ApplicationError)
                                else "INTERNAL_ERROR"
                            },
                        )
                raise
            finally:
                guard.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_id})
                guard.commit()

    def _process_locked(self, run_id: UUID, session_factory: Callable[[], Session]) -> None:
        while True:
            with session_factory() as session, session.begin():
                run = session.scalar(
                    select(m.AgentRun).where(m.AgentRun.id == run_id).with_for_update()
                )
                if run is None or run.status in {"succeeded", "failed", "waiting_input"}:
                    return
                actor, context, answers = self._context(session, run)
                run.status = "repairing" if run.repair_count else "running"
                run.revision += 1
                run.updated_at = utcnow()
                thread_id = run.thread_id
            output = TypeAdapter(ModelOutput).validate_python(
                self.workflow.run(thread_id, context, answers=answers or None)
            )
            with session_factory() as session, session.begin():
                run = session.scalar(
                    select(m.AgentRun).where(m.AgentRun.id == run_id).with_for_update()
                )
                assert run is not None
                actor, current, _ = self._context(session, run)
                if (
                    current.provider_binding != context.provider_binding
                    or current.repair_count != context.repair_count
                ):
                    raise ApplicationError("FINGERPRINT_CHANGED", "生成任务绑定已变化", 409)
                version = project_entity(
                    session, m.PipelineVersion, run.version_id, actor, "generate", lock=True
                )
                if version.is_immutable:
                    raise ApplicationError("STATE_CONFLICT", "冻结版本不能写入模型输出", 409)
                run.checkpoint_ref = "postgres:" + thread_id
                run.step_count += 1
                run.revision += 1
                run.updated_at = utcnow()
                if isinstance(output, ClarificationOutput):
                    existing = set(
                        session.scalars(
                            select(m.Clarification.question_key).where(
                                m.Clarification.agent_run_id == run.id
                            )
                        ).all()
                    )
                    if any(question.question_key in existing for question in output.questions):
                        raise invalid("模型复用了已处理的问题键")
                    for question in output.questions:
                        session.add(
                            m.Clarification(
                                id=uuid4(),
                                project_id=run.project_id,
                                agent_run_id=run.id,
                                question_key=question.question_key,
                                question=safe_prompt(question.question),
                                answer=None,
                                answered_by=None,
                                answered_at=None,
                            )
                        )
                    run.status = "waiting_input"
                    self.ledger.append(
                        session,
                        run.project_id,
                        None,
                        "generation_waiting_input",
                        "agent_run",
                        run.id,
                    )
                    return
                plan = output.etl_plan.model_dump(mode="json")
                initial = run.request_context
                if not isinstance(initial, dict):
                    raise unavailable("生成请求持久化上下文")
                expected_sources = set(initial["source_profile_ids"])
                if (
                    set(plan["source_profile_ids"]) != expected_sources
                    or plan["target_profile_id"] != initial["target_profile_id"]
                ):
                    raise invalid("模型扩大或改变了已选择Profile范围")
                if safe_prompt(output.hocon_text) != output.hocon_text:
                    raise invalid("模型HOCON包含禁止保存的凭据赋值")
                gate = validate_materials(session, actor, version, plan, output.hocon_text)
                root, _ = artifact_package(artifacts_for(plan, output.hocon_text))
                version.etl_plan_json = plan
                version.hocon_text = output.hocon_text
                version.artifact_digest = None
                version.revision += 1
                version.updated_at = utcnow()
                report = m.ValidationReport(
                    id=uuid4(),
                    project_id=version.project_id,
                    version_id=version.id,
                    content_digest=root,
                    rule_version=gate.rule_version,
                    passed=gate.passed,
                    issues=[asdict(issue) for issue in gate.issues],
                )
                session.add(report)
                session.flush()
                version.validation_report_id = report.id
                if gate.passed:
                    run.status = "succeeded"
                    run.error_message = None
                elif (
                    run.repair_count >= 3
                    or not gate.issues
                    or not all(issue.repairable for issue in gate.issues)
                ):
                    run.status = "failed"
                    run.error_message = "确定性门禁未通过，修复已耗尽或问题不可自动修复"
                else:
                    # Reserve the attempt before a model call; restart never resets it.
                    run.repair_count += 1
                    run.status = "queued"
                self.ledger.append(
                    session,
                    run.project_id,
                    None,
                    "generation_validated",
                    "agent_run",
                    run.id,
                    {
                        "passed": gate.passed,
                        "repair_count": run.repair_count,
                        "content_digest": root,
                    },
                )
                if run.status != "queued":
                    return
