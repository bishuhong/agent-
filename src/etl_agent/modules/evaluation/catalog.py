"""Versioned internal evaluation catalog, with no new public REST operations."""

from uuid import UUID, uuid4

from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from etl_agent.api.schemas import contracts as c
from etl_agent.infrastructure.persistence import models as m
from etl_agent.infrastructure.persistence.access import project_entity, public_record
from etl_agent.modules.audit.service import EvidenceLedger
from etl_agent.modules.evaluation.service import dataset_digest
from etl_agent.modules.identity_access.domain import assert_action
from etl_agent.shared.context import ActorContext
from etl_agent.shared.errors import ApplicationError, forbidden


class CaseImport(c.ContractModel):
    case_key: c.NonEmptyText
    level: c.BenchmarkLevel
    input: c.BenchmarkInput
    expected: c.BenchmarkExpectation


class DatasetImport(c.ContractModel):
    project_id: UUID
    name: c.NonEmptyText
    version: c.NonEmptyText
    cases: list[CaseImport] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_keys(self):
        if len({item.case_key for item in self.cases}) != len(self.cases):
            raise ValueError("case_key must be unique within a versioned dataset")
        return self


class EvaluationCatalog:
    def __init__(self, ledger: EvidenceLedger):
        self.ledger = ledger

    def import_dataset(self, session: Session, actor: ActorContext, payload: DatasetImport) -> dict:
        assert_action(actor, payload.project_id, "run_benchmark")
        session.scalars(
            select(m.Project).where(m.Project.id == payload.project_id).with_for_update()
        ).one()
        if session.scalar(
            select(m.BenchmarkDataset.id).where(
                m.BenchmarkDataset.project_id == payload.project_id,
                m.BenchmarkDataset.name == payload.name,
                m.BenchmarkDataset.version == payload.version,
            )
        ):
            raise ApplicationError("STATE_CONFLICT", "相同数据集版本已存在，必须使用新版本", 409)
        dataset = m.BenchmarkDataset(
            id=uuid4(),
            project_id=payload.project_id,
            name=payload.name,
            version=payload.version,
            content_digest="0" * 64,
        )
        cases = []
        for item in payload.cases:
            if item.input.version_id:
                version = project_entity(session, m.PipelineVersion, item.input.version_id, actor)
                if version.project_id != payload.project_id:
                    raise forbidden("评测版本必须属于数据集项目")
                if item.level == c.BenchmarkLevel.L2 and not version.is_immutable:
                    raise ApplicationError("STATE_CONFLICT", "L2只能引用冻结版本", 409)
            elif item.level == c.BenchmarkLevel.L2:
                raise ApplicationError("VALIDATION_FAILED", "L2用例必须引用冻结版本", 422)
            for profile_id in item.input.profile_ids:
                profile = project_entity(session, m.MetadataProfile, profile_id, actor)
                if profile.project_id != payload.project_id:
                    raise forbidden("评测Profile必须属于数据集项目")
            cases.append(
                m.BenchmarkCase(
                    id=uuid4(),
                    project_id=payload.project_id,
                    dataset_id=dataset.id,
                    case_key=item.case_key,
                    level=item.level.value,
                    input=item.input.model_dump(mode="json"),
                    expected=item.expected.model_dump(mode="json"),
                )
            )
        dataset.content_digest = dataset_digest(dataset, cases)
        session.add(dataset)
        session.flush([dataset])
        session.add_all(cases)
        session.flush()
        self.ledger.append(
            session,
            payload.project_id,
            actor,
            "benchmark_dataset_imported",
            "benchmark_dataset",
            dataset.id,
            {"content_digest": dataset.content_digest, "case_count": len(cases)},
        )
        return public_record(dataset, c.BenchmarkDatasetResponse)
