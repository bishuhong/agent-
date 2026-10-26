"""Atomic command creation, Ed25519 authorization and Worker-only consumption."""

import json
import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from etl_agent.api.schemas import contracts as c
from etl_agent.config import Settings
from etl_agent.infrastructure.persistence import models as m
from etl_agent.infrastructure.persistence.access import (
    compare_revision,
    project_entity,
    required_record,
)
from etl_agent.modules.audit.service import EvidenceLedger
from etl_agent.modules.governance.service import GovernanceService
from etl_agent.modules.identity_access.domain import assert_action
from etl_agent.modules.identity_access.service import actor_from_user
from etl_agent.shared.canonical import canonical_bytes, decode_url, digest, encode_url
from etl_agent.shared.context import ActorContext, RequestContext, WorkerContext
from etl_agent.shared.errors import ApplicationError, forbidden, invalid, unavailable
from etl_agent.shared.security import CapabilitySigner, require_key


@dataclass(frozen=True)
class ClaimedCommand:
    command_id: UUID
    execution_run_id: UUID
    tool: str
    recovering: bool
    completed: bool


class CommandAuthorizer:
    def __init__(self, settings: Settings, signer: CapabilitySigner, ledger: EvidenceLedger):
        self.settings, self.signer, self.ledger = settings, signer, ledger

    def _cipher(self) -> AESGCM:
        # Domain-separated standard key derivation; capability signing uses a separate Ed25519 key.
        import hmac

        key = hmac.digest(
            require_key(self.settings.session_key.get_secret_value(), "授权存储密钥"),
            b"etl-agent-capability-storage-v1",
            "sha256",
        )
        return AESGCM(key)

    def open_claims(self, authorization: m.CapabilityAuthorization) -> dict:
        sealed = decode_url(authorization.encrypted_token)
        try:
            plain = self._cipher().decrypt(
                sealed[:12], sealed[12:], str(authorization.command_id).encode()
            )
            value = json.loads(plain)
        except Exception as exc:
            raise forbidden("受管授权记录不能解密或验证") from exc
        if digest(value) != authorization.token_digest:
            raise forbidden("受管授权材料摘要不匹配")
        return value

    def create(
        self,
        session: Session,
        actor: ActorContext,
        preparation: m.Preparation,
        run: m.ExecutionRun,
        tool: str,
        key: str | None,
        *,
        parent_command_id: UUID | None = None,
        case_id: UUID | None = None,
    ) -> m.ManagedCommand:
        if key is None or not 1 <= len(key) <= 256:
            raise invalid("命令必须具有有效的幂等键")
        version = required_record(session, m.PipelineVersion, preparation.version_id)
        now = session.execute(select(func.clock_timestamp())).scalar_one()
        command_id, capability_id = uuid4(), uuid4()
        scopes = [
            {**scope, "operations": [tool]}
            for scope in preparation.resource_scopes
            if tool in scope.get("operations", [])
        ]
        if tool == "run_l2_benchmark":
            # Case identity is part of the atomically persisted binding; command
            # construction receives it on the run only through this local call.
            if case_id is None or preparation.evaluation_binding is None:
                raise forbidden("L2命令缺少精确用例关联")
            all_cases = session.scalars(
                select(m.BenchmarkCase).where(
                    m.BenchmarkCase.dataset_id
                    == UUID(preparation.evaluation_binding["dataset_id"]),
                    m.BenchmarkCase.level == "L2",
                )
            ).all()
            case_suffixes = tuple("_" + item.id.hex for item in all_cases)
            selected_suffix = "_" + case_id.hex
            base_shadow = preparation.rollback_plan["shadow_resource"]
            scopes = [
                scope
                for scope in scopes
                if scope["resource_name"] != base_shadow
                and (
                    not scope["resource_name"].endswith(case_suffixes)
                    or scope["resource_name"].endswith(selected_suffix)
                )
            ]
        if not scopes:
            raise forbidden("工具不在冻结的批准范围内")
        claims = {
            "schema_version": 1,
            "capability_id": capability_id,
            "command_id": command_id,
            "subject_id": actor.user_id,
            "project_id": preparation.project_id,
            "preparation_id": preparation.id,
            "authorization_basis": "execution_receipt" if parent_command_id else "preparation",
            "parent_command_id": parent_command_id,
            "tool": tool,
            "environment": preparation.environment,
            "resource_scopes": scopes,
            "artifact_digest": version.artifact_digest,
            "input_fingerprint": preparation.input_fingerprint,
            "issued_at": now,
            "expires_at": now + timedelta(minutes=5),
        }
        signed = json.loads(canonical_bytes(self.signer.sign(claims)))
        nonce = secrets.token_bytes(12)
        sealed = self._cipher().encrypt(nonce, canonical_bytes(signed), str(command_id).encode())
        token_hash = digest(signed)
        if tool in {"execute_etl", "run_l2_benchmark"}:
            run.capability_token_digest = token_hash
        command = m.ManagedCommand(
            id=command_id,
            project_id=preparation.project_id,
            execution_run_id=run.id,
            preparation_id=preparation.id,
            tool=tool,
            capability_id=capability_id,
            authorization_ref=f"db:{capability_id}",
            payload_digest=digest(
                {"run_id": run.id, "tool": tool, "fingerprint": preparation.input_fingerprint}
            ),
            status="pending",
            idempotency_key=key,
        )
        authorization = m.CapabilityAuthorization(
            capability_id=capability_id,
            project_id=preparation.project_id,
            command_id=command_id,
            subject_id=actor.user_id,
            preparation_id=preparation.id,
            authorization_basis=claims["authorization_basis"],
            parent_command_id=parent_command_id,
            tool=tool,
            environment=preparation.environment,
            resource_scopes=scopes,
            artifact_digest=version.artifact_digest,
            input_fingerprint=preparation.input_fingerprint,
            issued_at=now,
            expires_at=now + timedelta(minutes=5),
            key_id=signed["key_id"],
            encrypted_token=encode_url(nonce + sealed),
            token_digest=token_hash,
        )
        session.add_all(
            [
                command,
                authorization,
                m.OutboxMessage(
                    project_id=preparation.project_id,
                    command_id=command_id,
                    event_type="command_ready",
                    delivery_status="pending",
                    delivery_attempts=0,
                    next_attempt_at=now,
                ),
            ]
        )
        session.flush()
        self.ledger.append(
            session,
            preparation.project_id,
            actor,
            "command_authorized",
            "command",
            command_id,
            {"tool": tool, "token_digest": token_hash},
        )
        return command


class CommitCoordinator:
    def __init__(
        self,
        settings: Settings,
        governance: GovernanceService,
        authorizer: CommandAuthorizer,
        ledger: EvidenceLedger,
    ):
        self.settings, self.governance, self.authorizer, self.ledger = (
            settings,
            governance,
            authorizer,
            ledger,
        )

    def _lock_project(self, session: Session, project_id: UUID) -> None:
        session.scalar(select(m.Project).where(m.Project.id == project_id).with_for_update())

    def _idempotency(
        self,
        session: Session,
        actor: ActorContext,
        project_id: UUID,
        action: str,
        payload: dict,
        context: RequestContext,
    ) -> tuple[m.ApiIdempotencyRecord, bool]:
        if not context.idempotency_key or len(context.idempotency_key) > 256:
            raise invalid("必须提供1—256字符的Idempotency-Key", "Idempotency-Key")
        self._lock_project(session, project_id)
        request_hash = digest(payload)
        record = session.scalar(
            select(m.ApiIdempotencyRecord).where(
                m.ApiIdempotencyRecord.project_id == project_id,
                m.ApiIdempotencyRecord.subject_id == actor.user_id,
                m.ApiIdempotencyRecord.action == action,
                m.ApiIdempotencyRecord.key == context.idempotency_key,
            )
        )
        if record:
            if record.request_digest != request_hash:
                raise ApplicationError("IDEMPOTENCY_MISMATCH", "同一幂等键不能用于不同请求", 409)
            return record, True
        now = session.execute(select(func.clock_timestamp())).scalar_one()
        record = m.ApiIdempotencyRecord(
            project_id=project_id,
            subject_id=actor.user_id,
            action=action,
            key=context.idempotency_key,
            request_digest=request_hash,
            expires_at=now + timedelta(hours=24),
        )
        session.add(record)
        return record, False

    def commit(
        self,
        session: Session,
        actor: ActorContext,
        preparation_id: UUID,
        payload: c.CommitRequest,
        context: RequestContext,
        *,
        original_run_id: UUID | None = None,
    ) -> dict:
        prep = project_entity(session, m.Preparation, preparation_id, actor, "commit")
        if prep.purpose not in {"execute_etl", "isolated_trial"}:
            raise invalid("该用途必须通过对应回滚或L2入口提交")
        record, repeat = self._idempotency(
            session,
            actor,
            prep.project_id,
            "commit",
            {
                "preparation_id": prep.id,
                "fingerprint": payload.expected_input_fingerprint,
                "original_run_id": original_run_id,
            },
            context,
        )
        if repeat and record.resource_id:
            previous = required_record(session, m.ExecutionRun, record.resource_id)
            return {"execution_run_id": str(previous.id), "status": previous.status}
        prep = session.scalars(
            select(m.Preparation).where(m.Preparation.id == prep.id).with_for_update()
        ).one()
        previous_run = session.scalar(
            select(m.ExecutionRun.id).where(m.ExecutionRun.preparation_id == prep.id).limit(1)
        )
        if previous_run:
            raise ApplicationError(
                "STATE_CONFLICT", "该准备单已提交；新运行必须创建新的影子资源与准备授权", 409
            )
        self.governance.assert_fresh(session, prep)
        self.governance.assert_approved(session, prep)
        if prep.input_fingerprint != payload.expected_input_fingerprint:
            raise ApplicationError("FINGERPRINT_CHANGED", "提交指纹不是已审查的冻结事实", 409)
        run = m.ExecutionRun(
            id=uuid4(),
            project_id=prep.project_id,
            version_id=prep.version_id,
            preparation_id=prep.id,
            capability_token_digest="0" * 64,
            status="queued",
            original_run_id=original_run_id,
            metrics=c.RuntimeMetrics().model_dump(mode="json"),
        )
        session.add(run)
        self.authorizer.create(session, actor, prep, run, "execute_etl", context.idempotency_key)
        record.resource_id = run.id
        session.flush()
        self.ledger.append(
            session, prep.project_id, actor, "commit_accepted", "execution_run", run.id
        )
        return {"execution_run_id": str(run.id), "status": run.status}

    def operation(
        self,
        session: Session,
        actor: ActorContext,
        run_id: UUID,
        kind: str,
        expected_revision: int,
        context: RequestContext,
        *,
        preparation_id: UUID | None = None,
        publication_id: UUID | None = None,
    ) -> m.RunOperation:
        run = project_entity(session, m.ExecutionRun, run_id, actor, kind)
        record, repeat = self._idempotency(
            session,
            actor,
            run.project_id,
            kind,
            {
                "run_id": run.id,
                "expected_revision": expected_revision,
                "preparation_id": preparation_id,
                "publication_id": publication_id,
            },
            context,
        )
        if repeat and record.resource_id:
            return required_record(session, m.RunOperation, record.resource_id)
        run = session.scalars(
            select(m.ExecutionRun).where(m.ExecutionRun.id == run_id).with_for_update()
        ).one()
        compare_revision(run.revision, expected_revision)
        parent_id = None
        if kind == "rollback":
            if run.status not in {"succeeded", "failed", "cancelled"}:
                raise ApplicationError(
                    "STATE_CONFLICT", "必须先确认搬运已终止，再申请资源清理或恢复", 409
                )
            if preparation_id is None:
                raise invalid("人工回滚必须有新准备单", "preparation_id")
            prep = project_entity(session, m.Preparation, preparation_id, actor)
            if prep.purpose != "rollback" or prep.execution_run_id != run.id:
                raise forbidden("准备单不属于该运行的回滚用途")
            self.governance.assert_fresh(session, prep)
            self.governance.assert_approved(session, prep)
            publication = (
                session.get(m.PublicationRecord, publication_id) if publication_id else None
            )
            published = session.scalar(
                select(m.PublicationRecord).where(
                    m.PublicationRecord.execution_run_id == run.id,
                    m.PublicationRecord.status == "published",
                )
            )
            if published and (publication is None or publication.id != published.id):
                raise invalid("发布后回滚必须指定准确publication_id")
            if publication:
                now = session.execute(select(func.clock_timestamp())).scalar_one()
                if (
                    publication.execution_run_id != run.id
                    or publication.retained_until is None
                    or now >= publication.retained_until
                ):
                    raise ApplicationError(
                        "ROLLBACK_UNAVAILABLE", "发布保留资源已不可用于回滚", 409
                    )
                head = session.get(
                    m.TargetHead, (run.project_id, publication.target_key), with_for_update=True
                )
                if (
                    head is None
                    or head.current_publication_id != publication.id
                    or head.generation != publication.published_generation
                ):
                    raise ApplicationError(
                        "PUBLICATION_CONFLICT", "目标已有后续发布，不能自动覆盖", 409
                    )
        else:
            if kind in {"pause", "resume"} and kind not in self.settings.seatunnel_capabilities:
                raise unavailable(f"引擎{kind}能力")
            allowed = {
                "cancel": {"queued", "authorizing", "running", "paused", "reconciling"},
                "pause": {"running"},
                "resume": {"paused"},
            }
            if run.status not in allowed.get(kind, set()):
                raise ApplicationError("STATE_CONFLICT", "当前运行阶段不允许此操作", 409)
            prep = required_record(session, m.Preparation, run.preparation_id)
            parent = session.scalar(
                select(m.ManagedCommand)
                .where(
                    m.ManagedCommand.execution_run_id == run.id,
                    m.ManagedCommand.tool.in_(["execute_etl", "run_l2_benchmark"]),
                )
                .order_by(m.ManagedCommand.created_at)
                .limit(1)
            )
            consumed = (
                session.get(m.CapabilityConsumption, parent.capability_id) if parent else None
            )
            if consumed:
                now = session.execute(select(func.clock_timestamp())).scalar_one()
                started = run.started_at or consumed.consumed_at
                deadline = started + timedelta(
                    milliseconds=int(prep.supervision_contract["max_duration_ms"]), minutes=30
                )
                if now >= deadline:
                    raise ApplicationError(
                        "CAPABILITY_EXPIRED", "执行收据已超出批准的收尾期限", 409
                    )
                assert parent is not None
                parent_id = parent.id
                self.governance.assert_fresh(session, prep, allow_expired=True)
                self.governance.assert_approved(session, prep)
            else:
                self.governance.assert_fresh(session, prep)
                self.governance.assert_approved(session, prep)
        command = self.authorizer.create(
            session, actor, prep, run, kind, context.idempotency_key, parent_command_id=parent_id
        )
        operation = m.RunOperation(
            id=uuid4(),
            project_id=run.project_id,
            execution_run_id=run.id,
            kind=kind,
            command_id=command.id,
            requested_by=actor.user_id,
            status="requested",
            publication_id=publication_id,
        )
        session.add(operation)
        if kind == "cancel":
            run.status = "cancel_requested"
            run.revision += 1
        record.resource_id = operation.id
        session.flush()
        self.ledger.append(
            session,
            run.project_id,
            actor,
            "operation_requested",
            "operation",
            operation.id,
            {"kind": kind, "run_id": run.id},
        )
        return operation

    def commit_benchmark(
        self,
        session: Session,
        actor: ActorContext,
        payload: c.BenchmarkRunRequest,
        context: RequestContext,
    ) -> dict:
        from etl_agent.modules.evaluation.service import dataset_digest

        if payload.preparation_id is None:
            raise invalid("L2必须提供准备单")
        prep = project_entity(
            session, m.Preparation, payload.preparation_id, actor, "run_benchmark"
        )
        self._lock_project(session, prep.project_id)
        prep = session.scalars(
            select(m.Preparation).where(m.Preparation.id == prep.id).with_for_update()
        ).one()
        existing = session.scalar(
            select(m.BenchmarkRun).where(m.BenchmarkRun.preparation_id == prep.id)
        )
        if existing:
            return public_benchmark(existing)
        if prep.purpose != "run_l2_benchmark" or prep.project_id != payload.project_id:
            raise forbidden("L2准备用途或项目不匹配")
        self.governance.assert_fresh(session, prep)
        self.governance.assert_approved(session, prep)
        binding = prep.evaluation_binding or {}
        if binding.get("dataset_id") != str(payload.dataset_id) or binding.get(
            "provider_binding"
        ) != payload.provider_binding.model_dump(mode="json"):
            raise ApplicationError("FINGERPRINT_CHANGED", "评测请求与批准的绑定不同", 409)
        dataset = required_record(session, m.BenchmarkDataset, payload.dataset_id)
        cases_all = session.scalars(
            select(m.BenchmarkCase).where(m.BenchmarkCase.dataset_id == payload.dataset_id)
        ).all()
        if dataset_digest(dataset, cases_all) != binding.get("dataset_digest"):
            raise ApplicationError("FINGERPRINT_CHANGED", "评测用例集内容已变化", 409)
        cases = [case for case in cases_all if case.level == "L2"]
        if not cases or any(case.input.get("version_id") != str(prep.version_id) for case in cases):
            raise invalid("L2用例集与冻结被测版本不一致")
        if len(cases) > 1 and not {"isolated_benchmark_resources", "aggregate_budgets"}.issubset(
            self.settings.seatunnel_capabilities
        ):
            raise unavailable("经验证的L2逐例隔离及批次总预算能力")
        benchmark = m.BenchmarkRun(
            id=uuid4(),
            project_id=prep.project_id,
            dataset_id=dataset.id,
            preparation_id=prep.id,
            level="L2",
            provider_binding=binding["provider_binding"],
            status="queued",
        )
        session.add(benchmark)
        for case in cases:
            run = m.ExecutionRun(
                id=uuid4(),
                project_id=prep.project_id,
                version_id=prep.version_id,
                preparation_id=prep.id,
                capability_token_digest="0" * 64,
                status="queued",
                metrics=c.RuntimeMetrics().model_dump(mode="json"),
            )
            session.add(run)
            self.authorizer.create(
                session,
                actor,
                prep,
                run,
                "run_l2_benchmark",
                f"benchmark:{benchmark.id}:{case.id}",
                case_id=case.id,
            )
            session.add(
                m.BenchmarkCaseExecution(
                    project_id=prep.project_id,
                    benchmark_run_id=benchmark.id,
                    case_id=case.id,
                    execution_run_id=run.id,
                )
            )
        session.flush()
        self.ledger.append(
            session, prep.project_id, actor, "benchmark_accepted", "benchmark", benchmark.id
        )
        return public_benchmark(benchmark)


def public_benchmark(row: m.BenchmarkRun) -> dict:
    from etl_agent.infrastructure.persistence.access import public_record

    return public_record(row, c.BenchmarkRunResponse)


class ReplayGuard:
    def __init__(
        self, governance: GovernanceService, authorizer: CommandAuthorizer, ledger: EvidenceLedger
    ):
        self.governance, self.authorizer, self.ledger = governance, authorizer, ledger

    def consume(
        self, session: Session, command_id: UUID, worker: WorkerContext, *, recover: bool = False
    ) -> ClaimedCommand:
        command = session.get(m.ManagedCommand, command_id)
        if command is None:
            raise ApplicationError("RESOURCE_NOT_FOUND", "受管命令不存在", 404)
        session.scalar(
            select(m.Project).where(m.Project.id == command.project_id).with_for_update()
        )
        command = session.scalars(
            select(m.ManagedCommand).where(m.ManagedCommand.id == command_id).with_for_update()
        ).one()
        authorization = session.get(m.CapabilityAuthorization, command.capability_id)
        if authorization is None:
            raise forbidden("受管命令没有授权事实")
        consumption = session.get(m.CapabilityConsumption, command.capability_id)
        now = session.execute(select(func.clock_timestamp())).scalar_one()
        signed = self.authorizer.open_claims(authorization)
        if consumption:
            if not recover:
                raise ApplicationError("CAPABILITY_REPLAYED", "该能力已经消费", 409)
            self.authorizer.signer.verify_signature(signed)
            if command.status == "completed":
                return ClaimedCommand(
                    command.id, command.execution_run_id, command.tool, True, True
                )
            run = required_record(session, m.ExecutionRun, command.execution_run_id)
            if run.status in {"cancelled", "failed", "authorization_expired"} and command.tool in {
                "execute_etl",
                "run_l2_benchmark",
            }:
                raise ApplicationError("STATE_CONFLICT", "终态运行只能对账，不能重新提交引擎", 409)
            if (
                command.lease_until
                and now < command.lease_until
                and command.claim_owner != worker.worker_id
            ):
                raise ApplicationError("STATE_CONFLICT", "命令仍由其他Worker认领", 409)
        else:
            self.authorizer.signer.verify(signed, now)
        expected = {
            "command_id": str(command.id),
            "capability_id": str(command.capability_id),
            "project_id": str(command.project_id),
            "preparation_id": str(command.preparation_id),
            "tool": command.tool,
            "subject_id": str(authorization.subject_id),
        }
        if any(signed.get(name) != value for name, value in expected.items()):
            raise forbidden("能力与命令绑定不一致")
        run = required_record(session, m.ExecutionRun, command.execution_run_id)
        prep = required_record(session, m.Preparation, command.preparation_id)
        version = required_record(session, m.PipelineVersion, prep.version_id)
        if (
            run.project_id != command.project_id
            or run.version_id != prep.version_id
            or (command.tool != "rollback" and run.preparation_id != prep.id)
        ):
            raise forbidden("运行与命令的项目、版本或准备关联不一致")
        expected_payload = digest(
            {"run_id": run.id, "tool": command.tool, "fingerprint": prep.input_fingerprint}
        )
        if command.payload_digest != expected_payload:
            raise forbidden("命令载荷摘要不一致")
        if (
            signed["artifact_digest"] != version.artifact_digest
            or signed["input_fingerprint"] != prep.input_fingerprint
        ):
            raise ApplicationError("FINGERPRINT_CHANGED", "签名制品或输入事实不匹配", 409)
        if (
            signed["environment"] != prep.environment
            or signed["resource_scopes"] != authorization.resource_scopes
        ):
            raise forbidden("环境或资源绑定不一致")
        subject = session.get(m.User, authorization.subject_id)
        if subject is None or subject.status != "enabled":
            raise forbidden("授权主体已停用")
        actor = actor_from_user(session, subject)
        action = {
            "execute_etl": "commit",
            "run_l2_benchmark": "run_benchmark",
            "publish": "commit",
        }.get(command.tool, command.tool)
        assert_action(actor, command.project_id, action)
        receipt = authorization.authorization_basis == "execution_receipt"
        self.governance.assert_fresh(
            session, prep, allow_expired=receipt or consumption is not None
        )
        self.governance.assert_approved(session, prep)
        if receipt:
            parent = session.get(m.ManagedCommand, authorization.parent_command_id)
            parent_consumption = (
                session.get(m.CapabilityConsumption, parent.capability_id) if parent else None
            )
            if parent is None or parent_consumption is None or parent.execution_run_id != run.id:
                raise forbidden("后续能力没有同运行的有效父消费收据")
            deadline = (run.started_at or parent_consumption.consumed_at) + timedelta(
                milliseconds=int(prep.supervision_contract["max_duration_ms"]), minutes=30
            )
            if now >= deadline:
                raise ApplicationError("CAPABILITY_EXPIRED", "后续命令已超过收尾授权期限", 409)
        if consumption is None:
            session.add(
                m.CapabilityConsumption(
                    capability_id=command.capability_id,
                    project_id=command.project_id,
                    command_id=command.id,
                    execution_run_id=run.id,
                    token_digest=authorization.token_digest,
                    consumed_at=now,
                )
            )
            if command.tool in {"execute_etl", "run_l2_benchmark"}:
                if run.status != "queued":
                    raise ApplicationError("STATE_CONFLICT", "当前运行阶段不能首次启动", 409)
                run.status = "authorizing"
                run.revision += 1
        command.claim_owner = worker.worker_id
        command.lease_until = now + timedelta(seconds=60)
        command.status = "claimed" if not consumption else "reconciling"
        session.flush()
        self.ledger.append(
            session,
            command.project_id,
            actor,
            "command_recovered" if consumption else "capability_consumed",
            "command",
            command.id,
        )
        return ClaimedCommand(command.id, run.id, command.tool, consumption is not None, False)
