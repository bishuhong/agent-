"""Approved ETL-Agent contracts; API DTOs never expose internal persistence fields."""

from __future__ import annotations

import ipaddress
import re
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Generic, Literal, TypeVar
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    PlainSerializer,
    SecretStr,
    StrictBool,
    StrictBytes,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", validate_default=True, populate_by_name=True, allow_inf_nan=False
    )

    def model_dump(self, *, by_alias: bool | None = None, **kwargs: Any) -> dict[str, Any]:
        return super().model_dump(by_alias=True if by_alias is None else by_alias, **kwargs)

    def model_dump_json(self, *, by_alias: bool | None = None, **kwargs: Any) -> str:
        return super().model_dump_json(by_alias=True if by_alias is None else by_alias, **kwargs)


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


def _instant(value: Any) -> datetime:
    if isinstance(value, str):
        if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)", value):
            raise ValueError("must be RFC3339 with timezone")
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("must contain a timezone")
    return value.astimezone(timezone.utc)


def _instant_json(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _decimal(value: str) -> str:
    if not re.fullmatch(r"-?(?:0|[1-9]\d*)(?:\.\d+)?", value):
        raise ValueError("must be a finite plain decimal string")
    return value


def _nonnegative(value: str) -> str:
    if Decimal(value) < 0:
        raise ValueError("must be nonnegative")
    return value


def _ratio(value: str) -> str:
    if not Decimal("0") <= Decimal(value) <= Decimal("1"):
        raise ValueError("must be between zero and one")
    return value


BusinessId = UUID
EngineJobId = Annotated[StrictStr, AfterValidator(_nonblank)]
SafeText = Annotated[StrictStr, AfterValidator(_nonblank)]
NonEmptyText = SafeText
Instant = Annotated[
    datetime, BeforeValidator(_instant), PlainSerializer(_instant_json, return_type=str)
]
Count = Annotated[StrictStr, Field(pattern=r"^(0|[1-9][0-9]*)$")]
DurationMs = Count
DecimalString = Annotated[StrictStr, AfterValidator(_decimal)]
NonnegativeDecimal = Annotated[DecimalString, AfterValidator(_nonnegative)]
Ratio = Annotated[DecimalString, AfterValidator(_ratio)]
Digest = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
SmallInt = Annotated[StrictInt, Field(ge=0, le=2_147_483_647)]
PositiveInt = Annotated[StrictInt, Field(ge=1, le=2_147_483_647)]


class UserStatus(StrEnum):
    enabled = "enabled"
    disabled = "disabled"


class ProjectRole(StrEnum):
    maker = "maker"
    data_checker = "data_checker"
    security_checker = "security_checker"
    operator = "operator"
    auditor = "auditor"


class ApprovalSlot(StrEnum):
    data = "data"
    security = "security"


class RiskLevel(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class ConnectorType(StrEnum):
    mysql = "mysql"
    postgresql = "postgresql"
    oracle = "oracle"
    doris = "doris"
    clickhouse = "clickhouse"
    s3 = "s3"
    minio = "minio"
    rest_api = "rest_api"


class FileFormat(StrEnum):
    csv = "csv"
    xlsx = "xlsx"
    json = "json"
    parquet = "parquet"


class ProviderName(StrEnum):
    openai_compatible = "openai_compatible"
    deepseek = "deepseek"
    qwen = "qwen"


class LogicalType(StrEnum):
    string = "string"
    integer = "integer"
    decimal = "decimal"
    boolean = "boolean"
    date = "date"
    timestamp = "timestamp"
    binary = "binary"
    object = "object"
    array = "array"
    unknown = "unknown"


class ArtifactType(StrEnum):
    etl_plan = "etl_plan"
    hocon = "hocon"
    dag = "dag"
    quality_contract = "quality_contract"
    supervision_contract = "supervision_contract"


class PipelineStatus(StrEnum):
    draft = "draft"
    ready = "ready"


class AgentStatus(StrEnum):
    queued = "queued"
    running = "running"
    waiting_input = "waiting_input"
    validating = "validating"
    repairing = "repairing"
    succeeded = "succeeded"
    failed = "failed"


class ApprovalStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class ExecutionStatus(StrEnum):
    queued = "queued"
    authorizing = "authorizing"
    running = "running"
    cancel_requested = "cancel_requested"
    paused = "paused"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    authorization_expired = "authorization_expired"
    reconciling = "reconciling"


class PublicationStatus(StrEnum):
    not_published = "not_published"
    publishing = "publishing"
    published = "published"
    failed = "failed"
    reconciling = "reconciling"


class OperationStatus(StrEnum):
    requested = "requested"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    reconciling = "reconciling"


class CommandStatus(StrEnum):
    pending = "pending"
    claimed = "claimed"
    started = "started"
    completed = "completed"
    failed = "failed"
    expired = "expired"
    reconciling = "reconciling"


class ParseStatus(StrEnum):
    pending = "pending"
    parsing = "parsing"
    succeeded = "succeeded"
    failed = "failed"


class TestStatus(StrEnum):
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class TaskStatus(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class ToolName(StrEnum):
    execute_etl = "execute_etl"
    publish = "publish"
    cancel = "cancel"
    pause = "pause"
    resume = "resume"
    rollback = "rollback"
    cleanup = "cleanup"
    profile = "profile"
    test_connection = "test_connection"
    run_l2_benchmark = "run_l2_benchmark"


class ActionCode(StrEnum):
    view = "view"
    edit_connection = "edit_connection"
    test_connection = "test_connection"
    profile_connection = "profile_connection"
    upload_file = "upload_file"
    create_pipeline = "create_pipeline"
    generate = "generate"
    answer_clarification = "answer_clarification"
    freeze_version = "freeze_version"
    prepare = "prepare"
    approve_data = "approve_data"
    approve_security = "approve_security"
    commit = "commit"
    cancel = "cancel"
    pause = "pause"
    resume = "resume"
    rollback = "rollback"
    cleanup = "cleanup"
    rerun = "rerun"
    run_benchmark = "run_benchmark"
    authorize_evolution = "authorize_evolution"
    rotate_secret = "rotate_secret"
    manage_project_members = "manage_project_members"
    create_connection = "create_connection"
    manage_schedule = "manage_schedule"
    revoke_evolution = "revoke_evolution"


class PreparationPurpose(StrEnum):
    execute_etl = "execute_etl"
    rollback = "rollback"
    isolated_trial = "isolated_trial"
    run_l2_benchmark = "run_l2_benchmark"


class BenchmarkLevel(StrEnum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"


class FieldError(ContractModel):
    path: NonEmptyText
    code: NonEmptyText
    message: SafeText


class SafeError(ContractModel):
    code: NonEmptyText
    message: SafeText
    field_errors: list[FieldError] = Field(default_factory=list)
    retryable: StrictBool = False


T = TypeVar("T")


class DataEnvelope(ContractModel, Generic[T]):
    data: T
    request_id: NonEmptyText


class CursorPage(ContractModel, Generic[T]):
    items: list[T]
    next_cursor: StrictStr | None
    has_more: StrictBool
    request_id: NonEmptyText

    @model_validator(mode="after")
    def consistent_cursor(self) -> CursorPage[T]:
        if self.has_more != (self.next_cursor is not None) or self.next_cursor == "":
            raise ValueError("has_more must match a nonempty next_cursor")
        return self


class ErrorEnvelope(ContractModel):
    error: SafeError
    request_id: NonEmptyText


class CursorQuery(ContractModel):
    cursor: StrictStr | None = None
    limit: Annotated[StrictInt, Field(ge=1, le=100)] = 20
    query: StrictStr | None = None

    @model_validator(mode="before")
    @classmethod
    def distinguish_missing(cls, value: Any) -> Any:
        if isinstance(value, dict):
            for key in ("cursor", "query"):
                if key in value and (value[key] is None or value[key] == ""):
                    raise ValueError(f"{key} must be omitted rather than null or empty")
        return value


class ConnectionListQuery(CursorQuery):
    connector_type: ConnectorType | None = None


class ActionAvailability(ContractModel):
    action: ActionCode
    allowed: StrictBool
    reason_code: StrictStr | None
    reason_message: StrictStr | None

    @model_validator(mode="after")
    def denied_reason(self) -> ActionAvailability:
        if not self.allowed and not self.reason_code:
            raise ValueError("denied action requires reason_code")
        return self


class ResourceResponse(ContractModel):
    id: BusinessId
    project_id: BusinessId
    created_at: Instant


class MutableResourceResponse(ResourceResponse):
    updated_at: Instant
    revision: PositiveInt


class UserResponse(ContractModel):
    id: BusinessId
    created_at: Instant
    updated_at: Instant
    revision: PositiveInt
    username: NonEmptyText
    display_name: NonEmptyText
    email: StrictStr | None
    status: UserStatus


class ProjectCreateRequest(ContractModel):
    name: NonEmptyText
    code: NonEmptyText
    description: StrictStr | None = None


class ProjectResponse(ContractModel):
    id: BusinessId
    created_at: Instant
    updated_at: Instant
    revision: PositiveInt
    name: NonEmptyText
    code: NonEmptyText
    description: StrictStr | None
    available_actions: list[ActionAvailability] = Field(default_factory=list)


class ProjectMembershipResponse(MutableResourceResponse):
    user_id: BusinessId
    role: ProjectRole


class ProjectRoleGrantResponse(MutableResourceResponse):
    user_id: BusinessId
    role_slot: ApprovalSlot


class CurrentIdentityResponse(ContractModel):
    user: UserResponse
    project_memberships: list[ProjectMembershipResponse]
    role_grants: list[ProjectRoleGrantResponse]
    available_actions: list[ActionAvailability]
    expires_at: Instant
    csrf_token: NonEmptyText


class LoginResponse(ContractModel):
    user: UserResponse
    csrf_token: NonEmptyText
    expires_at: Instant


class LogoutResponse(ContractModel):
    logged_out: Literal[True]


class LocalLoginRequest(ContractModel):
    username: Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9_-]{3,64}$")]
    password: SecretStr = Field(repr=False)

    @field_validator("username")
    @classmethod
    def normalized_username(cls, value: str) -> str:
        return value.lower()

    @field_validator("password", mode="before")
    @classmethod
    def password_type(cls, value: Any) -> Any:
        if not isinstance(value, (str, SecretStr)):
            raise ValueError("password must be text")
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if not 12 <= len(raw) <= 128:
            raise ValueError("password length must be between 12 and 128")
        return value


def _safe_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("must be an absolute HTTP(S) URL")
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("URL must not contain credentials, query or fragment")
    return value


class DatabaseEndpoint(ContractModel):
    kind: Literal["database"]
    host: NonEmptyText
    port: Annotated[StrictInt, Field(ge=1, le=65535)]
    database: NonEmptyText
    schema_name: StrictStr | None = Field(
        default=None, alias="schema", serialization_alias="schema"
    )

    @field_validator("host")
    @classmethod
    def host_only(cls, value: str) -> str:
        if any(token in value for token in ("@", "/", "\\", "?", "#")):
            raise ValueError("host must not contain URL components or credentials")
        if ":" in value and not (value.startswith("[") and value.endswith("]")):
            try:
                ipaddress.ip_address(value)
            except ValueError as exc:
                raise ValueError("host must not contain a port") from exc
        return value


class ObjectStorageEndpoint(ContractModel):
    kind: Literal["object_storage"]
    base_url: Annotated[StrictStr, AfterValidator(_safe_url)]
    bucket: NonEmptyText
    prefix: StrictStr | None = None


class HttpEndpoint(ContractModel):
    kind: Literal["http"]
    base_url: Annotated[StrictStr, AfterValidator(_safe_url)]


ConnectionEndpoint = Annotated[
    DatabaseEndpoint | ObjectStorageEndpoint | HttpEndpoint, Field(discriminator="kind")
]


class ManagedCredentialInput(ContractModel):
    mode: Literal["managed_ref"]
    secret_ref_id: BusinessId


class InlineCredentialInput(ContractModel):
    mode: Literal["inline_secret"]
    secret_values: dict[NonEmptyText, SecretStr] = Field(min_length=1, repr=False)

    @field_validator("secret_values", mode="before")
    @classmethod
    def strict_values(cls, value: Any) -> Any:
        if isinstance(value, dict) and any(
            not isinstance(v, (str, SecretStr)) for v in value.values()
        ):
            raise ValueError("credential values must be text")
        return value

    def reveal_for_secret_provider(self) -> dict[str, str]:
        """Explicit sensitive boundary: only call from the SecretProvider adapter."""
        return {key: value.get_secret_value() for key, value in self.secret_values.items()}


CredentialInput = Annotated[
    ManagedCredentialInput | InlineCredentialInput, Field(discriminator="mode")
]


class ConnectionFields(ContractModel):
    name: NonEmptyText
    connector_type: ConnectorType
    endpoint: ConnectionEndpoint
    environment: NonEmptyText

    @model_validator(mode="after")
    def matching_endpoint(self) -> ConnectionFields:
        expected = (
            "object_storage"
            if self.connector_type in {ConnectorType.s3, ConnectorType.minio}
            else "http"
            if self.connector_type == ConnectorType.rest_api
            else "database"
        )
        if self.endpoint.kind != expected:
            raise ValueError("endpoint kind does not match connector_type")
        return self


class ConnectionCreateRequest(ConnectionFields):
    project_id: BusinessId
    credentials: CredentialInput | None = Field(default=None, repr=False)


class ConnectionReplaceRequest(ConnectionFields):
    credentials: CredentialInput | None = Field(default=None, repr=False)

    @model_validator(mode="before")
    @classmethod
    def missing_preserves_null_rejected(cls, value: Any) -> Any:
        if isinstance(value, dict) and "credentials" in value and value["credentials"] is None:
            raise ValueError("omit credentials to retain them; explicit null is forbidden")
        return value


class ConnectionResponse(MutableResourceResponse, ConnectionFields):
    secret_ref_id: BusinessId | None
    available_actions: list[ActionAvailability] = Field(default_factory=list)


class SecretReferenceResponse(MutableResourceResponse):
    provider: NonEmptyText
    public_alias: NonEmptyText
    secret_version: NonEmptyText


class ConnectionTestResponse(ResourceResponse):
    connection_id: BusinessId
    connection_revision: PositiveInt
    status: TestStatus
    finished_at: Instant | None
    error: SafeError | None


class ProfileCreateRequest(ContractModel):
    resource_names: list[NonEmptyText] = Field(min_length=1)
    sample_limit: Annotated[StrictInt, Field(ge=0, le=100)] = 100


class ProfileTaskAccepted(ContractModel):
    resource_id: BusinessId
    status: TaskStatus = TaskStatus.queued


class ProfileTaskResponse(ResourceResponse):
    connection_id: BusinessId
    status: TaskStatus
    started_at: Instant | None
    finished_at: Instant | None
    error: SafeError | None
    profile_ids: list[BusinessId] = Field(default_factory=list)


class SchemaField(ContractModel):
    field_name: NonEmptyText
    ordinal: SmallInt
    native_type: StrictStr | None
    logical_type: LogicalType
    nullable: StrictBool | None
    is_primary_key: StrictBool
    precision: PositiveInt | None
    scale: SmallInt | None
    sensitivity: NonEmptyText

    @model_validator(mode="after")
    def decimal_shape(self) -> SchemaField:
        if self.logical_type == LogicalType.decimal:
            if self.precision is None or self.scale is None or self.scale > self.precision:
                raise ValueError("decimal requires precision and scale <= precision")
        elif self.precision is not None or self.scale is not None:
            raise ValueError("precision and scale only apply to decimal fields")
        return self


class ApproximateStatistics(ContractModel):
    row_count: Count | None
    is_approximate: StrictBool


class MaskedSample(ContractModel):
    values: dict[str, JsonValue]
    masked_fields: list[StrictStr] = Field(default_factory=list)


class MetadataProfileResponse(ResourceResponse):
    schema_version: PositiveInt = 1
    connection_id: BusinessId | None
    file_asset_id: BusinessId | None
    resource_name: NonEmptyText
    fields: list[SchemaField]
    statistics: ApproximateStatistics
    masked_samples: list[MaskedSample] = Field(default_factory=list)
    profile_digest: Digest
    expires_at: Instant

    @model_validator(mode="after")
    def profile_consistency(self) -> MetadataProfileResponse:
        if (self.connection_id is None) == (self.file_asset_id is None):
            raise ValueError("exactly one source reference is required")
        names = [field.field_name for field in self.fields]
        ordinals = [field.ordinal for field in self.fields]
        if len(set(names)) != len(names) or len(set(ordinals)) != len(ordinals):
            raise ValueError("field names and ordinals must be unique")
        if len(self.masked_samples) > 100:
            raise ValueError("at most 100 masked samples")
        for sample in self.masked_samples:
            if not set(sample.values).issubset(names) or not set(sample.masked_fields).issubset(
                names
            ):
                raise ValueError("sample references an unknown field")
        return self


class FileAssetResponse(MutableResourceResponse):
    file_name: NonEmptyText
    file_size: Count
    file_format: FileFormat
    parse_status: ParseStatus
    fields: list[SchemaField] | None
    content_digest: Digest
    available_actions: list[ActionAvailability] = Field(default_factory=list)


class FileAssetUploadRequest(ContractModel):
    project_id: BusinessId
    file: Annotated[StrictBytes, Field(max_length=100 * 1024 * 1024)] = Field(repr=False)


class ProviderBinding(ContractModel):
    provider: ProviderName
    model: NonEmptyText
    prompt_version: NonEmptyText
    policy_version: NonEmptyText


class ResourceScope(ContractModel):
    connection_id: BusinessId | None
    file_asset_id: BusinessId | None
    resource_name: NonEmptyText
    operations: list[ToolName] = Field(min_length=1)
    environment: NonEmptyText

    @model_validator(mode="after")
    def exactly_one_reference(self) -> ResourceScope:
        if (self.connection_id is None) == (self.file_asset_id is None):
            raise ValueError("exactly one resource reference is required")
        if len(set(self.operations)) != len(self.operations):
            raise ValueError("operations must be unique")
        return self


class QualityRule(ContractModel):
    rule_id: NonEmptyText
    field_name: NonEmptyText
    kind: Literal["not_null", "positive", "mask_email"]
    stage: Literal["validate", "transform"]
    error_code: NonEmptyText

    @model_validator(mode="after")
    def stage_for_kind(self) -> QualityRule:
        expected = "transform" if self.kind == "mask_email" else "validate"
        if self.stage != expected:
            raise ValueError("rule stage does not match kind")
        return self


class QualityContract(ContractModel):
    schema_version: PositiveInt = 1
    rules: list[QualityRule] = Field(default_factory=list)
    error_resource: ResourceScope
    max_rejection_ratio: Ratio = "0"
    allow_empty_input: StrictBool = False

    @field_validator("rules")
    @classmethod
    def unique_rules(cls, value: list[QualityRule]) -> list[QualityRule]:
        if len({rule.rule_id for rule in value}) != len(value):
            raise ValueError("rule_id must be unique")
        return value


class RuntimeSupervisionContract(ContractModel):
    schema_version: PositiveInt = 1
    max_input_records: Count
    max_output_bytes: Count
    max_duration_ms: DurationMs
    max_amplification_ratio: NonnegativeDecimal
    max_rejection_ratio: Ratio
    on_exceed: Literal["warn", "kill", "isolate_alert"]


class FieldMapping(ContractModel):
    source_fields: list[NonEmptyText]
    target_field: NonEmptyText
    expression: NonEmptyText
    target_type: LogicalType


class DagNode(ContractModel):
    node_id: NonEmptyText
    kind: Literal["source", "transform", "sink", "error_sink"]
    resource_ref: StrictStr | None


class DagEdge(ContractModel):
    from_node_id: NonEmptyText
    to_node_id: NonEmptyText


class EtlPlan(ContractModel):
    schema_version: PositiveInt = 1
    source_profile_ids: list[BusinessId] = Field(min_length=1)
    target_profile_id: BusinessId
    field_mappings: list[FieldMapping]
    nodes: list[DagNode]
    edges: list[DagEdge] = Field(default_factory=list)
    quality_contract: QualityContract
    supervision_contract: RuntimeSupervisionContract


class PipelineCreateRequest(ContractModel):
    project_id: BusinessId
    name: NonEmptyText
    code: NonEmptyText
    description: StrictStr | None = None


class PipelineResponse(MutableResourceResponse):
    name: NonEmptyText
    code: NonEmptyText
    description: StrictStr | None
    status: PipelineStatus
    available_actions: list[ActionAvailability] = Field(default_factory=list)


class PipelineVersionResponse(ResourceResponse):
    pipeline_id: BusinessId
    version_number: PositiveInt
    is_immutable: StrictBool
    artifact_digest: Digest | None
    revision: PositiveInt


class PipelineCreateResponse(ContractModel):
    pipeline: PipelineResponse
    candidate_version_id: BusinessId


class GenerationRequest(ContractModel):
    prompt: SafeText
    source_profile_ids: list[BusinessId] = Field(min_length=1)
    target_profile_id: BusinessId
    provider: ProviderName | None = None

    @model_validator(mode="before")
    @classmethod
    def provider_omitted_not_null(cls, value: Any) -> Any:
        if isinstance(value, dict) and "provider" in value and value["provider"] is None:
            raise ValueError("omit provider to use the project default")
        return value


class AnswerItem(ContractModel):
    question_key: NonEmptyText
    answer: SafeText


class ClarificationAnswerRequest(ContractModel):
    answers: list[AnswerItem] = Field(min_length=1)

    @field_validator("answers")
    @classmethod
    def unique_questions(cls, value: list[AnswerItem]) -> list[AnswerItem]:
        if len({item.question_key for item in value}) != len(value):
            raise ValueError("question_key must be unique")
        return value


class ConversationResponse(ResourceResponse):
    pipeline_id: BusinessId
    creator_id: BusinessId


class ConversationMessageResponse(ResourceResponse):
    conversation_id: BusinessId
    role: Literal["user", "assistant", "tool"]
    content: SafeText
    seq_no: PositiveInt


class AgentRunResponse(MutableResourceResponse):
    version_id: BusinessId
    conversation_id: BusinessId
    thread_id: NonEmptyText
    prompt: SafeText
    provider_binding: ProviderBinding
    status: AgentStatus
    step_count: SmallInt
    repair_count: Annotated[StrictInt, Field(ge=0, le=3)]
    error_message: StrictStr | None


class ClarificationResponse(ResourceResponse):
    agent_run_id: BusinessId
    question_key: NonEmptyText
    question: SafeText
    answer: StrictStr | None
    answered_by: BusinessId | None
    answered_at: Instant | None


class ValidationIssue(ContractModel):
    code: NonEmptyText
    location: NonEmptyText
    severity: Literal["warning", "error"]
    message: SafeText
    repairable: StrictBool = False


class ValidationReportResponse(ResourceResponse):
    version_id: BusinessId
    content_digest: Digest
    rule_version: NonEmptyText
    passed: StrictBool
    issues: list[ValidationIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def errors_cannot_pass(self) -> ValidationReportResponse:
        if self.passed and any(issue.severity == "error" for issue in self.issues):
            raise ValueError("a report with errors cannot pass")
        return self


class DesignResponse(ContractModel):
    version: PipelineVersionResponse
    etl_plan: EtlPlan | None
    hocon_text: StrictStr | None
    validation_report: ValidationReportResponse | None
    available_actions: list[ActionAvailability] = Field(default_factory=list)


class RollbackPlan(ContractModel):
    schema_version: PositiveInt = 1
    target: ResourceScope
    shadow_resource: NonEmptyText
    retained_resource: StrictStr | None
    retention_ms: DurationMs = "86400000"
    expected_generation: Count | None
    steps: list[SafeText]


class FrozenInputBinding(ContractModel):
    resource_type: Literal["connection", "profile", "secret", "file"]
    resource_id: BusinessId
    resource_revision: PositiveInt | None
    content_digest: Digest | None
    secret_version: StrictStr | None


class EvaluationBinding(ContractModel):
    dataset_id: BusinessId
    dataset_digest: Digest
    provider_binding: ProviderBinding


class PrepareRequest(ContractModel):
    environment: NonEmptyText
    purpose: PreparationPurpose = PreparationPurpose.execute_etl
    execution_run_id: BusinessId | None = None
    supervision_contract: RuntimeSupervisionContract
    evaluation_binding: EvaluationBinding | None = None

    @model_validator(mode="after")
    def purpose_bindings(self) -> PrepareRequest:
        if (self.purpose == PreparationPurpose.rollback) != (self.execution_run_id is not None):
            raise ValueError("execution_run_id is required only for rollback")
        if (self.purpose == PreparationPurpose.run_l2_benchmark) != (
            self.evaluation_binding is not None
        ):
            raise ValueError("evaluation_binding is required only for run_l2_benchmark")
        return self


class PreparationResponseBody(ResourceResponse):
    schema_version: PositiveInt = 1
    version_id: BusinessId
    purpose: PreparationPurpose
    execution_run_id: BusinessId | None
    maker_id: BusinessId
    input_fingerprint: Digest
    input_bindings: list[FrozenInputBinding]
    resource_scopes: list[ResourceScope]
    impact_summary: SafeText
    data_classification: NonEmptyText
    environment: NonEmptyText
    risk_level: RiskLevel
    required_slots: list[ApprovalSlot]
    quality_contract: QualityContract
    supervision_contract: RuntimeSupervisionContract
    rollback_plan: RollbackPlan
    policy_version: NonEmptyText
    expires_at: Instant
    evaluation_binding: EvaluationBinding | None


class ApprovalDecisionRequest(ContractModel):
    decision: Literal["approve", "reject"]


class ApprovalResponse(MutableResourceResponse):
    preparation_id: BusinessId
    version_id: BusinessId
    required_slot: ApprovalSlot
    status: ApprovalStatus
    decision: Literal["approve", "reject"] | None
    approver_id: BusinessId | None
    decided_at: Instant | None


class PreparationResponse(ContractModel):
    preparation: PreparationResponseBody
    approvals: list[ApprovalResponse]
    validity: Literal["valid", "expired", "stale", "blocked"]
    available_actions: list[ActionAvailability] = Field(default_factory=list)


class CommitRequest(ContractModel):
    expected_input_fingerprint: Digest


class CommitAcceptedResponse(ContractModel):
    execution_run_id: BusinessId
    status: ExecutionStatus


class RuntimeMetrics(ContractModel):
    input_records: Count | None = None
    output_records: Count | None = None
    error_records: Count | None = None
    bytes_processed: Count | None = None
    filtered_records: Count | None = None
    output_bytes: Count | None = None
    duration_ms: DurationMs | None = None
    throughput_records_per_second: NonnegativeDecimal | None = None
    amplification_ratio: NonnegativeDecimal | None = None
    rejection_ratio: Ratio | None = None
    observed_at: Instant | None = None


class ExecutionRunResponseBody(MutableResourceResponse):
    version_id: BusinessId
    preparation_id: BusinessId
    capability_token_digest: Digest
    execution_status: ExecutionStatus
    engine_job_id: EngineJobId | None
    input_records: Count | None
    output_records: Count | None
    error_records: Count | None
    bytes_processed: Count | None
    started_at: Instant | None
    finished_at: Instant | None
    original_run_id: BusinessId | None
    metrics: RuntimeMetrics


class SupervisionResponse(ResourceResponse):
    execution_run_id: BusinessId
    metrics: RuntimeMetrics
    decision: Literal["within_budget", "exceeded", "unknown"]
    action_taken: Literal["none", "warn", "kill_requested", "isolate_alert"]
    exceeded_metrics: list[StrictStr] = Field(default_factory=list)


class RuleResult(ContractModel):
    rule_id: NonEmptyText
    checked_records: Count | None
    violating_records: Count | None


class ErrorCodeCount(ContractModel):
    error_code: NonEmptyText
    record_count: Count


class QualityReportResponse(ResourceResponse):
    execution_run_id: BusinessId
    artifact_digest: Digest
    result: Literal["passed", "failed", "unknown"]
    valid_records: Count | None
    error_records: Count | None
    rule_results: list[RuleResult] = Field(default_factory=list)
    error_code_distribution: list[ErrorCodeCount] = Field(default_factory=list)
    publish_eligible: StrictBool = False


class QualityViolationResponse(ResourceResponse):
    execution_run_id: BusinessId
    rule_id: NonEmptyText
    error_code: NonEmptyText
    field_name: NonEmptyText
    reason: SafeText


class PublicationResponse(MutableResourceResponse):
    execution_run_id: BusinessId
    command_id: BusinessId | None
    target: ResourceScope
    target_key: Digest
    expected_generation: Count
    published_generation: Count | None
    status: PublicationStatus
    retained_until: Instant | None


class RunActionRequest(ContractModel):
    expected_revision: PositiveInt


class RollbackRequest(RunActionRequest):
    publication_id: BusinessId | None
    preparation_id: BusinessId


class RunOperationResponse(MutableResourceResponse):
    execution_run_id: BusinessId
    kind: Literal["cancel", "pause", "resume", "rollback", "cleanup"]
    command_id: BusinessId
    requested_by: BusinessId
    status: OperationStatus
    publication_id: BusinessId | None
    error: SafeError | None


class ExecutionRunResponse(ContractModel):
    run: ExecutionRunResponseBody
    publication: PublicationResponse | None
    latest_rollback: RunOperationResponse | None
    quality_report: QualityReportResponse | None
    supervision: SupervisionResponse | None
    available_actions: list[ActionAvailability] = Field(default_factory=list)


class EvidenceReference(ContractModel):
    source_type: Literal["log", "profile", "validation", "supervision", "audit"]
    resource_id: NonEmptyText
    summary: SafeText


class DiagnosticFinding(ContractModel):
    certainty: Literal["confirmed", "hypothesis", "insufficient"]
    explanation: SafeText
    evidence: list[EvidenceReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def confirmed_evidence(self) -> DiagnosticFinding:
        if self.certainty == "confirmed" and not self.evidence:
            raise ValueError("confirmed finding requires evidence")
        return self


class DiagnosticReportResponse(ResourceResponse):
    execution_run_id: BusinessId
    findings: list[DiagnosticFinding] = Field(default_factory=list)
    suggestions: list[SafeText] = Field(default_factory=list)


class AuditEventResponse(ResourceResponse):
    actor_id: BusinessId | None
    actor_kind: Literal["user", "system"]
    event_type: NonEmptyText
    resource_type: NonEmptyText
    resource_id: NonEmptyText
    payload_digest: Digest
    prev_event_hash: Digest | None
    event_hash: Digest
    sequence_no: Count


class LedgerVerificationResponse(ContractModel):
    project_id: BusinessId
    checked_through_sequence: Count
    result: Literal["valid", "invalid", "not_verified"]
    checkpoint_id: BusinessId | None
    verified_at: Instant | None


class ScheduleDefinitionRequest(ContractModel):
    pipeline_id: BusinessId
    version_id: BusinessId
    kind: Literal["once", "recurring"]
    timezone: NonEmptyText
    run_at: Instant | None
    cron_expression: StrictStr | None
    enabled: StrictBool = False

    @model_validator(mode="after")
    def schedule_shape(self) -> ScheduleDefinitionRequest:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a known IANA timezone") from exc
        if self.kind == "once":
            if self.run_at is None or self.cron_expression is not None:
                raise ValueError("once requires run_at and forbids cron_expression")
        elif self.run_at is not None or not self.cron_expression:
            raise ValueError("recurring requires cron_expression and forbids run_at")
        elif len(self.cron_expression.split()) != 5:
            raise ValueError("cron_expression must have five fields")
        return self


class ScheduleDefinitionResponse(MutableResourceResponse, ScheduleDefinitionRequest):
    next_trigger_at: Instant | None


class BenchmarkInput(ContractModel):
    prompt: SafeText | None = None
    profile_ids: list[BusinessId] = Field(default_factory=list)
    version_id: BusinessId | None = None
    fault_scenario: NonEmptyText | None = None

    @model_validator(mode="after")
    def has_input(self) -> BenchmarkInput:
        if not (self.prompt or self.profile_ids or self.version_id or self.fault_scenario):
            raise ValueError("benchmark input requires an effective input")
        return self


class BenchmarkExpectation(ContractModel):
    expected_gate_pass: StrictBool | None = None
    expected_risk_level: RiskLevel | None = None
    expected_schema_fields: list[NonEmptyText] = Field(default_factory=list)
    expected_error_code: NonEmptyText | None = None

    @model_validator(mode="after")
    def has_expectation(self) -> BenchmarkExpectation:
        if (
            self.expected_gate_pass is None
            and self.expected_risk_level is None
            and not self.expected_schema_fields
            and self.expected_error_code is None
        ):
            raise ValueError("benchmark expectation must not be empty")
        return self


class BenchmarkObservation(ContractModel):
    gate_pass: StrictBool | None = None
    error_code: NonEmptyText | None = None
    risk_level: RiskLevel | None = None
    schema_fields: list[NonEmptyText] = Field(default_factory=list)
    evidence: list[EvidenceReference] = Field(default_factory=list)


class BenchmarkDatasetResponse(ResourceResponse):
    schema_version: PositiveInt = 1
    name: NonEmptyText
    version: NonEmptyText
    content_digest: Digest


class BenchmarkCaseResponse(ResourceResponse):
    schema_version: PositiveInt = 1
    dataset_id: BusinessId
    case_key: NonEmptyText
    level: BenchmarkLevel
    input: BenchmarkInput
    expected: BenchmarkExpectation


class BenchmarkRunRequest(ContractModel):
    project_id: BusinessId
    dataset_id: BusinessId
    level: BenchmarkLevel
    provider_binding: ProviderBinding
    preparation_id: BusinessId | None

    @model_validator(mode="after")
    def l2_authorization(self) -> BenchmarkRunRequest:
        if (self.level == BenchmarkLevel.L2) != (self.preparation_id is not None):
            raise ValueError("preparation_id is required only for L2")
        return self


class BenchmarkRunResponse(MutableResourceResponse):
    dataset_id: BusinessId
    level: BenchmarkLevel
    provider_binding: ProviderBinding
    status: TaskStatus
    started_at: Instant | None
    finished_at: Instant | None
    preparation_id: BusinessId | None


class CaseResultResponse(ResourceResponse):
    benchmark_run_id: BusinessId
    case_id: BusinessId
    passed: StrictBool
    actual: BenchmarkObservation
    latency_ms: DurationMs
    execution_run_id: BusinessId | None


class MetricResult(ContractModel):
    name: Literal["generation_accuracy", "schema_coverage", "safety_interception", "latency_ms"]
    numerator: Count | None
    denominator: Count | None
    value: DecimalString | None
    risk_level: RiskLevel | None


class EvolutionCandidateResponse(ResourceResponse):
    kind: Literal["prompt", "policy"]
    base_version: NonEmptyText
    candidate_version: NonEmptyText
    change_summary: SafeText
    benchmark_run_ids: list[BusinessId] = Field(default_factory=list)
    review_report: StrictStr | None


class EvolutionAuthorizationRequest(ContractModel):
    candidate_id: BusinessId
    mode: Literal["shadow", "canary"]
    resource_scopes: list[ResourceScope] = Field(min_length=1)
    traffic_ratio: Ratio
    expires_at: Instant

    @field_validator("traffic_ratio")
    @classmethod
    def positive_traffic(cls, value: str) -> str:
        if Decimal(value) <= 0:
            raise ValueError("traffic_ratio must be greater than zero")
        return value


class EvolutionAuthorizationResponse(MutableResourceResponse, EvolutionAuthorizationRequest):
    approved_by: BusinessId
    status: Literal["active", "revoked", "expired"]


class ComponentHealth(ContractModel):
    name: NonEmptyText
    status: Literal["ready", "not_ready", "unknown"]
    message: StrictStr | None


class HealthResult(ContractModel):
    status: Literal["ready", "degraded", "not_ready"]
    checked_at: Instant
    components: list[ComponentHealth]


class ResourceCount(ContractModel):
    connector_type: ConnectorType
    resource_count: Count


class ProjectOverviewResponse(ContractModel):
    project_id: BusinessId
    connection_count: Count
    pipeline_count: Count
    pending_approval_count: Count
    period_start: Instant
    period_end: Instant
    run_success_rate: Ratio | None
    resource_distribution: list[ResourceCount] = Field(default_factory=list)

    @model_validator(mode="after")
    def positive_period(self) -> ProjectOverviewResponse:
        if self.period_end <= self.period_start:
            raise ValueError("period_end must be after period_start")
        return self


class MemberGrantRequest(ContractModel):
    user_id: BusinessId
    role: ProjectRole | None = None
    role_slot: ApprovalSlot | None = None
    expected_revision: PositiveInt

    @model_validator(mode="after")
    def one_grant(self) -> MemberGrantRequest:
        if (self.role is None) == (self.role_slot is None):
            raise ValueError("provide exactly one role or role_slot")
        return self


class RotateCredentialRequest(ContractModel):
    secret_ref_id: BusinessId
    credentials: CredentialInput = Field(repr=False)
    expected_revision: PositiveInt


class FreezeVersionRequest(ContractModel):
    version_id: BusinessId
    content_digest: Digest
    validation_report_id: BusinessId
    expected_revision: PositiveInt


class TrialRequest(ContractModel):
    version_id: BusinessId
    mode: Literal["static", "isolated"]
    preparation_id: BusinessId | None

    @model_validator(mode="after")
    def isolated_authorization(self) -> TrialRequest:
        if (self.mode == "isolated") != (self.preparation_id is not None):
            raise ValueError("preparation_id is required only for isolated trial")
        return self


class RerunRequest(ContractModel):
    original_run_id: BusinessId
    preparation_id: BusinessId
    expected_revision: PositiveInt


class DiffItem(ContractModel):
    path: NonEmptyText
    change_type: Literal["added", "removed", "modified"]
    before: JsonValue
    after: JsonValue


class VersionDiffResponse(ContractModel):
    base_version_id: BusinessId
    target_version_id: BusinessId
    changes: list[DiffItem]


class LogEntryResponse(ContractModel):
    execution_run_id: BusinessId
    occurred_at: Instant
    level: Literal["info", "warning", "error"]
    message: SafeText
    sequence_no: Count


class PreparationSummary(ResourceResponse):
    version_id: BusinessId
    maker_id: BusinessId
    expires_at: Instant
    validity: Literal["valid", "expired", "stale", "blocked"]
    approval_status: Literal[
        "blocked", "pending_approval", "approved", "rejected", "expired", "stale", "submitted"
    ]


class ExecutionRunSummary(ResourceResponse):
    version_id: BusinessId
    execution_status: ExecutionStatus
    publication_status: PublicationStatus
    rollback_status: OperationStatus | None
    started_at: Instant | None
    finished_at: Instant | None


# Internal DTOs intentionally do not inherit from public response projections.
class CapabilityClaims(ContractModel):
    schema_version: PositiveInt = 1
    capability_id: BusinessId
    command_id: BusinessId
    subject_id: BusinessId
    project_id: BusinessId
    preparation_id: BusinessId
    authorization_basis: Literal["preparation", "execution_receipt"]
    parent_command_id: BusinessId | None
    tool: ToolName
    environment: NonEmptyText
    resource_scopes: list[ResourceScope]
    artifact_digest: Digest
    input_fingerprint: Digest
    issued_at: Instant
    expires_at: Instant
    key_id: NonEmptyText
    signature: SecretStr = Field(repr=False)


class CapabilityConsumption(ContractModel):
    capability_id: BusinessId
    command_id: BusinessId
    execution_run_id: BusinessId
    token_digest: Digest
    consumed_at: Instant


class ManagedCommand(MutableResourceResponse):
    schema_version: PositiveInt = 1
    execution_run_id: BusinessId
    preparation_id: BusinessId
    tool: ToolName
    capability_id: BusinessId
    authorization_ref: SecretStr = Field(repr=False)
    payload_digest: Digest
    status: CommandStatus
    claim_owner: StrictStr | None
    lease_until: Instant | None
    idempotency_key: NonEmptyText


class OutboxMessage(ContractModel):
    id: BusinessId
    created_at: Instant
    updated_at: Instant
    revision: PositiveInt
    command_id: BusinessId
    event_type: NonEmptyText
    delivery_status: Literal["pending", "sent"]
    delivery_attempts: SmallInt
    next_attempt_at: Instant


class LedgerCheckpoint(ResourceResponse):
    sequence_no: Count
    event_hash: Digest
    key_id: NonEmptyText
    signature: NonEmptyText
    anchor_ref: SecretStr = Field(repr=False)


class PipelineArtifact(ResourceResponse):
    schema_version: PositiveInt = 1
    version_id: BusinessId
    artifact_type: ArtifactType
    artifact_digest: Digest
    content: JsonValue


class ProfileTask(ProfileTaskResponse):
    requested_by: BusinessId
    resource_names: list[NonEmptyText] = Field(min_length=1)
    sample_limit: Annotated[StrictInt, Field(ge=0, le=100)]
    revision: PositiveInt


class BenchmarkCaseExecution(ContractModel):
    project_id: BusinessId
    benchmark_run_id: BusinessId
    case_id: BusinessId
    execution_run_id: BusinessId
