"""Generation-only ports. Models return untrusted materials, never tool calls."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Protocol

from pydantic import Field, StrictStr

from etl_agent.api.schemas.contracts import (
    ContractModel,
    EtlPlan,
    ProviderBinding,
    SafeText,
)


class ModelQuestion(ContractModel):
    question_key: SafeText
    question: SafeText


class CandidateArtifacts(ContractModel):
    kind: Literal["candidate"]
    etl_plan: EtlPlan
    hocon_text: SafeText


class ClarificationOutput(ContractModel):
    kind: Literal["clarification"]
    questions: list[ModelQuestion] = Field(min_length=1)


ModelOutput = Annotated[CandidateArtifacts | ClarificationOutput, Field(discriminator="kind")]


class ModelContext(ContractModel):
    prompt: SafeText
    provider_binding: ProviderBinding
    source_profiles: list[dict[str, Any]]
    target_profile: dict[str, Any]
    answers: dict[str, StrictStr] = Field(default_factory=dict)
    validation_issues: list[dict[str, Any]] = Field(default_factory=list)
    repair_count: int = 0


class ModelProvider(Protocol):
    def resolve_binding(self, provider: str | None) -> ProviderBinding: ...
    def generate(
        self, binding: ProviderBinding, context: ModelContext
    ) -> CandidateArtifacts | ClarificationOutput: ...


class PersistentWorkflow(Protocol):
    def ensure_ready(self) -> None: ...
    def run(
        self, thread_id: str, context: ModelContext, *, answers: dict[str, str] | None = None
    ) -> dict[str, Any]: ...
