"""Explicit PostgreSQL records for the approved data-object and total Spec contracts.

JSONB columns are limited to the embedded value objects named by the Spec.
Business authorization stays in the application; composite foreign keys also enforce
project ownership when callers bypass an application repository.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, synonym

from .database import Base

JSON = dict[str, Any] | list[Any]


def enum_check(column: str, values: str) -> CheckConstraint:
    quoted = ",".join("'" + item + "'" for item in values.split())
    return CheckConstraint(f"{column} IN ({quoted})", name=f"{column}_values")


def project_fk(column: str, target: str, *, target_column: str = "id") -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["project_id", column],
        [f"{target}.project_id", f"{target}.{target_column}"],
        name=f"fk_{target}_{column}_project",
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
        use_alter=True,
    )


def user_fk() -> ForeignKey:
    return ForeignKey("users.id", ondelete="RESTRICT")


class Record:
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class ProjectRecord(Record):
    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT")
    )

    @declared_attr.directive
    def __table_args__(cls) -> tuple[Any, ...]:
        return (
            UniqueConstraint("project_id", "id"),
            CheckConstraint("revision > 0", name="revision_positive"),
            *cls.__dict__.get("__constraints__", ()),
        )


class User(Record, Base):
    __tablename__ = "users"
    subject_ref: Mapped[str] = mapped_column(Text, unique=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    person_ref: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    display_name: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="enabled", server_default="enabled")
    __table_args__ = (
        enum_check("status", "enabled disabled"),
        CheckConstraint("username ~ '^[a-z0-9_-]{3,64}$'", name="username_canonical"),
        CheckConstraint("revision > 0", name="revision_positive"),
    )


class Project(Record, Base):
    __tablename__ = "projects"
    name: Mapped[str] = mapped_column(Text)
    code: Mapped[str] = mapped_column(Text, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint("btrim(name) <> '' AND btrim(code) <> ''", name="names_nonempty"),
        CheckConstraint("revision > 0", name="revision_positive"),
    )


class ProjectMembership(ProjectRecord, Base):
    __tablename__ = "project_memberships"
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), user_fk())
    role: Mapped[str] = mapped_column(String(32))
    __constraints__ = (
        UniqueConstraint("project_id", "user_id", "role"),
        enum_check("role", "maker data_checker security_checker operator auditor"),
        Index("ix_memberships_user_project", "user_id", "project_id"),
    )


class ProjectRoleGrant(ProjectRecord, Base):
    __tablename__ = "project_role_grants"
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), user_fk())
    role_slot: Mapped[str] = mapped_column(String(16))
    __constraints__ = (
        UniqueConstraint("project_id", "user_id", "role_slot"),
        enum_check("role_slot", "data security"),
        Index("ix_role_grants_slot", "project_id", "role_slot", "user_id"),
    )


class LocalCredential(Base):
    __tablename__ = "local_credentials"
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), user_fk(), primary_key=True)
    password_hash: Mapped[str] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuthSession(Record, Base):
    __tablename__ = "auth_sessions"
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), user_fk())
    token_digest: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_digest: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index("ix_auth_sessions_user_expiry", "user_id", "expires_at"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        CheckConstraint("revision > 0", name="revision_positive"),
    )


class LoginThrottle(Base):
    __tablename__ = "login_throttles"
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("failures >= 0", name="failures_nonnegative"),)


class SecretReference(ProjectRecord, Base):
    __tablename__ = "secret_references"
    provider: Mapped[str] = mapped_column(Text)
    public_alias: Mapped[str] = mapped_column(Text)
    provider_locator: Mapped[str] = mapped_column(Text)
    secret_version: Mapped[str] = mapped_column(Text)
    __constraints__ = (UniqueConstraint("project_id", "public_alias", "secret_version"),)


class Connection(ProjectRecord, Base):
    __tablename__ = "connections"
    name: Mapped[str] = mapped_column(Text)
    connector_type: Mapped[str] = mapped_column(String(32))
    endpoint: Mapped[dict[str, Any]] = mapped_column(JSONB(none_as_null=True))
    secret_ref_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    environment: Mapped[str] = mapped_column(Text)
    __constraints__ = (
        UniqueConstraint("project_id", "name"),
        project_fk("secret_ref_id", "secret_references"),
        enum_check("connector_type", "mysql postgresql oracle doris clickhouse s3 minio rest_api"),
        Index("ix_connections_type", "project_id", "connector_type", "id"),
    )


class ConnectionTestResult(ProjectRecord, Base):
    __tablename__ = "connection_tests"
    connection_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    connection_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    __constraints__ = (
        project_fk("connection_id", "connections"),
        enum_check("status", "running succeeded failed"),
        CheckConstraint("connection_revision > 0", name="connection_revision_positive"),
        CheckConstraint("status <> 'failed' OR error IS NOT NULL", name="failed_has_error"),
        Index("ix_connection_tests_recent", "project_id", "connection_id", "created_at", "id"),
    )


class FileAsset(ProjectRecord, Base):
    __tablename__ = "file_assets"
    file_name: Mapped[str] = mapped_column(Text)
    file_path: Mapped[str] = mapped_column(Text)
    file_size: Mapped[int] = mapped_column(BigInteger)
    file_format: Mapped[str] = mapped_column(String(16))
    parse_status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending"
    )
    schema_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    content_digest: Mapped[str] = mapped_column(String(64))
    __constraints__ = (
        enum_check("file_format", "csv xlsx json parquet"),
        enum_check("parse_status", "pending parsing succeeded failed"),
        CheckConstraint("file_size >= 0", name="file_size_nonnegative"),
        Index("ix_file_assets_recent", "project_id", "created_at", "id"),
    )


class MetadataProfile(ProjectRecord, Base):
    __tablename__ = "metadata_profiles"
    schema_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    connection_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    file_asset_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    resource_name: Mapped[str] = mapped_column(Text)
    fields: Mapped[list[dict[str, Any]]] = mapped_column(JSONB(none_as_null=True))
    statistics: Mapped[JSON] = mapped_column(JSONB(none_as_null=True))
    masked_samples: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB(none_as_null=True), default=list, server_default=text("'[]'::jsonb")
    )
    profile_digest: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __constraints__ = (
        project_fk("connection_id", "connections"),
        project_fk("file_asset_id", "file_assets"),
        CheckConstraint("(connection_id IS NULL) <> (file_asset_id IS NULL)", name="source_xor"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        Index("ix_profiles_connection", "project_id", "connection_id", "created_at", "id"),
        Index("ix_profiles_file", "project_id", "file_asset_id", "created_at", "id"),
    )


class ProfileTask(ProjectRecord, Base):
    __tablename__ = "profile_tasks"
    connection_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    requested_by: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), user_fk())
    resource_names: Mapped[list[str]] = mapped_column(JSONB(none_as_null=True))
    sample_limit: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    status: Mapped[str] = mapped_column(String(16), default="queued", server_default="queued")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    __constraints__ = (
        project_fk("connection_id", "connections"),
        enum_check("status", "queued running succeeded failed"),
        CheckConstraint("sample_limit BETWEEN 0 AND 100", name="sample_limit_bounds"),
        CheckConstraint(
            "jsonb_typeof(resource_names) = 'array' AND jsonb_array_length(resource_names) > 0",
            name="resources_nonempty",
        ),
        CheckConstraint(
            "(status = 'failed' AND error IS NOT NULL) OR (status <> 'failed' AND error IS NULL)",
            name="error_matches_status",
        ),
        Index("ix_profile_tasks_connection", "project_id", "connection_id", "created_at", "id"),
        Index("ix_profile_tasks_pending", "status", "created_at"),
    )


class ProfileTaskResult(Base):
    __tablename__ = "profile_task_results"
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    task_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    profile_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    __table_args__ = (
        project_fk("task_id", "profile_tasks"),
        project_fk("profile_id", "metadata_profiles"),
    )


class Pipeline(ProjectRecord, Base):
    __tablename__ = "pipelines"
    name: Mapped[str] = mapped_column(Text)
    code: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default="draft")
    __constraints__ = (
        UniqueConstraint("project_id", "code"),
        enum_check("status", "draft ready"),
        Index("ix_pipelines_recent", "project_id", "created_at", "id"),
    )


class PipelineVersion(ProjectRecord, Base):
    __tablename__ = "pipeline_versions"
    pipeline_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    version_number: Mapped[int] = mapped_column(Integer)
    etl_plan_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    hocon_text: Mapped[str | None] = mapped_column(Text)
    artifact_digest: Mapped[str | None] = mapped_column(String(64))
    is_immutable: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    validation_report_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    __constraints__ = (
        project_fk("pipeline_id", "pipelines"),
        project_fk("validation_report_id", "validation_reports"),
        UniqueConstraint("pipeline_id", "version_number"),
        CheckConstraint("version_number >= 1", name="version_number_positive"),
        CheckConstraint(
            "NOT is_immutable OR (etl_plan_json IS NOT NULL AND hocon_text IS NOT NULL AND artifact_digest IS NOT NULL AND validation_report_id IS NOT NULL)",
            name="frozen_complete",
        ),
        Index("ix_versions_history", "project_id", "pipeline_id", "version_number"),
    )


class PipelineArtifact(ProjectRecord, Base):
    __tablename__ = "pipeline_artifacts"
    version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    artifact_type: Mapped[str] = mapped_column(String(32))
    artifact_digest: Mapped[str] = mapped_column(String(64))
    content: Mapped[JSON | str] = mapped_column(JSONB(none_as_null=True))
    __constraints__ = (
        project_fk("version_id", "pipeline_versions"),
        UniqueConstraint("version_id", "artifact_type"),
        enum_check("artifact_type", "etl_plan hocon dag quality_contract supervision_contract"),
    )


class Conversation(ProjectRecord, Base):
    __tablename__ = "conversations"
    pipeline_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    creator_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), user_fk())
    __constraints__ = (
        project_fk("pipeline_id", "pipelines"),
        Index("ix_conversations_pipeline", "project_id", "pipeline_id", "created_at", "id"),
    )


class ConversationMessage(ProjectRecord, Base):
    __tablename__ = "conversation_messages"
    conversation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    seq_no: Mapped[int] = mapped_column(Integer)
    __constraints__ = (
        project_fk("conversation_id", "conversations"),
        UniqueConstraint("conversation_id", "seq_no"),
        enum_check("role", "user assistant tool"),
        CheckConstraint("seq_no > 0", name="sequence_positive"),
    )


class AgentRun(ProjectRecord, Base):
    __tablename__ = "agent_runs"
    version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    conversation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    thread_id: Mapped[str] = mapped_column(Text, unique=True)
    prompt: Mapped[str] = mapped_column(Text)
    provider_binding: Mapped[dict[str, Any]] = mapped_column(JSONB(none_as_null=True))
    status: Mapped[str] = mapped_column(String(24), default="queued", server_default="queued")
    step_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    repair_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_message: Mapped[str | None] = mapped_column(Text)
    checkpoint_ref: Mapped[str | None] = mapped_column(Text)
    request_context: Mapped[dict[str, Any]] = mapped_column(
        JSONB(none_as_null=True), default=dict, server_default=text("'{}'::jsonb")
    )
    __constraints__ = (
        project_fk("version_id", "pipeline_versions"),
        project_fk("conversation_id", "conversations"),
        enum_check("status", "queued running waiting_input validating repairing succeeded failed"),
        CheckConstraint("step_count >= 0 AND repair_count BETWEEN 0 AND 3", name="counts_valid"),
        Index("ix_agent_runs_version", "project_id", "version_id", "created_at", "id"),
    )


class Clarification(ProjectRecord, Base):
    __tablename__ = "clarifications"
    agent_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    question_key: Mapped[str] = mapped_column(Text)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    answered_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), user_fk())
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __constraints__ = (
        project_fk("agent_run_id", "agent_runs"),
        UniqueConstraint("agent_run_id", "question_key"),
        CheckConstraint(
            "(answer IS NULL AND answered_by IS NULL AND answered_at IS NULL) OR (btrim(answer) <> '' AND answered_by IS NOT NULL AND answered_at IS NOT NULL)",
            name="answer_complete",
        ),
    )


class ValidationReport(ProjectRecord, Base):
    __tablename__ = "validation_reports"
    version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    content_digest: Mapped[str] = mapped_column(String(64))
    rule_version: Mapped[str] = mapped_column(Text)
    passed: Mapped[bool] = mapped_column(Boolean)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB(none_as_null=True), default=list, server_default=text("'[]'::jsonb")
    )
    __constraints__ = (
        project_fk("version_id", "pipeline_versions"),
        Index(
            "ix_validation_reports_version",
            "project_id",
            "version_id",
            "content_digest",
            "created_at",
        ),
    )


class Preparation(ProjectRecord, Base):
    __tablename__ = "preparations"
    schema_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    purpose: Mapped[str] = mapped_column(
        String(32), default="execute_etl", server_default="execute_etl"
    )
    execution_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    maker_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), user_fk())
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    input_bindings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB(none_as_null=True))
    resource_scopes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB(none_as_null=True))
    impact_summary: Mapped[str] = mapped_column(Text)
    data_classification: Mapped[str] = mapped_column(Text)
    environment: Mapped[str] = mapped_column(Text)
    risk_level: Mapped[str] = mapped_column(String(2))
    required_slots: Mapped[list[str]] = mapped_column(JSONB(none_as_null=True))
    quality_contract: Mapped[dict[str, Any]] = mapped_column(JSONB(none_as_null=True))
    supervision_contract: Mapped[dict[str, Any]] = mapped_column(JSONB(none_as_null=True))
    rollback_plan: Mapped[dict[str, Any]] = mapped_column(JSONB(none_as_null=True))
    policy_version: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    evaluation_binding: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    __constraints__ = (
        project_fk("version_id", "pipeline_versions"),
        project_fk("execution_run_id", "execution_runs"),
        enum_check("purpose", "execute_etl rollback isolated_trial run_l2_benchmark"),
        enum_check("risk_level", "P0 P1 P2 P3"),
        CheckConstraint(
            "(purpose = 'rollback') = (execution_run_id IS NOT NULL)",
            name="rollback_run_matches_purpose",
        ),
        CheckConstraint(
            "(purpose = 'run_l2_benchmark') = (evaluation_binding IS NOT NULL)",
            name="evaluation_matches_purpose",
        ),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        Index("ix_preparations_recent", "project_id", "created_at", "id"),
        Index("ix_preparations_version_expiry", "version_id", "expires_at"),
    )


class ApprovalRequest(ProjectRecord, Base):
    __tablename__ = "approval_requests"
    preparation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    required_role: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    decision: Mapped[str | None] = mapped_column(String(16))
    approver_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), user_fk())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __constraints__ = (
        project_fk("preparation_id", "preparations"),
        project_fk("version_id", "pipeline_versions"),
        UniqueConstraint("preparation_id", "required_role"),
        enum_check("required_role", "data security"),
        enum_check("status", "pending approved rejected"),
        CheckConstraint(
            "(status = 'pending' AND decision IS NULL AND approver_id IS NULL AND decided_at IS NULL) OR (status = 'approved' AND decision = 'approve' AND approver_id IS NOT NULL AND decided_at IS NOT NULL) OR (status = 'rejected' AND decision = 'reject' AND approver_id IS NOT NULL AND decided_at IS NOT NULL)",
            name="decision_matches_status",
        ),
        Index("ix_approval_requests_pending", "project_id", "status", "created_at", "id"),
    )


class ExecutionRun(ProjectRecord, Base):
    __tablename__ = "execution_runs"
    version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    preparation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    capability_token_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="queued", server_default="queued")
    engine_job_id: Mapped[str | None] = mapped_column(Text)
    input_records: Mapped[int | None] = mapped_column(BigInteger)
    output_records: Mapped[int | None] = mapped_column(BigInteger)
    error_records: Mapped[int | None] = mapped_column(BigInteger)
    bytes_processed: Mapped[int | None] = mapped_column(BigInteger)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    original_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB(none_as_null=True), default=dict, server_default=text("'{}'::jsonb")
    )
    __constraints__ = (
        project_fk("version_id", "pipeline_versions"),
        project_fk("preparation_id", "preparations"),
        project_fk("original_run_id", "execution_runs"),
        enum_check(
            "status",
            "queued authorizing running cancel_requested paused succeeded failed cancelled authorization_expired reconciling",
        ),
        CheckConstraint(
            "input_records >= 0 AND output_records >= 0 AND error_records >= 0 AND bytes_processed >= 0",
            name="counts_nonnegative",
        ),
        Index("ix_execution_runs_recent", "project_id", "created_at", "id"),
        Index("ix_execution_runs_status", "project_id", "status", "created_at", "id"),
    )


class ManagedCommand(ProjectRecord, Base):
    __tablename__ = "managed_commands"
    schema_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    execution_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    preparation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    tool: Mapped[str] = mapped_column(String(32))
    capability_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    authorization_ref: Mapped[str] = mapped_column(Text)
    payload_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    claim_owner: Mapped[str | None] = mapped_column(Text)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(Text)
    __constraints__ = (
        project_fk("execution_run_id", "execution_runs"),
        project_fk("preparation_id", "preparations"),
        project_fk("capability_id", "capability_authorizations", target_column="capability_id"),
        enum_check(
            "tool", "execute_etl publish cancel pause resume rollback cleanup run_l2_benchmark"
        ),
        enum_check("status", "pending claimed started completed failed expired reconciling"),
        Index("ix_managed_commands_pending", "project_id", "status", "created_at", "id"),
    )


class CapabilityAuthorization(Base):
    __tablename__ = "capability_authorizations"
    capability_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    id = synonym("capability_id")
    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT")
    )
    command_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    subject_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), user_fk())
    preparation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    authorization_basis: Mapped[str] = mapped_column(
        String(24), default="preparation", server_default="preparation"
    )
    parent_command_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    tool: Mapped[str] = mapped_column(String(32))
    environment: Mapped[str] = mapped_column(Text)
    resource_scopes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB(none_as_null=True))
    artifact_digest: Mapped[str] = mapped_column(String(64))
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    key_id: Mapped[str] = mapped_column(Text)
    encrypted_token: Mapped[str] = mapped_column(Text)
    token_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("project_id", "capability_id"),
        project_fk("command_id", "managed_commands"),
        project_fk("preparation_id", "preparations"),
        project_fk("parent_command_id", "managed_commands"),
        enum_check("authorization_basis", "preparation execution_receipt"),
        CheckConstraint("expires_at > issued_at", name="expiry_after_issuance"),
        CheckConstraint(
            "authorization_basis <> 'execution_receipt' OR parent_command_id IS NOT NULL",
            name="receipt_parent_required",
        ),
        Index("ix_capability_authorizations_expiry", "expires_at"),
    )


class CapabilityConsumption(Base):
    __tablename__ = "capability_consumptions"
    capability_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    id = synonym("capability_id")
    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT")
    )
    command_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    execution_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    token_digest: Mapped[str] = mapped_column(String(64))
    consumed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    __table_args__ = (
        project_fk("capability_id", "capability_authorizations", target_column="capability_id"),
        project_fk("command_id", "managed_commands"),
        project_fk("execution_run_id", "execution_runs"),
    )


class OutboxMessage(ProjectRecord, Base):
    __tablename__ = "outbox_messages"
    command_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    event_type: Mapped[str] = mapped_column(
        String(32), default="command_ready", server_default="command_ready"
    )
    delivery_status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending"
    )
    delivery_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    __constraints__ = (
        project_fk("command_id", "managed_commands"),
        UniqueConstraint("command_id", "event_type"),
        enum_check("delivery_status", "pending sent"),
        CheckConstraint("delivery_attempts >= 0", name="attempts_nonnegative"),
        Index(
            "ix_outbox_messages_pending",
            "next_attempt_at",
            "id",
            postgresql_where=text("delivery_status = 'pending'"),
        ),
    )


class RuntimeSupervisionSnapshot(ProjectRecord, Base):
    __tablename__ = "runtime_supervision_snapshots"
    execution_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSONB(none_as_null=True))
    decision: Mapped[str] = mapped_column(String(16))
    action_taken: Mapped[str] = mapped_column(String(24), default="none", server_default="none")
    exceeded_metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB(none_as_null=True), default=list, server_default=text("'[]'::jsonb")
    )
    __constraints__ = (
        project_fk("execution_run_id", "execution_runs"),
        enum_check("decision", "within_budget exceeded unknown"),
        enum_check("action_taken", "none warn kill_requested isolate_alert"),
        Index("ix_supervision_run", "project_id", "execution_run_id", "created_at", "id"),
    )


class QualityReport(ProjectRecord, Base):
    __tablename__ = "quality_reports"
    execution_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    artifact_digest: Mapped[str] = mapped_column(String(64))
    result: Mapped[str] = mapped_column(String(16), default="unknown", server_default="unknown")
    valid_records: Mapped[int | None] = mapped_column(BigInteger)
    error_records: Mapped[int | None] = mapped_column(BigInteger)
    rule_results: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB(none_as_null=True), default=list, server_default=text("'[]'::jsonb")
    )
    error_code_distribution: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB(none_as_null=True), default=list, server_default=text("'[]'::jsonb")
    )
    publish_eligible: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    __constraints__ = (
        project_fk("execution_run_id", "execution_runs"),
        enum_check("result", "passed failed unknown"),
        CheckConstraint("valid_records >= 0 AND error_records >= 0", name="counts_nonnegative"),
        CheckConstraint("NOT publish_eligible OR result = 'passed'", name="eligible_requires_pass"),
        Index("ix_quality_reports_run", "project_id", "execution_run_id", "created_at", "id"),
    )


class QualityViolation(ProjectRecord, Base):
    __tablename__ = "quality_violations"
    execution_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    rule_id: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str] = mapped_column(Text)
    field_name: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    protected_record_ref: Mapped[str] = mapped_column(Text)
    __constraints__ = (
        project_fk("execution_run_id", "execution_runs"),
        Index("ix_quality_violations_run", "project_id", "execution_run_id", "error_code", "id"),
    )


class PublicationRecord(ProjectRecord, Base):
    __tablename__ = "publication_records"
    execution_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    command_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    target: Mapped[dict[str, Any]] = mapped_column(JSONB(none_as_null=True))
    target_key: Mapped[str] = mapped_column(String(64))
    expected_generation: Mapped[int] = mapped_column(BigInteger)
    published_generation: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(
        String(16), default="not_published", server_default="not_published"
    )
    retained_resource: Mapped[str | None] = mapped_column(Text)
    retained_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __constraints__ = (
        project_fk("execution_run_id", "execution_runs"),
        project_fk("command_id", "managed_commands"),
        UniqueConstraint("project_id", "target_key", "published_generation"),
        enum_check("status", "not_published publishing published failed reconciling"),
        CheckConstraint(
            "expected_generation >= 0 AND (published_generation IS NULL OR published_generation > expected_generation)",
            name="generation_increases",
        ),
        CheckConstraint(
            "status NOT IN ('publishing','published') OR command_id IS NOT NULL",
            name="publishing_requires_command",
        ),
    )


class RunOperation(ProjectRecord, Base):
    __tablename__ = "run_operations"
    execution_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(16))
    command_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    requested_by: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), user_fk())
    status: Mapped[str] = mapped_column(String(16), default="requested", server_default="requested")
    publication_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    __constraints__ = (
        project_fk("execution_run_id", "execution_runs"),
        project_fk("command_id", "managed_commands"),
        project_fk("publication_id", "publication_records"),
        enum_check("kind", "cancel pause resume rollback cleanup"),
        enum_check("status", "requested running succeeded failed reconciling"),
        Index("ix_operations_run", "project_id", "execution_run_id", "kind", "created_at", "id"),
    )


class DiagnosticReport(ProjectRecord, Base):
    __tablename__ = "diagnostic_reports"
    execution_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    findings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB(none_as_null=True), default=list, server_default=text("'[]'::jsonb")
    )
    suggestions: Mapped[list[str]] = mapped_column(
        JSONB(none_as_null=True), default=list, server_default=text("'[]'::jsonb")
    )
    __constraints__ = (
        project_fk("execution_run_id", "execution_runs"),
        Index("ix_diagnostics_run", "project_id", "execution_run_id", "created_at", "id"),
    )


class AuditEvent(ProjectRecord, Base):
    __tablename__ = "audit_events"
    actor_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), user_fk())
    actor_kind: Mapped[str] = mapped_column(String(16))
    event_type: Mapped[str] = mapped_column(Text)
    resource_type: Mapped[str] = mapped_column(Text)
    resource_id: Mapped[str] = mapped_column(Text)
    payload_digest: Mapped[str] = mapped_column(String(64))
    prev_event_hash: Mapped[str | None] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64))
    sequence_no: Mapped[int] = mapped_column(BigInteger)
    __constraints__ = (
        UniqueConstraint("project_id", "sequence_no"),
        enum_check("actor_kind", "user system"),
        CheckConstraint(
            "(actor_kind = 'user') = (actor_id IS NOT NULL)", name="actor_matches_kind"
        ),
        CheckConstraint(
            "sequence_no > 0 AND ((sequence_no = 1) = (prev_event_hash IS NULL))",
            name="sequence_predecessor",
        ),
        Index("ix_audit_resource", "project_id", "resource_type", "resource_id", "sequence_no"),
    )


class LedgerCheckpoint(ProjectRecord, Base):
    __tablename__ = "ledger_checkpoints"
    sequence_no: Mapped[int] = mapped_column(BigInteger)
    event_hash: Mapped[str] = mapped_column(String(64))
    key_id: Mapped[str] = mapped_column(Text)
    signature: Mapped[str] = mapped_column(Text)
    anchor_ref: Mapped[str] = mapped_column(Text)
    __constraints__ = (
        UniqueConstraint("project_id", "sequence_no"),
        CheckConstraint("sequence_no > 0", name="sequence_positive"),
    )


class ScheduleDefinition(ProjectRecord, Base):
    __tablename__ = "schedule_definitions"
    pipeline_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    version_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(16))
    timezone: Mapped[str] = mapped_column(Text)
    run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cron_expression: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    next_trigger_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __constraints__ = (
        project_fk("pipeline_id", "pipelines"),
        project_fk("version_id", "pipeline_versions"),
        enum_check("kind", "once recurring"),
        CheckConstraint(
            "(kind = 'once' AND run_at IS NOT NULL AND cron_expression IS NULL) OR (kind = 'recurring' AND run_at IS NULL AND cron_expression IS NOT NULL)",
            name="schedule_input_xor",
        ),
        Index("ix_schedule_due", "next_trigger_at", "id", postgresql_where=text("enabled = true")),
    )


class BenchmarkDataset(ProjectRecord, Base):
    __tablename__ = "benchmark_datasets"
    schema_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    name: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(Text)
    content_digest: Mapped[str] = mapped_column(String(64))
    __constraints__ = (UniqueConstraint("project_id", "name", "version"),)


class BenchmarkCase(ProjectRecord, Base):
    __tablename__ = "benchmark_cases"
    schema_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    dataset_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    case_key: Mapped[str] = mapped_column(Text)
    level: Mapped[str] = mapped_column(String(2))
    input: Mapped[dict[str, Any]] = mapped_column(JSONB(none_as_null=True))
    expected: Mapped[dict[str, Any]] = mapped_column(JSONB(none_as_null=True))
    __constraints__ = (
        project_fk("dataset_id", "benchmark_datasets"),
        UniqueConstraint("dataset_id", "case_key"),
        enum_check("level", "L0 L1 L2"),
        Index("ix_cases_dataset", "dataset_id", "level", "case_key"),
    )


class BenchmarkRun(ProjectRecord, Base):
    __tablename__ = "benchmark_runs"
    dataset_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    preparation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    level: Mapped[str] = mapped_column(String(2))
    provider_binding: Mapped[dict[str, Any]] = mapped_column(JSONB(none_as_null=True))
    status: Mapped[str] = mapped_column(String(16), default="queued", server_default="queued")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __constraints__ = (
        project_fk("dataset_id", "benchmark_datasets"),
        project_fk("preparation_id", "preparations"),
        enum_check("level", "L0 L1 L2"),
        enum_check("status", "queued running succeeded failed"),
        CheckConstraint(
            "(level = 'L2') = (preparation_id IS NOT NULL)", name="l2_preparation_required"
        ),
        Index("ix_benchmark_runs_recent", "project_id", "created_at", "id"),
        Index(
            "uq_l2_preparation_submission",
            "preparation_id",
            unique=True,
            postgresql_where=text("level = 'L2'"),
        ),
    )


class BenchmarkCaseExecution(Base):
    __tablename__ = "benchmark_case_executions"
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    benchmark_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    case_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    execution_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), unique=True)
    __table_args__ = (
        project_fk("benchmark_run_id", "benchmark_runs"),
        project_fk("case_id", "benchmark_cases"),
        project_fk("execution_run_id", "execution_runs"),
    )


class BenchmarkCaseResult(ProjectRecord, Base):
    __tablename__ = "benchmark_case_results"
    benchmark_run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    case_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    execution_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    passed: Mapped[bool] = mapped_column(Boolean)
    actual: Mapped[dict[str, Any]] = mapped_column(JSONB(none_as_null=True))
    latency_ms: Mapped[int] = mapped_column(BigInteger)
    __constraints__ = (
        project_fk("benchmark_run_id", "benchmark_runs"),
        project_fk("case_id", "benchmark_cases"),
        project_fk("execution_run_id", "execution_runs"),
        UniqueConstraint("benchmark_run_id", "case_id"),
        CheckConstraint("latency_ms >= 0", name="latency_nonnegative"),
        Index("ix_case_results_run", "benchmark_run_id", "passed", "case_id"),
    )


class EvolutionCandidate(ProjectRecord, Base):
    __tablename__ = "evolution_candidates"
    kind: Mapped[str] = mapped_column(String(16))
    base_version: Mapped[str] = mapped_column(Text)
    candidate_version: Mapped[str] = mapped_column(Text)
    change_summary: Mapped[str] = mapped_column(Text)
    benchmark_run_ids: Mapped[list[str]] = mapped_column(
        JSONB(none_as_null=True), default=list, server_default=text("'[]'::jsonb")
    )
    review_report: Mapped[str | None] = mapped_column(Text)
    __constraints__ = (
        enum_check("kind", "prompt policy"),
        CheckConstraint("base_version <> candidate_version", name="versions_differ"),
    )


class EvolutionAuthorization(ProjectRecord, Base):
    __tablename__ = "evolution_authorizations"
    candidate_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    mode: Mapped[str] = mapped_column(String(16), default="shadow", server_default="shadow")
    resource_scopes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB(none_as_null=True))
    traffic_ratio: Mapped[Decimal] = mapped_column(Numeric)
    approved_by: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), user_fk())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    __constraints__ = (
        project_fk("candidate_id", "evolution_candidates"),
        enum_check("mode", "shadow canary"),
        enum_check("status", "active revoked expired"),
        CheckConstraint("traffic_ratio > 0 AND traffic_ratio <= 1", name="traffic_ratio_bounds"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        Index("ix_evolution_auth_active", "project_id", "status", "expires_at", "id"),
    )


class ApiIdempotencyRecord(ProjectRecord, Base):
    __tablename__ = "api_idempotency_records"
    subject_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), user_fk())
    action: Mapped[str] = mapped_column(Text)
    key: Mapped[str] = mapped_column(Text)
    request_digest: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __constraints__ = (
        UniqueConstraint("project_id", "subject_id", "action", "key"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
    )


class TargetHead(Base):
    __tablename__ = "target_heads"
    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="RESTRICT"), primary_key=True
    )
    target_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    generation: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    current_publication_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    __table_args__ = (
        project_fk("current_publication_id", "publication_records"),
        CheckConstraint("generation >= 0 AND revision > 0", name="generation_revision_valid"),
    )


class ScheduleTriggerRecord(Record, Base):
    __tablename__ = "schedule_trigger_records"
    schedule_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("schedule_definitions.id", ondelete="RESTRICT")
    )
    trigger_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    preparation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("preparations.id", ondelete="RESTRICT")
    )
    result: Mapped[str] = mapped_column(String(16))
    __table_args__ = (
        UniqueConstraint("schedule_id", "trigger_at"),
        enum_check("result", "created skipped"),
        CheckConstraint(
            "(result = 'created') = (preparation_id IS NOT NULL)", name="preparation_matches_result"
        ),
    )


# Scalar digest and schema/revision constraints apply consistently even to records
# with a special primary key. These are structural checks, not API authorization.
for _table in Base.metadata.tables.values():
    for _column in _table.columns:
        if _column.name.endswith(("_digest", "_hash")) and _column.name != "password_hash":
            _table.append_constraint(
                CheckConstraint(
                    f"{_column.name} ~ '^[0-9a-f]{{64}}$'", name=f"{_column.name}_sha256"
                )
            )
        if _column.name == "schema_version":
            _table.append_constraint(
                CheckConstraint("schema_version > 0", name="schema_version_positive")
            )

Base.metadata.tables["local_credentials"].append_constraint(
    CheckConstraint(
        "password_hash ~ '^scrypt[$][0-9]+[$][0-9]+[$][0-9]+[$][A-Za-z0-9_-]{22}[$][A-Za-z0-9_-]{43}$'",
        name="password_hash_scrypt",
    )
)
