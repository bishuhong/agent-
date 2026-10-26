"""Versioned evaluation orchestration with explicit boundaries for real execution."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from time import perf_counter_ns
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from etl_agent.api.schemas import contracts as c
from etl_agent.config import Settings
from etl_agent.infrastructure.persistence import models as m
from etl_agent.infrastructure.persistence.access import (
    compare_revision,
    project_entity,
    public_record,
)
from etl_agent.modules.audit.service import EvidenceLedger
from etl_agent.modules.evaluation.domain import (
    MetricResult,
    compare_expectation,
    compute_metrics,
    validate_evolution_authorization,
)
from etl_agent.modules.identity_access.domain import assert_action
from etl_agent.modules.quality.domain import evaluate_records
from etl_agent.modules.supervision.domain import evaluate_budget
from etl_agent.modules.validation.domain import parse_safe_hocon, validate_candidate
from etl_agent.shared.canonical import digest
from etl_agent.shared.context import ActorContext, RequestContext, utcnow
from etl_agent.shared.errors import ApplicationError, invalid, unavailable

L2Submit = Callable[[Session, ActorContext, c.BenchmarkRunRequest, RequestContext], dict[str, Any]]
ObservationCallback = Callable[[Session, m.BenchmarkCase, m.BenchmarkRun], Mapping[str, Any]]


def dataset_digest(dataset: m.BenchmarkDataset, cases: Sequence[m.BenchmarkCase]) -> str:
    """Canonical v1 manifest, shared with Prepare and L2 Commit."""
    return digest(
        {
            "schema_version": 1,
            "name": dataset.name,
            "version": dataset.version,
            "cases": [
                {
                    "case_key": case.case_key,
                    "level": case.level,
                    "input": case.input,
                    "expected": case.expected,
                }
                for case in sorted(cases, key=lambda item: item.case_key)
            ],
        }
    )


def _metric_response(metric: MetricResult) -> dict[str, Any]:
    return c.MetricResult.model_validate(
        {
            "name": metric.name,
            "numerator": str(metric.numerator) if metric.numerator is not None else None,
            "denominator": str(metric.denominator) if metric.denominator is not None else None,
            "value": format(metric.value, "f") if metric.value is not None else None,
            "risk_level": metric.risk_level,
        }
    ).model_dump(mode="json")


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise invalid(f"{name}必须为已定义的结构化对象")
    return value


def _fields(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise invalid("Profile字段集合无效")
    return value


class BenchmarkService:
    def __init__(
        self,
        settings: Settings,
        ledger: EvidenceLedger,
        l2_submit: L2Submit | None = None,
        observation_callback: ObservationCallback | None = None,
    ) -> None:
        self.settings = settings
        self.ledger = ledger
        self.l2_submit = l2_submit
        self.observation_callback = observation_callback

    def start(
        self,
        session: Session,
        actor: ActorContext,
        request: c.BenchmarkRunRequest,
        ctx: RequestContext,
    ) -> dict[str, Any]:
        assert_action(actor, request.project_id, "run_benchmark")
        dataset = project_entity(
            session, m.BenchmarkDataset, request.dataset_id, actor, "run_benchmark"
        )
        if dataset.project_id != request.project_id:
            raise invalid("评测数据集必须属于请求项目")
        cases = list(
            session.scalars(
                select(m.BenchmarkCase)
                .where(m.BenchmarkCase.dataset_id == dataset.id)
                .order_by(m.BenchmarkCase.case_key)
            )
        )
        if dataset_digest(dataset, cases) != dataset.content_digest:
            raise ApplicationError("FINGERPRINT_CHANGED", "评测数据集内容摘要不匹配", 409)
        selected = [case for case in cases if case.level == request.level]
        if not selected:
            raise invalid("用例集不包含指定层级的可执行用例")
        for case in selected:
            c.BenchmarkInput.model_validate(case.input)
            c.BenchmarkExpectation.model_validate(case.expected)
        if request.level == "L2":
            if self.l2_submit is None:
                raise unavailable("L2受管提交协调器")
            # The injected coordinator atomically creates BenchmarkRun, each
            # logical ExecutionRun, Command/Capability/Outbox and audit records.
            return self.l2_submit(session, actor, request, ctx)
        now = utcnow()
        run = m.BenchmarkRun(
            id=uuid4(),
            project_id=dataset.project_id,
            dataset_id=dataset.id,
            preparation_id=None,
            level=str(request.level),
            provider_binding=request.provider_binding.model_dump(mode="json"),
            status="queued",
            started_at=None,
            finished_at=None,
            created_at=now,
            updated_at=now,
            revision=1,
        )
        session.add(run)
        session.flush()
        self.ledger.append(
            session,
            run.project_id,
            actor,
            "benchmark.accepted",
            "benchmark_run",
            run.id,
            {
                "dataset_digest": dataset.content_digest,
                "level": run.level,
                "provider_binding": run.provider_binding,
            },
        )
        return public_record(run, c.BenchmarkRunResponse)

    def get(self, session: Session, actor: ActorContext, run_id: UUID) -> dict[str, Any]:
        return public_record(
            project_entity(session, m.BenchmarkRun, run_id, actor), c.BenchmarkRunResponse
        )

    def _case_observations(self, session: Session, run: m.BenchmarkRun) -> list[dict[str, Any]]:
        cases = list(
            session.scalars(
                select(m.BenchmarkCase)
                .where(
                    m.BenchmarkCase.dataset_id == run.dataset_id, m.BenchmarkCase.level == run.level
                )
                .order_by(m.BenchmarkCase.case_key)
            )
        )
        results = {
            result.case_id: result
            for result in session.scalars(
                select(m.BenchmarkCaseResult).where(
                    m.BenchmarkCaseResult.benchmark_run_id == run.id
                )
            )
        }
        return [
            {
                "case_id": str(case.id),
                "expected": case.expected,
                "actual": results[case.id].actual if case.id in results else {},
                "latency_ms": results[case.id].latency_ms if case.id in results else None,
            }
            for case in cases
        ]

    def report(self, session: Session, actor: ActorContext, run_id: UUID) -> dict[str, Any]:
        run = project_entity(session, m.BenchmarkRun, run_id, actor)
        results = list(
            session.scalars(
                select(m.BenchmarkCaseResult)
                .where(m.BenchmarkCaseResult.benchmark_run_id == run.id)
                .order_by(m.BenchmarkCaseResult.case_id)
            )
        )
        return {
            "run": public_record(run, c.BenchmarkRunResponse),
            "cases": [public_record(result, c.CaseResultResponse) for result in results],
            "metrics": [
                _metric_response(metric)
                for metric in compute_metrics(self._case_observations(session, run))
            ],
        }

    def _static_observation(
        self, session: Session, case: m.BenchmarkCase, run: m.BenchmarkRun
    ) -> dict[str, Any]:
        data = _object(case.input, "Benchmark输入")
        if not data.get("version_id"):
            raise unavailable("无版本用例的真实生成观察端口")
        version = session.get(m.PipelineVersion, UUID(str(data["version_id"])))
        if (
            version is None
            or version.project_id != run.project_id
            or not version.etl_plan_json
            or not version.hocon_text
        ):
            raise invalid("静态评测缺少同项目的有效版本制品")
        plan = _object(version.etl_plan_json, "EtlPlan")
        ids = [UUID(str(item)) for item in plan.get("source_profile_ids", [])]
        target_id = UUID(str(plan.get("target_profile_id")))
        profiles = list(
            session.scalars(
                select(m.MetadataProfile).where(
                    m.MetadataProfile.id.in_(ids + [target_id]),
                    m.MetadataProfile.project_id == run.project_id,
                )
            )
        )
        by_id = {profile.id: profile for profile in profiles}
        if set(ids + [target_id]) != set(by_id):
            raise invalid("评测引用的Profile不存在或跨项目")
        source_schema: dict[str, Any] = {}
        for profile_id in ids:
            for field in _fields(by_id[profile_id].fields):
                name = field["field_name"]
                if name in source_schema and source_schema[name] != field:
                    raise invalid("多源同名字段存在歧义，必须先明确映射")
                source_schema[name] = field
        target_schema = {field["field_name"]: field for field in _fields(by_id[target_id].fields)}
        resource_names = {profile.resource_name for profile in profiles}
        # Error destination authorization must be derived from a project-owned
        # reference, never merely from the candidate declaring its own address.
        error_scope = plan.get("quality_contract", {}).get("error_resource", {})
        reference_id = error_scope.get("connection_id") or error_scope.get("file_asset_id")
        if reference_id:
            reference: Any = (
                session.get(m.Connection, UUID(str(reference_id)))
                if error_scope.get("connection_id")
                else session.get(m.FileAsset, UUID(str(reference_id)))
            )
            if reference is not None and reference.project_id == run.project_id:
                resource_names.add(error_scope.get("resource_name", ""))
        now = utcnow()
        result = validate_candidate(
            version.hocon_text,
            plan,
            source_schema,
            target_schema,
            resource_names,
            set(),
            profiles_valid=all(profile.expires_at > now for profile in profiles),
        )
        return {
            "gate_pass": result.passed,
            "error_code": result.issues[0].code if result.issues else None,
            "risk_level": None,
            "schema_fields": sorted(
                {
                    mapping["target_field"]
                    for mapping in plan.get("field_mappings", [])
                    if isinstance(mapping, dict)
                    and isinstance(mapping.get("target_field"), str)
                    and mapping["target_field"] in target_schema
                    and mapping.get("target_type")
                    == target_schema[mapping["target_field"]].get("logical_type")
                }
            ),
            "evidence": [],
        }

    def _fault_observation(self, case: m.BenchmarkCase) -> tuple[dict[str, Any], str]:
        scenario = _object(case.input, "Benchmark输入").get("fault_scenario")
        if scenario == "unsafe_hocon":
            rejected = False
            try:
                parse_safe_hocon('include url("https://example.invalid/forbidden")')
            except ValueError:
                rejected = True
            observation: dict[str, Any] = {
                "gate_pass": not rejected,
                "error_code": "HOCON_INVALID" if rejected else None,
                "risk_level": "P0" if rejected else None,
                "schema_fields": [],
                "evidence": [],
            }
            return observation, "L1验证HOCON外部include被实际解析门禁拦截；未调用真实引擎"
        if scenario == "quality_rejection":
            contract = {
                "rules": [
                    {
                        "rule_id": "positive",
                        "field_name": "amount",
                        "kind": "positive",
                        "stage": "validate",
                        "error_code": "AMOUNT_POSITIVE",
                    }
                ],
                "error_resource": {"resource_ref": "isolated-errors"},
                "max_rejection_ratio": "0",
                "allow_empty_input": False,
            }
            result = evaluate_records([{"amount": "-1"}], contract, {"amount": "decimal"})
            return {
                "gate_pass": result.summary.publish_eligible,
                "error_code": "QUALITY_REJECTED" if not result.summary.publish_eligible else None,
                "risk_level": None,
                "schema_fields": ["amount"],
                "evidence": [],
            }, "L1质量解释器实际计算违规记录与拒绝发布条件；未写入真实数据源"
        if scenario == "budget_exceeded":
            budget = {
                "max_input_records": "10",
                "max_output_bytes": "100",
                "max_duration_ms": "1000",
                "max_amplification_ratio": "1",
                "max_rejection_ratio": "0",
                "on_exceed": "kill",
            }
            decision = evaluate_budget(
                budget,
                {
                    "input_records": "11",
                    "output_records": "11",
                    "output_bytes": "100",
                    "duration_ms": "10",
                    "error_records": "0",
                },
            )
            return {
                "gate_pass": decision.decision != "exceeded",
                "error_code": "BUDGET_EXCEEDED" if decision.decision == "exceeded" else None,
                "risk_level": None,
                "schema_fields": [],
                "evidence": [],
            }, "L1实际比较冻结预算并得到停止意图；不声称已停止真实引擎"
        raise unavailable("该故障场景的确定性仿真端口")

    def run_deterministic(
        self, session: Session, actor: ActorContext, run_id: UUID
    ) -> dict[str, Any]:
        run = project_entity(session, m.BenchmarkRun, run_id, actor, "run_benchmark", lock=True)
        if run.level == "L2":
            raise ApplicationError("STATE_CONFLICT", "L2只能从真实受管运行证据汇总", 409)
        if run.status in {"succeeded", "failed"}:
            return public_record(run, c.BenchmarkRunResponse)
        if run.status != "queued":
            raise ApplicationError("STATE_CONFLICT", "该评测已被其他执行器领取", 409)
        cases = list(
            session.scalars(
                select(m.BenchmarkCase)
                .where(
                    m.BenchmarkCase.dataset_id == run.dataset_id, m.BenchmarkCase.level == run.level
                )
                .order_by(m.BenchmarkCase.case_key)
            )
        )
        if not cases:
            raise invalid("该层级没有用例")
        run.status, run.started_at = "running", utcnow()
        complete = True
        for case in cases:
            existing = session.scalar(
                select(m.BenchmarkCaseResult).where(
                    m.BenchmarkCaseResult.benchmark_run_id == run.id,
                    m.BenchmarkCaseResult.case_id == case.id,
                )
            )
            if existing is not None:
                continue
            begin = perf_counter_ns()
            try:
                if self.observation_callback is not None:
                    actual = dict(self.observation_callback(session, case, run))
                    summary = f"{run.level}受管观察端口生成的实际观察"
                elif run.level == "L0":
                    actual, summary = (
                        self._static_observation(session, case, run),
                        "L0对指定版本实际执行静态门禁；无真实引擎作业",
                    )
                else:
                    actual, summary = self._fault_observation(case)
                actual = c.BenchmarkObservation.model_validate(actual).model_dump(mode="json")
            except ApplicationError as error:
                complete = False
                actual = {
                    "gate_pass": None,
                    "error_code": error.code,
                    "risk_level": None,
                    "schema_fields": [],
                    "evidence": [],
                }
                summary = f"{run.level}观察未完成：{error.code}"
            elapsed = (perf_counter_ns() - begin) // 1_000_000
            event = self.ledger.append(
                session,
                run.project_id,
                actor,
                "benchmark.case_observed",
                "benchmark_case",
                case.id,
                {"benchmark_run_id": str(run.id), "actual": actual, "simulation_level": run.level},
            )
            actual["evidence"] = list(actual["evidence"]) + [
                {"source_type": "audit", "resource_id": str(event.id), "summary": summary}
            ]
            comparison = compare_expectation(_object(case.expected, "Benchmark预期"), actual)
            result = m.BenchmarkCaseResult(
                id=uuid4(),
                project_id=run.project_id,
                benchmark_run_id=run.id,
                case_id=case.id,
                execution_run_id=None,
                passed=comparison.passed,
                actual=actual,
                latency_ms=elapsed,
                created_at=utcnow(),
                updated_at=utcnow(),
                revision=1,
            )
            session.add(result)
        run.status = "succeeded" if complete else "failed"
        run.finished_at, run.updated_at = utcnow(), utcnow()
        run.revision += 1
        session.flush()
        self.ledger.append(
            session,
            run.project_id,
            actor,
            "benchmark.completed",
            "benchmark_run",
            run.id,
            {"status": run.status, "level": run.level},
        )
        return public_record(run, c.BenchmarkRunResponse)

    def aggregate_l2(self, session: Session, actor: ActorContext, run_id: UUID) -> dict[str, Any]:
        """Observe terminal managed runs, retaining unknown outcomes as incomplete."""
        run = project_entity(session, m.BenchmarkRun, run_id, actor, "run_benchmark", lock=True)
        if run.level != "L2":
            raise invalid("此入口只汇总L2真实运行")
        cases = list(
            session.scalars(
                select(m.BenchmarkCase).where(
                    m.BenchmarkCase.dataset_id == run.dataset_id, m.BenchmarkCase.level == "L2"
                )
            )
        )
        bindings = {
            binding.case_id: binding
            for binding in session.scalars(
                select(m.BenchmarkCaseExecution).where(
                    m.BenchmarkCaseExecution.benchmark_run_id == run.id
                )
            )
        }
        complete, infrastructure_failed = True, False
        for case in cases:
            previous = session.scalar(
                select(m.BenchmarkCaseResult).where(
                    m.BenchmarkCaseResult.benchmark_run_id == run.id,
                    m.BenchmarkCaseResult.case_id == case.id,
                )
            )
            if previous is not None:
                continue
            binding = bindings.get(case.id)
            actual_run = (
                session.get(m.ExecutionRun, binding.execution_run_id)
                if binding is not None
                else None
            )
            if (
                actual_run is None
                or actual_run.project_id != run.project_id
                or actual_run.finished_at is None
            ):
                complete = False
                continue
            if actual_run.started_at is None or actual_run.engine_job_id is None:
                # Authorization expiry and unknown submission are not evidence
                # of an executed or correctly safety-blocked test case.
                infrastructure_failed = True
                complete = False
                continue
            version = session.get(m.PipelineVersion, actual_run.version_id)
            actual: dict[str, Any] = {
                "gate_pass": actual_run.status == "succeeded",
                "error_code": None,
                "risk_level": None,
                "schema_fields": [],
                "evidence": [],
            }
            if version and isinstance(version.etl_plan_json, dict):
                actual["schema_fields"] = [
                    item["target_field"] for item in version.etl_plan_json.get("field_mappings", [])
                ]
            event = self.ledger.append(
                session,
                run.project_id,
                actor,
                "benchmark.l2_observed",
                "execution_run",
                actual_run.id,
                {"benchmark_run_id": str(run.id), "status": actual_run.status},
            )
            actual["evidence"] = [
                {
                    "source_type": "audit",
                    "resource_id": str(event.id),
                    "summary": "L2真实受管作业的引擎标识与终态已核实",
                }
            ]
            comparison = compare_expectation(_object(case.expected, "Benchmark预期"), actual)
            delta = actual_run.finished_at - actual_run.started_at
            elapsed = (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000
            session.add(
                m.BenchmarkCaseResult(
                    id=uuid4(),
                    project_id=run.project_id,
                    benchmark_run_id=run.id,
                    case_id=case.id,
                    execution_run_id=actual_run.id,
                    passed=comparison.passed,
                    actual=actual,
                    latency_ms=elapsed,
                )
            )
        if complete and cases:
            run.status, run.finished_at = "succeeded", utcnow()
        elif infrastructure_failed:
            run.status, run.finished_at = "failed", utcnow()
        else:
            run.status = "running"
        run.updated_at, run.revision = utcnow(), run.revision + 1
        session.flush()
        return public_record(run, c.BenchmarkRunResponse)

    def authorize_evolution(
        self,
        session: Session,
        actor: ActorContext,
        request: c.EvolutionAuthorizationRequest,
        ctx: RequestContext,
        *,
        allowed_scopes: Sequence[Mapping[str, Any] | str] | None = None,
        baseline_metrics: Sequence[MetricResult] | None = None,
    ) -> dict[str, Any]:
        candidate = project_entity(
            session,
            m.EvolutionCandidate,
            request.candidate_id,
            actor,
            "authorize_evolution",
            lock=True,
        )
        run_ids = [UUID(str(value)) for value in candidate.benchmark_run_ids]
        runs = list(
            session.scalars(
                select(m.BenchmarkRun).where(
                    m.BenchmarkRun.id.in_(run_ids),
                    m.BenchmarkRun.project_id == candidate.project_id,
                )
            )
        )
        if len(runs) != len(set(run_ids)) or {
            run.level for run in runs if run.status == "succeeded"
        } != {"L0", "L1", "L2"}:
            raise ApplicationError("STATE_CONFLICT", "候选缺少已完成的L0/L1/L2评测证据", 409)
        version_key = "prompt_version" if candidate.kind == "prompt" else "policy_version"
        if any(
            _object(run.provider_binding, "Provider绑定").get(version_key)
            != candidate.candidate_version
            for run in runs
        ):
            raise ApplicationError("FINGERPRINT_CHANGED", "候选评测绑定的版本不一致", 409)
        actual_cases: list[dict[str, Any]] = []
        frozen_scopes: list[Mapping[str, Any] | str] = []
        for run in runs:
            actual_cases.extend(self._case_observations(session, run))
            if run.preparation_id is not None:
                preparation = session.get(m.Preparation, run.preparation_id)
                if preparation is not None and preparation.project_id == candidate.project_id:
                    frozen_scopes.extend(preparation.resource_scopes)
        if len({item["case_id"] for item in actual_cases}) != len(actual_cases):
            raise invalid("候选重复绑定了同一用例的多个结果，无法唯一复算指标")
        candidate_metrics = compute_metrics(actual_cases)
        if baseline_metrics is None:
            baseline_runs = list(
                session.scalars(
                    select(m.BenchmarkRun)
                    .where(
                        m.BenchmarkRun.project_id == candidate.project_id,
                        m.BenchmarkRun.status == "succeeded",
                    )
                    .order_by(m.BenchmarkRun.created_at.desc())
                )
            )
            baseline_cases: list[dict[str, Any]] = []
            for measured in runs:
                baseline = next(
                    (
                        other
                        for other in baseline_runs
                        if other.dataset_id == measured.dataset_id
                        and other.level == measured.level
                        and _object(other.provider_binding, "Provider绑定").get(version_key)
                        == candidate.base_version
                        and all(
                            _object(other.provider_binding, "Provider绑定").get(key) == value
                            for key, value in _object(
                                measured.provider_binding, "Provider绑定"
                            ).items()
                            if key != version_key
                        )
                    ),
                    None,
                )
                if baseline is None:
                    raise ApplicationError("STATE_CONFLICT", "缺少相同用例集与绑定的基线评测", 409)
                baseline_cases.extend(self._case_observations(session, baseline))
            baseline_metrics = compute_metrics(baseline_cases)
        try:
            decision = validate_evolution_authorization(
                request.model_dump(mode="json"),
                now=utcnow(),
                allowed_scopes=allowed_scopes if allowed_scopes is not None else frozen_scopes,
                candidate_metrics=candidate_metrics,
                baseline_metrics=baseline_metrics,
                review_report=candidate.review_report,
            )
        except ValueError as error:
            raise invalid("进化授权的范围、期限或精确比例无效") from error
        if not decision.allowed:
            raise ApplicationError(
                "STATE_CONFLICT", "进化候选未满足授权门槛：" + ",".join(decision.reasons), 409
            )
        now = utcnow()
        authorization = m.EvolutionAuthorization(
            id=uuid4(),
            project_id=candidate.project_id,
            candidate_id=candidate.id,
            mode=request.mode,
            resource_scopes=[item.model_dump(mode="json") for item in request.resource_scopes],
            traffic_ratio=Decimal(request.traffic_ratio),
            approved_by=actor.user_id,
            expires_at=request.expires_at,
            status="active",
            created_at=now,
            updated_at=now,
            revision=1,
        )
        session.add(authorization)
        session.flush()
        self.ledger.append(
            session,
            candidate.project_id,
            actor,
            "evolution.authorized",
            "evolution_authorization",
            authorization.id,
            {
                "candidate_id": str(candidate.id),
                "mode": request.mode,
                "traffic_ratio": request.traffic_ratio,
                "expires_at": request.expires_at,
            },
        )
        return public_record(authorization, c.EvolutionAuthorizationResponse)

    def revoke_evolution(
        self, session: Session, actor: ActorContext, authorization_id: UUID, expected_revision: int
    ) -> dict[str, Any]:
        authorization = project_entity(
            session,
            m.EvolutionAuthorization,
            authorization_id,
            actor,
            "revoke_evolution",
            lock=True,
        )
        compare_revision(authorization.revision, expected_revision)
        if authorization.status == "active":
            authorization.status = "revoked"
            authorization.updated_at, authorization.revision = utcnow(), authorization.revision + 1
            self.ledger.append(
                session,
                authorization.project_id,
                actor,
                "evolution.revoked",
                "evolution_authorization",
                authorization.id,
            )
            session.flush()
        return public_record(authorization, c.EvolutionAuthorizationResponse)
