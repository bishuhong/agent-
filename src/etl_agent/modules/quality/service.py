"""Persist quality facts from actual engine counters; never publish a target."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from etl_agent.api.schemas import contracts as c
from etl_agent.config import Settings
from etl_agent.infrastructure.persistence import models as m
from etl_agent.infrastructure.persistence.access import project_entity, public_record
from etl_agent.modules.audit.service import EvidenceLedger
from etl_agent.modules.quality.domain import ErrorCodeCount, RuleResult, summarize_quality
from etl_agent.shared.context import ActorContext, utcnow
from etl_agent.shared.errors import ApplicationError, invalid


class QualityService:
    def __init__(self, settings: Settings, ledger: EvidenceLedger) -> None:
        self.settings = settings
        self.ledger = ledger

    def record(
        self,
        session: Session,
        run_id: UUID,
        *,
        rule_results: Sequence[RuleResult] = (),
        error_code_distribution: Sequence[ErrorCodeCount] = (),
        violations: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        """Internal worker operation; caller owns the enclosing transaction.

        Counts are observed ExecutionRun fields. No caller may submit an
        eligibility boolean or substitute current form defaults for a contract.
        """
        run = session.scalar(
            select(m.ExecutionRun).where(m.ExecutionRun.id == run_id).with_for_update()
        )
        if run is None:
            raise ApplicationError("RESOURCE_NOT_FOUND", "运行不存在", 404)
        preparation = session.get(m.Preparation, run.preparation_id)
        version = session.get(m.PipelineVersion, run.version_id)
        if (
            preparation is None
            or version is None
            or preparation.project_id != run.project_id
            or version.project_id != run.project_id
            or not version.artifact_digest
        ):
            raise ApplicationError("STATE_CONFLICT", "运行缺少同项目的冻结质量事实", 409)
        if not isinstance(preparation.quality_contract, dict):
            raise invalid("冻结质量契约必须为对象")
        try:
            summary = summarize_quality(
                run.input_records,
                run.output_records,
                run.error_records,
                preparation.quality_contract,
                rule_results=rule_results,
                error_code_distribution=error_code_distribution,
            )
        except (ValueError, TypeError) as error:
            raise invalid("引擎质量统计不一致或冻结契约无效") from error
        rules = {item["rule_id"]: item for item in preparation.quality_contract.get("rules", [])}
        safe_violations: list[dict[str, Any]] = []
        for item in violations:
            rule = rules.get(item.get("rule_id"))
            if (
                rule is None
                or rule.get("field_name") != item.get("field_name")
                or rule.get("error_code") != item.get("error_code")
            ):
                raise invalid("错误流标签与冻结质量规则不一致")
            reference = item.get("protected_record_ref")
            if not isinstance(reference, str) or not reference:
                raise invalid("错误流必须提供受保护的内部记录引用")
            safe_violations.append(
                {
                    "rule_id": rule["rule_id"],
                    "field_name": rule["field_name"],
                    "error_code": rule["error_code"],
                    "protected_record_ref": reference,
                }
            )
        if set(item.rule_id for item in rule_results) - rules.keys():
            raise invalid("规则统计包含未冻结的规则")
        if {item.error_code for item in error_code_distribution} - {
            rule["error_code"] for rule in rules.values()
        }:
            raise invalid("错误码统计包含未冻结的标签")
        report = m.QualityReport(
            id=uuid4(),
            project_id=run.project_id,
            execution_run_id=run.id,
            artifact_digest=version.artifact_digest,
            result=summary.result,
            valid_records=summary.valid_records,
            error_records=summary.error_records,
            publish_eligible=summary.publish_eligible,
            rule_results=[
                {
                    "rule_id": item.rule_id,
                    "checked_records": str(item.checked_records)
                    if item.checked_records is not None
                    else None,
                    "violating_records": str(item.violating_records)
                    if item.violating_records is not None
                    else None,
                }
                for item in rule_results
            ],
            error_code_distribution=[
                {"error_code": item.error_code, "record_count": str(item.record_count)}
                for item in error_code_distribution
            ],
            created_at=utcnow(),
            updated_at=utcnow(),
            revision=1,
        )
        session.add(report)
        for item in safe_violations:
            session.add(
                m.QualityViolation(
                    id=uuid4(),
                    project_id=run.project_id,
                    execution_run_id=run.id,
                    reason="记录未通过冻结的质量规则",
                    **item,
                )
            )
        session.flush()
        self.ledger.append(
            session,
            run.project_id,
            None,
            "quality.evaluated",
            "quality_report",
            report.id,
            {
                "result": report.result,
                "publish_eligible": report.publish_eligible,
                "execution_run_id": str(run.id),
                "artifact_digest": report.artifact_digest,
            },
        )
        return public_record(report, c.QualityReportResponse)

    def get(self, session: Session, actor: ActorContext, run_id: UUID) -> dict[str, Any] | None:
        run = project_entity(session, m.ExecutionRun, run_id, actor)
        report = session.scalar(
            select(m.QualityReport)
            .where(
                m.QualityReport.project_id == run.project_id,
                m.QualityReport.execution_run_id == run.id,
            )
            .order_by(m.QualityReport.created_at.desc(), m.QualityReport.id.desc())
            .limit(1)
        )
        return public_record(report, c.QualityReportResponse) if report is not None else None
