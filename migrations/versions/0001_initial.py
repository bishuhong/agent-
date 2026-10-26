"""Initial explicit PostgreSQL schema and immutable-evidence guardrails.

Revision ID: 0001_initial
Revises: None

This revision is frozen SQL, independent of subsequent ORM changes. No data is
seeded, no external service is called, and application startup does not run it.
"""

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""CREATE TABLE benchmark_case_executions (
	project_id UUID NOT NULL, 
	benchmark_run_id UUID NOT NULL, 
	case_id UUID NOT NULL, 
	execution_run_id UUID NOT NULL, 
	CONSTRAINT pk_benchmark_case_executions PRIMARY KEY (benchmark_run_id, case_id), 
	CONSTRAINT uq_benchmark_case_executions_execution_run_id UNIQUE (execution_run_id)
)""")

    op.execute(r"""CREATE TABLE login_throttles (
	key_hash VARCHAR(64) NOT NULL, 
	failures INTEGER DEFAULT '0' NOT NULL, 
	window_started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_login_throttles PRIMARY KEY (key_hash), 
	CONSTRAINT ck_login_throttles_failures_nonnegative CHECK (failures >= 0), 
	CONSTRAINT ck_login_throttles_key_hash_sha256 CHECK (key_hash ~ '^[0-9a-f]{64}$')
)""")

    op.execute(r"""CREATE TABLE profile_task_results (
	project_id UUID NOT NULL, 
	task_id UUID NOT NULL, 
	profile_id UUID NOT NULL, 
	CONSTRAINT pk_profile_task_results PRIMARY KEY (task_id, profile_id)
)""")

    op.execute(r"""CREATE TABLE projects (
	name TEXT NOT NULL, 
	code TEXT NOT NULL, 
	description TEXT, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_projects PRIMARY KEY (id), 
	CONSTRAINT ck_projects_names_nonempty CHECK (btrim(name) <> '' AND btrim(code) <> ''), 
	CONSTRAINT ck_projects_revision_positive CHECK (revision > 0), 
	CONSTRAINT uq_projects_code UNIQUE (code)
)""")

    op.execute(r"""CREATE TABLE users (
	subject_ref TEXT NOT NULL, 
	username VARCHAR(64) NOT NULL, 
	person_ref UUID NOT NULL, 
	display_name TEXT NOT NULL, 
	email TEXT, 
	status VARCHAR(16) DEFAULT 'enabled' NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_users PRIMARY KEY (id), 
	CONSTRAINT ck_users_status_values CHECK (status IN ('enabled','disabled')), 
	CONSTRAINT ck_users_username_canonical CHECK (username ~ '^[a-z0-9_-]{3,64}$'), 
	CONSTRAINT ck_users_revision_positive CHECK (revision > 0), 
	CONSTRAINT uq_users_subject_ref UNIQUE (subject_ref), 
	CONSTRAINT uq_users_username UNIQUE (username)
)""")

    op.execute(r"""CREATE TABLE agent_runs (
	version_id UUID NOT NULL, 
	conversation_id UUID NOT NULL, 
	thread_id TEXT NOT NULL, 
	prompt TEXT NOT NULL, 
	provider_binding JSONB NOT NULL, 
	status VARCHAR(24) DEFAULT 'queued' NOT NULL, 
	step_count INTEGER DEFAULT '0' NOT NULL, 
	repair_count INTEGER DEFAULT '0' NOT NULL, 
	error_message TEXT, 
	checkpoint_ref TEXT, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_agent_runs PRIMARY KEY (id), 
	CONSTRAINT ck_agent_runs_status_values CHECK (status IN ('queued','running','waiting_input','validating','repairing','succeeded','failed')), 
	CONSTRAINT ck_agent_runs_counts_valid CHECK (step_count >= 0 AND repair_count BETWEEN 0 AND 3), 
	CONSTRAINT uq_agent_runs_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_agent_runs_revision_positive CHECK (revision > 0), 
	CONSTRAINT uq_agent_runs_thread_id UNIQUE (thread_id)
)""")

    op.execute(r"""CREATE TABLE api_idempotency_records (
	subject_id UUID NOT NULL, 
	action TEXT NOT NULL, 
	key TEXT NOT NULL, 
	request_digest VARCHAR(64) NOT NULL, 
	resource_id UUID, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_api_idempotency_records PRIMARY KEY (id), 
	CONSTRAINT uq_api_idempotency_records_project_id_subject_id_action_key UNIQUE (project_id, subject_id, action, key), 
	CONSTRAINT ck_api_idempotency_records_expiry_after_creation CHECK (expires_at > created_at), 
	CONSTRAINT uq_api_idempotency_records_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_api_idempotency_records_revision_positive CHECK (revision > 0), 
	CONSTRAINT ck_api_idempotency_records_request_digest_sha256 CHECK (request_digest ~ '^[0-9a-f]{64}$')
)""")

    op.execute(r"""CREATE TABLE approval_requests (
	preparation_id UUID NOT NULL, 
	version_id UUID NOT NULL, 
	required_role VARCHAR(16) NOT NULL, 
	status VARCHAR(16) DEFAULT 'pending' NOT NULL, 
	decision VARCHAR(16), 
	approver_id UUID, 
	decided_at TIMESTAMP WITH TIME ZONE, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_approval_requests PRIMARY KEY (id), 
	CONSTRAINT uq_approval_requests_preparation_id_required_role UNIQUE (preparation_id, required_role), 
	CONSTRAINT ck_approval_requests_required_role_values CHECK (required_role IN ('data','security')), 
	CONSTRAINT ck_approval_requests_status_values CHECK (status IN ('pending','approved','rejected')), 
	CONSTRAINT ck_approval_requests_decision_matches_status CHECK ((status = 'pending' AND decision IS NULL AND approver_id IS NULL AND decided_at IS NULL) OR (status = 'approved' AND decision = 'approve' AND approver_id IS NOT NULL AND decided_at IS NOT NULL) OR (status = 'rejected' AND decision = 'reject' AND approver_id IS NOT NULL AND decided_at IS NOT NULL)), 
	CONSTRAINT uq_approval_requests_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_approval_requests_revision_positive CHECK (revision > 0)
)""")

    op.execute(r"""CREATE TABLE audit_events (
	actor_id UUID, 
	actor_kind VARCHAR(16) NOT NULL, 
	event_type TEXT NOT NULL, 
	resource_type TEXT NOT NULL, 
	resource_id TEXT NOT NULL, 
	payload_digest VARCHAR(64) NOT NULL, 
	prev_event_hash VARCHAR(64), 
	event_hash VARCHAR(64) NOT NULL, 
	sequence_no BIGINT NOT NULL, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_audit_events PRIMARY KEY (id), 
	CONSTRAINT uq_audit_events_project_id_sequence_no UNIQUE (project_id, sequence_no), 
	CONSTRAINT ck_audit_events_actor_kind_values CHECK (actor_kind IN ('user','system')), 
	CONSTRAINT ck_audit_events_actor_matches_kind CHECK ((actor_kind = 'user') = (actor_id IS NOT NULL)), 
	CONSTRAINT ck_audit_events_sequence_predecessor CHECK (sequence_no > 0 AND ((sequence_no = 1) = (prev_event_hash IS NULL))), 
	CONSTRAINT uq_audit_events_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_audit_events_revision_positive CHECK (revision > 0), 
	CONSTRAINT ck_audit_events_payload_digest_sha256 CHECK (payload_digest ~ '^[0-9a-f]{64}$'), 
	CONSTRAINT ck_audit_events_prev_event_hash_sha256 CHECK (prev_event_hash ~ '^[0-9a-f]{64}$'), 
	CONSTRAINT ck_audit_events_event_hash_sha256 CHECK (event_hash ~ '^[0-9a-f]{64}$')
)""")

    op.execute(r"""CREATE TABLE auth_sessions (
	user_id UUID NOT NULL, 
	token_digest VARCHAR(64) NOT NULL, 
	csrf_digest VARCHAR(64) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_auth_sessions PRIMARY KEY (id), 
	CONSTRAINT ck_auth_sessions_expiry_after_creation CHECK (expires_at > created_at), 
	CONSTRAINT ck_auth_sessions_revision_positive CHECK (revision > 0), 
	CONSTRAINT uq_auth_sessions_token_digest UNIQUE (token_digest), 
	CONSTRAINT ck_auth_sessions_token_digest_sha256 CHECK (token_digest ~ '^[0-9a-f]{64}$'), 
	CONSTRAINT ck_auth_sessions_csrf_digest_sha256 CHECK (csrf_digest ~ '^[0-9a-f]{64}$')
)""")

    op.execute(r"""CREATE TABLE benchmark_case_results (
	benchmark_run_id UUID NOT NULL, 
	case_id UUID NOT NULL, 
	execution_run_id UUID, 
	passed BOOLEAN NOT NULL, 
	actual JSONB NOT NULL, 
	latency_ms BIGINT NOT NULL, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_benchmark_case_results PRIMARY KEY (id), 
	CONSTRAINT uq_benchmark_case_results_benchmark_run_id_case_id UNIQUE (benchmark_run_id, case_id), 
	CONSTRAINT ck_benchmark_case_results_latency_nonnegative CHECK (latency_ms >= 0), 
	CONSTRAINT uq_benchmark_case_results_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_benchmark_case_results_revision_positive CHECK (revision > 0)
)""")

    op.execute(r"""CREATE TABLE benchmark_cases (
	schema_version INTEGER DEFAULT '1' NOT NULL, 
	dataset_id UUID NOT NULL, 
	case_key TEXT NOT NULL, 
	level VARCHAR(2) NOT NULL, 
	input JSONB NOT NULL, 
	expected JSONB NOT NULL, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_benchmark_cases PRIMARY KEY (id), 
	CONSTRAINT uq_benchmark_cases_dataset_id_case_key UNIQUE (dataset_id, case_key), 
	CONSTRAINT ck_benchmark_cases_level_values CHECK (level IN ('L0','L1','L2')), 
	CONSTRAINT uq_benchmark_cases_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_benchmark_cases_revision_positive CHECK (revision > 0), 
	CONSTRAINT ck_benchmark_cases_schema_version_positive CHECK (schema_version > 0)
)""")

    op.execute(r"""CREATE TABLE benchmark_datasets (
	schema_version INTEGER DEFAULT '1' NOT NULL, 
	name TEXT NOT NULL, 
	version TEXT NOT NULL, 
	content_digest VARCHAR(64) NOT NULL, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_benchmark_datasets PRIMARY KEY (id), 
	CONSTRAINT uq_benchmark_datasets_project_id_name_version UNIQUE (project_id, name, version), 
	CONSTRAINT uq_benchmark_datasets_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_benchmark_datasets_revision_positive CHECK (revision > 0), 
	CONSTRAINT ck_benchmark_datasets_schema_version_positive CHECK (schema_version > 0), 
	CONSTRAINT ck_benchmark_datasets_content_digest_sha256 CHECK (content_digest ~ '^[0-9a-f]{64}$')
)""")

    op.execute(r"""CREATE TABLE benchmark_runs (
	dataset_id UUID NOT NULL, 
	preparation_id UUID, 
	level VARCHAR(2) NOT NULL, 
	provider_binding JSONB NOT NULL, 
	status VARCHAR(16) DEFAULT 'queued' NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_benchmark_runs PRIMARY KEY (id), 
	CONSTRAINT ck_benchmark_runs_level_values CHECK (level IN ('L0','L1','L2')), 
	CONSTRAINT ck_benchmark_runs_status_values CHECK (status IN ('queued','running','succeeded','failed')), 
	CONSTRAINT ck_benchmark_runs_l2_preparation_required CHECK ((level = 'L2') = (preparation_id IS NOT NULL)), 
	CONSTRAINT uq_benchmark_runs_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_benchmark_runs_revision_positive CHECK (revision > 0)
)""")

    op.execute(r"""CREATE TABLE capability_authorizations (
	capability_id UUID NOT NULL, 
	project_id UUID NOT NULL, 
	command_id UUID NOT NULL, 
	subject_id UUID NOT NULL, 
	preparation_id UUID NOT NULL, 
	authorization_basis VARCHAR(24) DEFAULT 'preparation' NOT NULL, 
	parent_command_id UUID, 
	tool VARCHAR(32) NOT NULL, 
	environment TEXT NOT NULL, 
	resource_scopes JSONB NOT NULL, 
	artifact_digest VARCHAR(64) NOT NULL, 
	input_fingerprint VARCHAR(64) NOT NULL, 
	issued_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	key_id TEXT NOT NULL, 
	encrypted_token TEXT NOT NULL, 
	token_digest VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_capability_authorizations PRIMARY KEY (capability_id), 
	CONSTRAINT uq_capability_authorizations_project_id_capability_id UNIQUE (project_id, capability_id), 
	CONSTRAINT ck_capability_authorizations_authorization_basis_values CHECK (authorization_basis IN ('preparation','execution_receipt')), 
	CONSTRAINT ck_capability_authorizations_expiry_after_issuance CHECK (expires_at > issued_at), 
	CONSTRAINT ck_capability_authorizations_receipt_parent_required CHECK (authorization_basis <> 'execution_receipt' OR parent_command_id IS NOT NULL), 
	CONSTRAINT uq_capability_authorizations_command_id UNIQUE (command_id), 
	CONSTRAINT ck_capability_authorizations_artifact_digest_sha256 CHECK (artifact_digest ~ '^[0-9a-f]{64}$'), 
	CONSTRAINT ck_capability_authorizations_token_digest_sha256 CHECK (token_digest ~ '^[0-9a-f]{64}$')
)""")

    op.execute(r"""CREATE TABLE capability_consumptions (
	capability_id UUID NOT NULL, 
	project_id UUID NOT NULL, 
	command_id UUID NOT NULL, 
	execution_run_id UUID NOT NULL, 
	token_digest VARCHAR(64) NOT NULL, 
	consumed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_capability_consumptions PRIMARY KEY (capability_id), 
	CONSTRAINT uq_capability_consumptions_command_id UNIQUE (command_id), 
	CONSTRAINT ck_capability_consumptions_token_digest_sha256 CHECK (token_digest ~ '^[0-9a-f]{64}$')
)""")

    op.execute(r"""CREATE TABLE clarifications (
	agent_run_id UUID NOT NULL, 
	question_key TEXT NOT NULL, 
	question TEXT NOT NULL, 
	answer TEXT, 
	answered_by UUID, 
	answered_at TIMESTAMP WITH TIME ZONE, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_clarifications PRIMARY KEY (id), 
	CONSTRAINT uq_clarifications_agent_run_id_question_key UNIQUE (agent_run_id, question_key), 
	CONSTRAINT ck_clarifications_answer_complete CHECK ((answer IS NULL AND answered_by IS NULL AND answered_at IS NULL) OR (btrim(answer) <> '' AND answered_by IS NOT NULL AND answered_at IS NOT NULL)), 
	CONSTRAINT uq_clarifications_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_clarifications_revision_positive CHECK (revision > 0)
)""")

    op.execute(r"""CREATE TABLE connection_tests (
	connection_id UUID NOT NULL, 
	connection_revision INTEGER NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	error JSONB, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_connection_tests PRIMARY KEY (id), 
	CONSTRAINT ck_connection_tests_status_values CHECK (status IN ('running','succeeded','failed')), 
	CONSTRAINT ck_connection_tests_connection_revision_positive CHECK (connection_revision > 0), 
	CONSTRAINT ck_connection_tests_failed_has_error CHECK (status <> 'failed' OR error IS NOT NULL), 
	CONSTRAINT uq_connection_tests_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_connection_tests_revision_positive CHECK (revision > 0)
)""")

    op.execute(r"""CREATE TABLE connections (
	name TEXT NOT NULL, 
	connector_type VARCHAR(32) NOT NULL, 
	endpoint JSONB NOT NULL, 
	secret_ref_id UUID, 
	environment TEXT NOT NULL, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_connections PRIMARY KEY (id), 
	CONSTRAINT uq_connections_project_id_name UNIQUE (project_id, name), 
	CONSTRAINT ck_connections_connector_type_values CHECK (connector_type IN ('mysql','postgresql','oracle','doris','clickhouse','s3','minio','rest_api')), 
	CONSTRAINT uq_connections_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_connections_revision_positive CHECK (revision > 0)
)""")

    op.execute(r"""CREATE TABLE conversation_messages (
	conversation_id UUID NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	content TEXT NOT NULL, 
	seq_no INTEGER NOT NULL, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_conversation_messages PRIMARY KEY (id), 
	CONSTRAINT uq_conversation_messages_conversation_id_seq_no UNIQUE (conversation_id, seq_no), 
	CONSTRAINT ck_conversation_messages_role_values CHECK (role IN ('user','assistant','tool')), 
	CONSTRAINT ck_conversation_messages_sequence_positive CHECK (seq_no > 0), 
	CONSTRAINT uq_conversation_messages_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_conversation_messages_revision_positive CHECK (revision > 0)
)""")

    op.execute(r"""CREATE TABLE conversations (
	pipeline_id UUID NOT NULL, 
	creator_id UUID NOT NULL, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_conversations PRIMARY KEY (id), 
	CONSTRAINT uq_conversations_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_conversations_revision_positive CHECK (revision > 0)
)""")

    op.execute(r"""CREATE TABLE diagnostic_reports (
	execution_run_id UUID NOT NULL, 
	findings JSONB DEFAULT '[]'::jsonb NOT NULL, 
	suggestions JSONB DEFAULT '[]'::jsonb NOT NULL, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_diagnostic_reports PRIMARY KEY (id), 
	CONSTRAINT uq_diagnostic_reports_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_diagnostic_reports_revision_positive CHECK (revision > 0)
)""")

    op.execute(r"""CREATE TABLE evolution_authorizations (
	candidate_id UUID NOT NULL, 
	mode VARCHAR(16) DEFAULT 'shadow' NOT NULL, 
	resource_scopes JSONB NOT NULL, 
	traffic_ratio NUMERIC NOT NULL, 
	approved_by UUID NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	status VARCHAR(16) DEFAULT 'active' NOT NULL, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_evolution_authorizations PRIMARY KEY (id), 
	CONSTRAINT ck_evolution_authorizations_mode_values CHECK (mode IN ('shadow','canary')), 
	CONSTRAINT ck_evolution_authorizations_status_values CHECK (status IN ('active','revoked','expired')), 
	CONSTRAINT ck_evolution_authorizations_traffic_ratio_bounds CHECK (traffic_ratio > 0 AND traffic_ratio <= 1), 
	CONSTRAINT ck_evolution_authorizations_expiry_after_creation CHECK (expires_at > created_at), 
	CONSTRAINT uq_evolution_authorizations_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_evolution_authorizations_revision_positive CHECK (revision > 0)
)""")

    op.execute(r"""CREATE TABLE evolution_candidates (
	kind VARCHAR(16) NOT NULL, 
	base_version TEXT NOT NULL, 
	candidate_version TEXT NOT NULL, 
	change_summary TEXT NOT NULL, 
	benchmark_run_ids JSONB DEFAULT '[]'::jsonb NOT NULL, 
	review_report TEXT, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_evolution_candidates PRIMARY KEY (id), 
	CONSTRAINT ck_evolution_candidates_kind_values CHECK (kind IN ('prompt','policy')), 
	CONSTRAINT ck_evolution_candidates_versions_differ CHECK (base_version <> candidate_version), 
	CONSTRAINT uq_evolution_candidates_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_evolution_candidates_revision_positive CHECK (revision > 0)
)""")

    op.execute(r"""CREATE TABLE execution_runs (
	version_id UUID NOT NULL, 
	preparation_id UUID NOT NULL, 
	capability_token_digest VARCHAR(64) NOT NULL, 
	status VARCHAR(32) DEFAULT 'queued' NOT NULL, 
	engine_job_id TEXT, 
	input_records BIGINT, 
	output_records BIGINT, 
	error_records BIGINT, 
	bytes_processed BIGINT, 
	started_at TIMESTAMP WITH TIME ZONE, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	original_run_id UUID, 
	metrics JSONB DEFAULT '{}'::jsonb NOT NULL, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_execution_runs PRIMARY KEY (id), 
	CONSTRAINT ck_execution_runs_status_values CHECK (status IN ('queued','authorizing','running','cancel_requested','paused','succeeded','failed','cancelled','authorization_expired','reconciling')), 
	CONSTRAINT ck_execution_runs_counts_nonnegative CHECK (input_records >= 0 AND output_records >= 0 AND error_records >= 0 AND bytes_processed >= 0), 
	CONSTRAINT uq_execution_runs_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_execution_runs_revision_positive CHECK (revision > 0), 
	CONSTRAINT ck_execution_runs_capability_token_digest_sha256 CHECK (capability_token_digest ~ '^[0-9a-f]{64}$')
)""")

    op.execute(r"""CREATE TABLE file_assets (
	file_name TEXT NOT NULL, 
	file_path TEXT NOT NULL, 
	file_size BIGINT NOT NULL, 
	file_format VARCHAR(16) NOT NULL, 
	parse_status VARCHAR(16) DEFAULT 'pending' NOT NULL, 
	schema_json JSONB, 
	content_digest VARCHAR(64) NOT NULL, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_file_assets PRIMARY KEY (id), 
	CONSTRAINT ck_file_assets_file_format_values CHECK (file_format IN ('csv','xlsx','json','parquet')), 
	CONSTRAINT ck_file_assets_parse_status_values CHECK (parse_status IN ('pending','parsing','succeeded','failed')), 
	CONSTRAINT ck_file_assets_file_size_nonnegative CHECK (file_size >= 0), 
	CONSTRAINT uq_file_assets_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_file_assets_revision_positive CHECK (revision > 0), 
	CONSTRAINT ck_file_assets_content_digest_sha256 CHECK (content_digest ~ '^[0-9a-f]{64}$')
)""")

    op.execute(r"""CREATE TABLE ledger_checkpoints (
	sequence_no BIGINT NOT NULL, 
	event_hash VARCHAR(64) NOT NULL, 
	key_id TEXT NOT NULL, 
	signature TEXT NOT NULL, 
	anchor_ref TEXT NOT NULL, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_ledger_checkpoints PRIMARY KEY (id), 
	CONSTRAINT uq_ledger_checkpoints_project_id_sequence_no UNIQUE (project_id, sequence_no), 
	CONSTRAINT ck_ledger_checkpoints_sequence_positive CHECK (sequence_no > 0), 
	CONSTRAINT uq_ledger_checkpoints_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_ledger_checkpoints_revision_positive CHECK (revision > 0), 
	CONSTRAINT ck_ledger_checkpoints_event_hash_sha256 CHECK (event_hash ~ '^[0-9a-f]{64}$')
)""")

    op.execute(r"""CREATE TABLE local_credentials (
	user_id UUID NOT NULL, 
	password_hash TEXT NOT NULL, 
	changed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT pk_local_credentials PRIMARY KEY (user_id), 
	CONSTRAINT ck_local_credentials_password_hash_sha256 CHECK (password_hash ~ '^[0-9a-f]{64}$')
)""")

    op.execute(r"""CREATE TABLE managed_commands (
	schema_version INTEGER DEFAULT '1' NOT NULL, 
	execution_run_id UUID NOT NULL, 
	preparation_id UUID NOT NULL, 
	tool VARCHAR(32) NOT NULL, 
	capability_id UUID NOT NULL, 
	authorization_ref TEXT NOT NULL, 
	payload_digest VARCHAR(64) NOT NULL, 
	status VARCHAR(16) DEFAULT 'pending' NOT NULL, 
	claim_owner TEXT, 
	lease_until TIMESTAMP WITH TIME ZONE, 
	idempotency_key TEXT NOT NULL, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_managed_commands PRIMARY KEY (id), 
	CONSTRAINT ck_managed_commands_tool_values CHECK (tool IN ('execute_etl','publish','cancel','pause','resume','rollback','cleanup','run_l2_benchmark')), 
	CONSTRAINT ck_managed_commands_status_values CHECK (status IN ('pending','claimed','started','completed','failed','expired','reconciling')), 
	CONSTRAINT uq_managed_commands_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_managed_commands_revision_positive CHECK (revision > 0), 
	CONSTRAINT uq_managed_commands_capability_id UNIQUE (capability_id), 
	CONSTRAINT ck_managed_commands_schema_version_positive CHECK (schema_version > 0), 
	CONSTRAINT ck_managed_commands_payload_digest_sha256 CHECK (payload_digest ~ '^[0-9a-f]{64}$')
)""")

    op.execute(r"""CREATE TABLE metadata_profiles (
	schema_version INTEGER DEFAULT '1' NOT NULL, 
	connection_id UUID, 
	file_asset_id UUID, 
	resource_name TEXT NOT NULL, 
	fields JSONB NOT NULL, 
	statistics JSONB NOT NULL, 
	masked_samples JSONB DEFAULT '[]'::jsonb NOT NULL, 
	profile_digest VARCHAR(64) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_metadata_profiles PRIMARY KEY (id), 
	CONSTRAINT ck_metadata_profiles_source_xor CHECK ((connection_id IS NULL) <> (file_asset_id IS NULL)), 
	CONSTRAINT ck_metadata_profiles_expiry_after_creation CHECK (expires_at > created_at), 
	CONSTRAINT uq_metadata_profiles_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_metadata_profiles_revision_positive CHECK (revision > 0), 
	CONSTRAINT ck_metadata_profiles_schema_version_positive CHECK (schema_version > 0), 
	CONSTRAINT ck_metadata_profiles_profile_digest_sha256 CHECK (profile_digest ~ '^[0-9a-f]{64}$')
)""")

    op.execute(r"""CREATE TABLE outbox_messages (
	command_id UUID NOT NULL, 
	event_type VARCHAR(32) DEFAULT 'command_ready' NOT NULL, 
	delivery_status VARCHAR(16) DEFAULT 'pending' NOT NULL, 
	delivery_attempts INTEGER DEFAULT '0' NOT NULL, 
	next_attempt_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_outbox_messages PRIMARY KEY (id), 
	CONSTRAINT uq_outbox_messages_command_id_event_type UNIQUE (command_id, event_type), 
	CONSTRAINT ck_outbox_messages_delivery_status_values CHECK (delivery_status IN ('pending','sent')), 
	CONSTRAINT ck_outbox_messages_attempts_nonnegative CHECK (delivery_attempts >= 0), 
	CONSTRAINT uq_outbox_messages_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_outbox_messages_revision_positive CHECK (revision > 0)
)""")

    op.execute(r"""CREATE TABLE pipeline_artifacts (
	version_id UUID NOT NULL, 
	artifact_type VARCHAR(32) NOT NULL, 
	artifact_digest VARCHAR(64) NOT NULL, 
	content JSONB NOT NULL, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_pipeline_artifacts PRIMARY KEY (id), 
	CONSTRAINT uq_pipeline_artifacts_version_id_artifact_type UNIQUE (version_id, artifact_type), 
	CONSTRAINT ck_pipeline_artifacts_artifact_type_values CHECK (artifact_type IN ('etl_plan','hocon','dag','quality_contract','supervision_contract')), 
	CONSTRAINT uq_pipeline_artifacts_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_pipeline_artifacts_revision_positive CHECK (revision > 0), 
	CONSTRAINT ck_pipeline_artifacts_artifact_digest_sha256 CHECK (artifact_digest ~ '^[0-9a-f]{64}$')
)""")

    op.execute(r"""CREATE TABLE pipeline_versions (
	pipeline_id UUID NOT NULL, 
	version_number INTEGER NOT NULL, 
	etl_plan_json JSONB, 
	hocon_text TEXT, 
	artifact_digest VARCHAR(64), 
	is_immutable BOOLEAN DEFAULT false NOT NULL, 
	validation_report_id UUID, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_pipeline_versions PRIMARY KEY (id), 
	CONSTRAINT uq_pipeline_versions_pipeline_id_version_number UNIQUE (pipeline_id, version_number), 
	CONSTRAINT ck_pipeline_versions_version_number_positive CHECK (version_number >= 1), 
	CONSTRAINT ck_pipeline_versions_frozen_complete CHECK (NOT is_immutable OR (etl_plan_json IS NOT NULL AND hocon_text IS NOT NULL AND artifact_digest IS NOT NULL AND validation_report_id IS NOT NULL)), 
	CONSTRAINT uq_pipeline_versions_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_pipeline_versions_revision_positive CHECK (revision > 0), 
	CONSTRAINT ck_pipeline_versions_artifact_digest_sha256 CHECK (artifact_digest ~ '^[0-9a-f]{64}$')
)""")

    op.execute(r"""CREATE TABLE pipelines (
	name TEXT NOT NULL, 
	code TEXT NOT NULL, 
	description TEXT, 
	status VARCHAR(16) DEFAULT 'draft' NOT NULL, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_pipelines PRIMARY KEY (id), 
	CONSTRAINT uq_pipelines_project_id_code UNIQUE (project_id, code), 
	CONSTRAINT ck_pipelines_status_values CHECK (status IN ('draft','ready')), 
	CONSTRAINT uq_pipelines_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_pipelines_revision_positive CHECK (revision > 0)
)""")

    op.execute(r"""CREATE TABLE preparations (
	schema_version INTEGER DEFAULT '1' NOT NULL, 
	version_id UUID NOT NULL, 
	purpose VARCHAR(32) DEFAULT 'execute_etl' NOT NULL, 
	execution_run_id UUID, 
	maker_id UUID NOT NULL, 
	input_fingerprint VARCHAR(64) NOT NULL, 
	input_bindings JSONB NOT NULL, 
	resource_scopes JSONB NOT NULL, 
	impact_summary TEXT NOT NULL, 
	data_classification TEXT NOT NULL, 
	environment TEXT NOT NULL, 
	risk_level VARCHAR(2) NOT NULL, 
	required_slots JSONB NOT NULL, 
	quality_contract JSONB NOT NULL, 
	supervision_contract JSONB NOT NULL, 
	rollback_plan JSONB NOT NULL, 
	policy_version TEXT NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	evaluation_binding JSONB, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_preparations PRIMARY KEY (id), 
	CONSTRAINT ck_preparations_purpose_values CHECK (purpose IN ('execute_etl','rollback','isolated_trial','run_l2_benchmark')), 
	CONSTRAINT ck_preparations_risk_level_values CHECK (risk_level IN ('P0','P1','P2','P3')), 
	CONSTRAINT ck_preparations_rollback_run_matches_purpose CHECK ((purpose = 'rollback') = (execution_run_id IS NOT NULL)), 
	CONSTRAINT ck_preparations_evaluation_matches_purpose CHECK ((purpose = 'run_l2_benchmark') = (evaluation_binding IS NOT NULL)), 
	CONSTRAINT ck_preparations_expiry_after_creation CHECK (expires_at > created_at), 
	CONSTRAINT uq_preparations_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_preparations_revision_positive CHECK (revision > 0), 
	CONSTRAINT ck_preparations_schema_version_positive CHECK (schema_version > 0)
)""")

    op.execute(r"""CREATE TABLE profile_tasks (
	connection_id UUID NOT NULL, 
	requested_by UUID NOT NULL, 
	resource_names JSONB NOT NULL, 
	sample_limit INTEGER DEFAULT '100' NOT NULL, 
	status VARCHAR(16) DEFAULT 'queued' NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	error JSONB, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_profile_tasks PRIMARY KEY (id), 
	CONSTRAINT ck_profile_tasks_status_values CHECK (status IN ('queued','running','succeeded','failed')), 
	CONSTRAINT ck_profile_tasks_sample_limit_bounds CHECK (sample_limit BETWEEN 0 AND 100), 
	CONSTRAINT ck_profile_tasks_resources_nonempty CHECK (jsonb_typeof(resource_names) = 'array' AND jsonb_array_length(resource_names) > 0), 
	CONSTRAINT ck_profile_tasks_error_matches_status CHECK ((status = 'failed' AND error IS NOT NULL) OR (status <> 'failed' AND error IS NULL)), 
	CONSTRAINT uq_profile_tasks_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_profile_tasks_revision_positive CHECK (revision > 0)
)""")

    op.execute(r"""CREATE TABLE project_memberships (
	user_id UUID NOT NULL, 
	role VARCHAR(32) NOT NULL, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_project_memberships PRIMARY KEY (id), 
	CONSTRAINT uq_project_memberships_project_id_user_id_role UNIQUE (project_id, user_id, role), 
	CONSTRAINT ck_project_memberships_role_values CHECK (role IN ('maker','data_checker','security_checker','operator','auditor')), 
	CONSTRAINT uq_project_memberships_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_project_memberships_revision_positive CHECK (revision > 0)
)""")

    op.execute(r"""CREATE TABLE project_role_grants (
	user_id UUID NOT NULL, 
	role_slot VARCHAR(16) NOT NULL, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_project_role_grants PRIMARY KEY (id), 
	CONSTRAINT uq_project_role_grants_project_id_user_id_role_slot UNIQUE (project_id, user_id, role_slot), 
	CONSTRAINT ck_project_role_grants_role_slot_values CHECK (role_slot IN ('data','security')), 
	CONSTRAINT uq_project_role_grants_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_project_role_grants_revision_positive CHECK (revision > 0)
)""")

    op.execute(r"""CREATE TABLE publication_records (
	execution_run_id UUID NOT NULL, 
	command_id UUID, 
	target JSONB NOT NULL, 
	target_key VARCHAR(64) NOT NULL, 
	expected_generation BIGINT NOT NULL, 
	published_generation BIGINT, 
	status VARCHAR(16) DEFAULT 'not_published' NOT NULL, 
	retained_resource TEXT, 
	retained_until TIMESTAMP WITH TIME ZONE, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_publication_records PRIMARY KEY (id), 
	CONSTRAINT uq_publication_records_project_id_target_key_published__943f UNIQUE (project_id, target_key, published_generation), 
	CONSTRAINT ck_publication_records_status_values CHECK (status IN ('not_published','publishing','published','failed','reconciling')), 
	CONSTRAINT ck_publication_records_generation_increases CHECK (expected_generation >= 0 AND (published_generation IS NULL OR published_generation > expected_generation)), 
	CONSTRAINT ck_publication_records_publishing_requires_command CHECK (status NOT IN ('publishing','published') OR command_id IS NOT NULL), 
	CONSTRAINT uq_publication_records_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_publication_records_revision_positive CHECK (revision > 0), 
	CONSTRAINT uq_publication_records_execution_run_id UNIQUE (execution_run_id)
)""")

    op.execute(r"""CREATE TABLE quality_reports (
	execution_run_id UUID NOT NULL, 
	artifact_digest VARCHAR(64) NOT NULL, 
	result VARCHAR(16) DEFAULT 'unknown' NOT NULL, 
	valid_records BIGINT, 
	error_records BIGINT, 
	rule_results JSONB DEFAULT '[]'::jsonb NOT NULL, 
	error_code_distribution JSONB DEFAULT '[]'::jsonb NOT NULL, 
	publish_eligible BOOLEAN DEFAULT false NOT NULL, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_quality_reports PRIMARY KEY (id), 
	CONSTRAINT ck_quality_reports_result_values CHECK (result IN ('passed','failed','unknown')), 
	CONSTRAINT ck_quality_reports_counts_nonnegative CHECK (valid_records >= 0 AND error_records >= 0), 
	CONSTRAINT ck_quality_reports_eligible_requires_pass CHECK (NOT publish_eligible OR result = 'passed'), 
	CONSTRAINT uq_quality_reports_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_quality_reports_revision_positive CHECK (revision > 0), 
	CONSTRAINT ck_quality_reports_artifact_digest_sha256 CHECK (artifact_digest ~ '^[0-9a-f]{64}$')
)""")

    op.execute(r"""CREATE TABLE quality_violations (
	execution_run_id UUID NOT NULL, 
	rule_id TEXT NOT NULL, 
	error_code TEXT NOT NULL, 
	field_name TEXT NOT NULL, 
	reason TEXT NOT NULL, 
	protected_record_ref TEXT NOT NULL, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_quality_violations PRIMARY KEY (id), 
	CONSTRAINT uq_quality_violations_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_quality_violations_revision_positive CHECK (revision > 0)
)""")

    op.execute(r"""CREATE TABLE run_operations (
	execution_run_id UUID NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	command_id UUID NOT NULL, 
	requested_by UUID NOT NULL, 
	status VARCHAR(16) DEFAULT 'requested' NOT NULL, 
	publication_id UUID, 
	error JSONB, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_run_operations PRIMARY KEY (id), 
	CONSTRAINT ck_run_operations_kind_values CHECK (kind IN ('cancel','pause','resume','rollback','cleanup')), 
	CONSTRAINT ck_run_operations_status_values CHECK (status IN ('requested','running','succeeded','failed','reconciling')), 
	CONSTRAINT uq_run_operations_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_run_operations_revision_positive CHECK (revision > 0), 
	CONSTRAINT uq_run_operations_command_id UNIQUE (command_id)
)""")

    op.execute(r"""CREATE TABLE runtime_supervision_snapshots (
	execution_run_id UUID NOT NULL, 
	metrics_json JSONB NOT NULL, 
	decision VARCHAR(16) NOT NULL, 
	action_taken VARCHAR(24) DEFAULT 'none' NOT NULL, 
	exceeded_metrics JSONB DEFAULT '[]'::jsonb NOT NULL, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_runtime_supervision_snapshots PRIMARY KEY (id), 
	CONSTRAINT ck_runtime_supervision_snapshots_decision_values CHECK (decision IN ('within_budget','exceeded','unknown')), 
	CONSTRAINT ck_runtime_supervision_snapshots_action_taken_values CHECK (action_taken IN ('none','warn','kill_requested','isolate_alert')), 
	CONSTRAINT uq_runtime_supervision_snapshots_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_runtime_supervision_snapshots_revision_positive CHECK (revision > 0)
)""")

    op.execute(r"""CREATE TABLE schedule_definitions (
	pipeline_id UUID NOT NULL, 
	version_id UUID NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	timezone TEXT NOT NULL, 
	run_at TIMESTAMP WITH TIME ZONE, 
	cron_expression TEXT, 
	enabled BOOLEAN DEFAULT false NOT NULL, 
	next_trigger_at TIMESTAMP WITH TIME ZONE, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_schedule_definitions PRIMARY KEY (id), 
	CONSTRAINT ck_schedule_definitions_kind_values CHECK (kind IN ('once','recurring')), 
	CONSTRAINT ck_schedule_definitions_schedule_input_xor CHECK ((kind = 'once' AND run_at IS NOT NULL AND cron_expression IS NULL) OR (kind = 'recurring' AND run_at IS NULL AND cron_expression IS NOT NULL)), 
	CONSTRAINT uq_schedule_definitions_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_schedule_definitions_revision_positive CHECK (revision > 0)
)""")

    op.execute(r"""CREATE TABLE secret_references (
	provider TEXT NOT NULL, 
	public_alias TEXT NOT NULL, 
	provider_locator TEXT NOT NULL, 
	secret_version TEXT NOT NULL, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_secret_references PRIMARY KEY (id), 
	CONSTRAINT uq_secret_references_project_id_public_alias_secret_version UNIQUE (project_id, public_alias, secret_version), 
	CONSTRAINT uq_secret_references_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_secret_references_revision_positive CHECK (revision > 0)
)""")

    op.execute(r"""CREATE TABLE target_heads (
	project_id UUID NOT NULL, 
	target_key VARCHAR(64) NOT NULL, 
	generation BIGINT DEFAULT '0' NOT NULL, 
	current_publication_id UUID, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_target_heads PRIMARY KEY (project_id, target_key), 
	CONSTRAINT ck_target_heads_generation_revision_valid CHECK (generation >= 0 AND revision > 0)
)""")

    op.execute(r"""CREATE TABLE validation_reports (
	version_id UUID NOT NULL, 
	content_digest VARCHAR(64) NOT NULL, 
	rule_version TEXT NOT NULL, 
	passed BOOLEAN NOT NULL, 
	issues JSONB DEFAULT '[]'::jsonb NOT NULL, 
	project_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_validation_reports PRIMARY KEY (id), 
	CONSTRAINT uq_validation_reports_project_id_id UNIQUE (project_id, id), 
	CONSTRAINT ck_validation_reports_revision_positive CHECK (revision > 0), 
	CONSTRAINT ck_validation_reports_content_digest_sha256 CHECK (content_digest ~ '^[0-9a-f]{64}$')
)""")

    op.execute(r"""CREATE TABLE schedule_trigger_records (
	schedule_id UUID NOT NULL, 
	trigger_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	preparation_id UUID, 
	result VARCHAR(16) NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revision INTEGER DEFAULT '1' NOT NULL, 
	CONSTRAINT pk_schedule_trigger_records PRIMARY KEY (id), 
	CONSTRAINT uq_schedule_trigger_records_schedule_id_trigger_at UNIQUE (schedule_id, trigger_at), 
	CONSTRAINT ck_schedule_trigger_records_result_values CHECK (result IN ('created','skipped')), 
	CONSTRAINT ck_schedule_trigger_records_preparation_matches_result CHECK ((result = 'created') = (preparation_id IS NOT NULL))
)""")

    op.execute(
        r"""ALTER TABLE agent_runs ADD CONSTRAINT fk_agent_runs_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE agent_runs ADD CONSTRAINT fk_conversations_conversation_id_project FOREIGN KEY(project_id, conversation_id) REFERENCES conversations (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE agent_runs ADD CONSTRAINT fk_pipeline_versions_version_id_project FOREIGN KEY(project_id, version_id) REFERENCES pipeline_versions (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE api_idempotency_records ADD CONSTRAINT fk_api_idempotency_records_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE api_idempotency_records ADD CONSTRAINT fk_api_idempotency_records_subject_id_users FOREIGN KEY(subject_id) REFERENCES users (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE approval_requests ADD CONSTRAINT fk_approval_requests_approver_id_users FOREIGN KEY(approver_id) REFERENCES users (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE approval_requests ADD CONSTRAINT fk_approval_requests_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE approval_requests ADD CONSTRAINT fk_pipeline_versions_version_id_project FOREIGN KEY(project_id, version_id) REFERENCES pipeline_versions (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE approval_requests ADD CONSTRAINT fk_preparations_preparation_id_project FOREIGN KEY(project_id, preparation_id) REFERENCES preparations (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE audit_events ADD CONSTRAINT fk_audit_events_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE audit_events ADD CONSTRAINT fk_audit_events_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE auth_sessions ADD CONSTRAINT fk_auth_sessions_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE benchmark_case_executions ADD CONSTRAINT fk_benchmark_cases_case_id_project FOREIGN KEY(project_id, case_id) REFERENCES benchmark_cases (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE benchmark_case_executions ADD CONSTRAINT fk_benchmark_runs_benchmark_run_id_project FOREIGN KEY(project_id, benchmark_run_id) REFERENCES benchmark_runs (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE benchmark_case_executions ADD CONSTRAINT fk_execution_runs_execution_run_id_project FOREIGN KEY(project_id, execution_run_id) REFERENCES execution_runs (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE benchmark_case_results ADD CONSTRAINT fk_benchmark_case_results_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE benchmark_case_results ADD CONSTRAINT fk_benchmark_cases_case_id_project FOREIGN KEY(project_id, case_id) REFERENCES benchmark_cases (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE benchmark_case_results ADD CONSTRAINT fk_benchmark_runs_benchmark_run_id_project FOREIGN KEY(project_id, benchmark_run_id) REFERENCES benchmark_runs (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE benchmark_case_results ADD CONSTRAINT fk_execution_runs_execution_run_id_project FOREIGN KEY(project_id, execution_run_id) REFERENCES execution_runs (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE benchmark_cases ADD CONSTRAINT fk_benchmark_cases_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE benchmark_cases ADD CONSTRAINT fk_benchmark_datasets_dataset_id_project FOREIGN KEY(project_id, dataset_id) REFERENCES benchmark_datasets (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE benchmark_datasets ADD CONSTRAINT fk_benchmark_datasets_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE benchmark_runs ADD CONSTRAINT fk_benchmark_datasets_dataset_id_project FOREIGN KEY(project_id, dataset_id) REFERENCES benchmark_datasets (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE benchmark_runs ADD CONSTRAINT fk_benchmark_runs_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE benchmark_runs ADD CONSTRAINT fk_preparations_preparation_id_project FOREIGN KEY(project_id, preparation_id) REFERENCES preparations (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE capability_authorizations ADD CONSTRAINT fk_capability_authorizations_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE capability_authorizations ADD CONSTRAINT fk_capability_authorizations_subject_id_users FOREIGN KEY(subject_id) REFERENCES users (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE capability_authorizations ADD CONSTRAINT fk_managed_commands_command_id_project FOREIGN KEY(project_id, command_id) REFERENCES managed_commands (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE capability_authorizations ADD CONSTRAINT fk_managed_commands_parent_command_id_project FOREIGN KEY(project_id, parent_command_id) REFERENCES managed_commands (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE capability_authorizations ADD CONSTRAINT fk_preparations_preparation_id_project FOREIGN KEY(project_id, preparation_id) REFERENCES preparations (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE capability_consumptions ADD CONSTRAINT fk_capability_authorizations_capability_id_project FOREIGN KEY(project_id, capability_id) REFERENCES capability_authorizations (project_id, capability_id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE capability_consumptions ADD CONSTRAINT fk_capability_consumptions_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE capability_consumptions ADD CONSTRAINT fk_execution_runs_execution_run_id_project FOREIGN KEY(project_id, execution_run_id) REFERENCES execution_runs (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE capability_consumptions ADD CONSTRAINT fk_managed_commands_command_id_project FOREIGN KEY(project_id, command_id) REFERENCES managed_commands (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE clarifications ADD CONSTRAINT fk_agent_runs_agent_run_id_project FOREIGN KEY(project_id, agent_run_id) REFERENCES agent_runs (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE clarifications ADD CONSTRAINT fk_clarifications_answered_by_users FOREIGN KEY(answered_by) REFERENCES users (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE clarifications ADD CONSTRAINT fk_clarifications_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE connection_tests ADD CONSTRAINT fk_connection_tests_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE connection_tests ADD CONSTRAINT fk_connections_connection_id_project FOREIGN KEY(project_id, connection_id) REFERENCES connections (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE connections ADD CONSTRAINT fk_connections_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE connections ADD CONSTRAINT fk_secret_references_secret_ref_id_project FOREIGN KEY(project_id, secret_ref_id) REFERENCES secret_references (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE conversation_messages ADD CONSTRAINT fk_conversation_messages_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE conversation_messages ADD CONSTRAINT fk_conversations_conversation_id_project FOREIGN KEY(project_id, conversation_id) REFERENCES conversations (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE conversations ADD CONSTRAINT fk_conversations_creator_id_users FOREIGN KEY(creator_id) REFERENCES users (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE conversations ADD CONSTRAINT fk_conversations_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE conversations ADD CONSTRAINT fk_pipelines_pipeline_id_project FOREIGN KEY(project_id, pipeline_id) REFERENCES pipelines (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE diagnostic_reports ADD CONSTRAINT fk_diagnostic_reports_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE diagnostic_reports ADD CONSTRAINT fk_execution_runs_execution_run_id_project FOREIGN KEY(project_id, execution_run_id) REFERENCES execution_runs (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE evolution_authorizations ADD CONSTRAINT fk_evolution_authorizations_approved_by_users FOREIGN KEY(approved_by) REFERENCES users (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE evolution_authorizations ADD CONSTRAINT fk_evolution_authorizations_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE evolution_authorizations ADD CONSTRAINT fk_evolution_candidates_candidate_id_project FOREIGN KEY(project_id, candidate_id) REFERENCES evolution_candidates (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE evolution_candidates ADD CONSTRAINT fk_evolution_candidates_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE execution_runs ADD CONSTRAINT fk_execution_runs_original_run_id_project FOREIGN KEY(project_id, original_run_id) REFERENCES execution_runs (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE execution_runs ADD CONSTRAINT fk_execution_runs_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE execution_runs ADD CONSTRAINT fk_pipeline_versions_version_id_project FOREIGN KEY(project_id, version_id) REFERENCES pipeline_versions (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE execution_runs ADD CONSTRAINT fk_preparations_preparation_id_project FOREIGN KEY(project_id, preparation_id) REFERENCES preparations (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE file_assets ADD CONSTRAINT fk_file_assets_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE ledger_checkpoints ADD CONSTRAINT fk_ledger_checkpoints_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE local_credentials ADD CONSTRAINT fk_local_credentials_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE managed_commands ADD CONSTRAINT fk_capability_authorizations_capability_id_project FOREIGN KEY(project_id, capability_id) REFERENCES capability_authorizations (project_id, capability_id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE managed_commands ADD CONSTRAINT fk_execution_runs_execution_run_id_project FOREIGN KEY(project_id, execution_run_id) REFERENCES execution_runs (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE managed_commands ADD CONSTRAINT fk_managed_commands_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE managed_commands ADD CONSTRAINT fk_preparations_preparation_id_project FOREIGN KEY(project_id, preparation_id) REFERENCES preparations (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE metadata_profiles ADD CONSTRAINT fk_connections_connection_id_project FOREIGN KEY(project_id, connection_id) REFERENCES connections (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE metadata_profiles ADD CONSTRAINT fk_file_assets_file_asset_id_project FOREIGN KEY(project_id, file_asset_id) REFERENCES file_assets (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE metadata_profiles ADD CONSTRAINT fk_metadata_profiles_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE outbox_messages ADD CONSTRAINT fk_managed_commands_command_id_project FOREIGN KEY(project_id, command_id) REFERENCES managed_commands (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE outbox_messages ADD CONSTRAINT fk_outbox_messages_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE pipeline_artifacts ADD CONSTRAINT fk_pipeline_artifacts_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE pipeline_artifacts ADD CONSTRAINT fk_pipeline_versions_version_id_project FOREIGN KEY(project_id, version_id) REFERENCES pipeline_versions (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE pipeline_versions ADD CONSTRAINT fk_pipeline_versions_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE pipeline_versions ADD CONSTRAINT fk_pipelines_pipeline_id_project FOREIGN KEY(project_id, pipeline_id) REFERENCES pipelines (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE pipeline_versions ADD CONSTRAINT fk_validation_reports_validation_report_id_project FOREIGN KEY(project_id, validation_report_id) REFERENCES validation_reports (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE pipelines ADD CONSTRAINT fk_pipelines_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE preparations ADD CONSTRAINT fk_execution_runs_execution_run_id_project FOREIGN KEY(project_id, execution_run_id) REFERENCES execution_runs (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE preparations ADD CONSTRAINT fk_pipeline_versions_version_id_project FOREIGN KEY(project_id, version_id) REFERENCES pipeline_versions (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE preparations ADD CONSTRAINT fk_preparations_maker_id_users FOREIGN KEY(maker_id) REFERENCES users (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE preparations ADD CONSTRAINT fk_preparations_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE profile_task_results ADD CONSTRAINT fk_metadata_profiles_profile_id_project FOREIGN KEY(project_id, profile_id) REFERENCES metadata_profiles (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE profile_task_results ADD CONSTRAINT fk_profile_tasks_task_id_project FOREIGN KEY(project_id, task_id) REFERENCES profile_tasks (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE profile_tasks ADD CONSTRAINT fk_connections_connection_id_project FOREIGN KEY(project_id, connection_id) REFERENCES connections (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE profile_tasks ADD CONSTRAINT fk_profile_tasks_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE profile_tasks ADD CONSTRAINT fk_profile_tasks_requested_by_users FOREIGN KEY(requested_by) REFERENCES users (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE project_memberships ADD CONSTRAINT fk_project_memberships_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE project_memberships ADD CONSTRAINT fk_project_memberships_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE project_role_grants ADD CONSTRAINT fk_project_role_grants_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE project_role_grants ADD CONSTRAINT fk_project_role_grants_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE publication_records ADD CONSTRAINT fk_execution_runs_execution_run_id_project FOREIGN KEY(project_id, execution_run_id) REFERENCES execution_runs (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE publication_records ADD CONSTRAINT fk_managed_commands_command_id_project FOREIGN KEY(project_id, command_id) REFERENCES managed_commands (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE publication_records ADD CONSTRAINT fk_publication_records_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE quality_reports ADD CONSTRAINT fk_execution_runs_execution_run_id_project FOREIGN KEY(project_id, execution_run_id) REFERENCES execution_runs (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE quality_reports ADD CONSTRAINT fk_quality_reports_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE quality_violations ADD CONSTRAINT fk_execution_runs_execution_run_id_project FOREIGN KEY(project_id, execution_run_id) REFERENCES execution_runs (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE quality_violations ADD CONSTRAINT fk_quality_violations_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE run_operations ADD CONSTRAINT fk_execution_runs_execution_run_id_project FOREIGN KEY(project_id, execution_run_id) REFERENCES execution_runs (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE run_operations ADD CONSTRAINT fk_managed_commands_command_id_project FOREIGN KEY(project_id, command_id) REFERENCES managed_commands (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE run_operations ADD CONSTRAINT fk_publication_records_publication_id_project FOREIGN KEY(project_id, publication_id) REFERENCES publication_records (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE run_operations ADD CONSTRAINT fk_run_operations_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE run_operations ADD CONSTRAINT fk_run_operations_requested_by_users FOREIGN KEY(requested_by) REFERENCES users (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE runtime_supervision_snapshots ADD CONSTRAINT fk_execution_runs_execution_run_id_project FOREIGN KEY(project_id, execution_run_id) REFERENCES execution_runs (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE runtime_supervision_snapshots ADD CONSTRAINT fk_runtime_supervision_snapshots_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE schedule_definitions ADD CONSTRAINT fk_pipeline_versions_version_id_project FOREIGN KEY(project_id, version_id) REFERENCES pipeline_versions (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE schedule_definitions ADD CONSTRAINT fk_pipelines_pipeline_id_project FOREIGN KEY(project_id, pipeline_id) REFERENCES pipelines (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE schedule_definitions ADD CONSTRAINT fk_schedule_definitions_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE schedule_trigger_records ADD CONSTRAINT fk_schedule_trigger_records_preparation_id_preparations FOREIGN KEY(preparation_id) REFERENCES preparations (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE schedule_trigger_records ADD CONSTRAINT fk_schedule_trigger_records_schedule_id_schedule_definitions FOREIGN KEY(schedule_id) REFERENCES schedule_definitions (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE secret_references ADD CONSTRAINT fk_secret_references_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE target_heads ADD CONSTRAINT fk_publication_records_current_publication_id_project FOREIGN KEY(project_id, current_publication_id) REFERENCES publication_records (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE target_heads ADD CONSTRAINT fk_target_heads_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""ALTER TABLE validation_reports ADD CONSTRAINT fk_pipeline_versions_version_id_project FOREIGN KEY(project_id, version_id) REFERENCES pipeline_versions (project_id, id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"""
    )

    op.execute(
        r"""ALTER TABLE validation_reports ADD CONSTRAINT fk_validation_reports_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE RESTRICT"""
    )

    op.execute(
        r"""CREATE INDEX ix_agent_runs_version ON agent_runs (project_id, version_id, created_at, id)"""
    )

    op.execute(
        r"""CREATE INDEX ix_approval_requests_pending ON approval_requests (project_id, status, created_at, id)"""
    )

    op.execute(
        r"""CREATE INDEX ix_audit_resource ON audit_events (project_id, resource_type, resource_id, sequence_no)"""
    )

    op.execute(
        r"""CREATE INDEX ix_auth_sessions_user_expiry ON auth_sessions (user_id, expires_at)"""
    )

    op.execute(
        r"""CREATE INDEX ix_case_results_run ON benchmark_case_results (benchmark_run_id, passed, case_id)"""
    )

    op.execute(
        r"""CREATE INDEX ix_cases_dataset ON benchmark_cases (dataset_id, level, case_key)"""
    )

    op.execute(
        r"""CREATE INDEX ix_benchmark_runs_recent ON benchmark_runs (project_id, created_at, id)"""
    )

    op.execute(
        r"""CREATE UNIQUE INDEX uq_l2_preparation_submission ON benchmark_runs (preparation_id) WHERE level = 'L2'"""
    )

    op.execute(
        r"""CREATE INDEX ix_capability_authorizations_expiry ON capability_authorizations (expires_at)"""
    )

    op.execute(
        r"""CREATE INDEX ix_connection_tests_recent ON connection_tests (project_id, connection_id, created_at, id)"""
    )

    op.execute(
        r"""CREATE INDEX ix_connections_type ON connections (project_id, connector_type, id)"""
    )

    op.execute(
        r"""CREATE INDEX ix_conversations_pipeline ON conversations (project_id, pipeline_id, created_at, id)"""
    )

    op.execute(
        r"""CREATE INDEX ix_diagnostics_run ON diagnostic_reports (project_id, execution_run_id, created_at, id)"""
    )

    op.execute(
        r"""CREATE INDEX ix_evolution_auth_active ON evolution_authorizations (project_id, status, expires_at, id)"""
    )

    op.execute(
        r"""CREATE INDEX ix_execution_runs_recent ON execution_runs (project_id, created_at, id)"""
    )

    op.execute(
        r"""CREATE INDEX ix_execution_runs_status ON execution_runs (project_id, status, created_at, id)"""
    )

    op.execute(
        r"""CREATE INDEX ix_file_assets_recent ON file_assets (project_id, created_at, id)"""
    )

    op.execute(
        r"""CREATE INDEX ix_managed_commands_pending ON managed_commands (project_id, status, created_at, id)"""
    )

    op.execute(
        r"""CREATE INDEX ix_profiles_connection ON metadata_profiles (project_id, connection_id, created_at, id)"""
    )

    op.execute(
        r"""CREATE INDEX ix_profiles_file ON metadata_profiles (project_id, file_asset_id, created_at, id)"""
    )

    op.execute(
        r"""CREATE INDEX ix_outbox_messages_pending ON outbox_messages (next_attempt_at, id) WHERE delivery_status = 'pending'"""
    )

    op.execute(
        r"""CREATE INDEX ix_versions_history ON pipeline_versions (project_id, pipeline_id, version_number)"""
    )

    op.execute(r"""CREATE INDEX ix_pipelines_recent ON pipelines (project_id, created_at, id)""")

    op.execute(
        r"""CREATE INDEX ix_preparations_recent ON preparations (project_id, created_at, id)"""
    )

    op.execute(
        r"""CREATE INDEX ix_preparations_version_expiry ON preparations (version_id, expires_at)"""
    )

    op.execute(
        r"""CREATE INDEX ix_profile_tasks_connection ON profile_tasks (project_id, connection_id, created_at, id)"""
    )

    op.execute(r"""CREATE INDEX ix_profile_tasks_pending ON profile_tasks (status, created_at)""")

    op.execute(
        r"""CREATE INDEX ix_memberships_user_project ON project_memberships (user_id, project_id)"""
    )

    op.execute(
        r"""CREATE INDEX ix_role_grants_slot ON project_role_grants (project_id, role_slot, user_id)"""
    )

    op.execute(
        r"""CREATE INDEX ix_quality_reports_run ON quality_reports (project_id, execution_run_id, created_at, id)"""
    )

    op.execute(
        r"""CREATE INDEX ix_quality_violations_run ON quality_violations (project_id, execution_run_id, error_code, id)"""
    )

    op.execute(
        r"""CREATE INDEX ix_operations_run ON run_operations (project_id, execution_run_id, kind, created_at, id)"""
    )

    op.execute(
        r"""CREATE INDEX ix_supervision_run ON runtime_supervision_snapshots (project_id, execution_run_id, created_at, id)"""
    )

    op.execute(
        r"""CREATE INDEX ix_schedule_due ON schedule_definitions (next_trigger_at, id) WHERE enabled = true"""
    )

    op.execute(
        r"""CREATE INDEX ix_validation_reports_version ON validation_reports (project_id, version_id, content_digest, created_at)"""
    )

    op.execute(r"""CREATE FUNCTION etl_forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = '23514';
END $$""")

    op.execute(r"""CREATE FUNCTION etl_guard_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.is_immutable AND (TG_OP = 'DELETE' OR NEW IS DISTINCT FROM OLD) THEN
    RAISE EXCEPTION 'frozen version is immutable' USING ERRCODE = '23514';
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$""")

    op.execute(r"""CREATE TRIGGER frozen_version_guard BEFORE UPDATE OR DELETE ON pipeline_versions
FOR EACH ROW EXECUTE FUNCTION etl_guard_version()""")

    op.execute(r"""CREATE FUNCTION etl_guard_frozen_complete() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE version_row pipeline_versions%ROWTYPE; report_row validation_reports%ROWTYPE;
BEGIN
  SELECT * INTO version_row FROM pipeline_versions WHERE id = NEW.id;
  IF NOT version_row.is_immutable THEN RETURN NEW; END IF;
  SELECT * INTO report_row FROM validation_reports WHERE id = version_row.validation_report_id;
  IF NOT FOUND OR NOT report_row.passed OR report_row.version_id <> version_row.id
      OR report_row.project_id <> version_row.project_id THEN
    RAISE EXCEPTION 'frozen version requires its passing validation report' USING ERRCODE = '23514';
  END IF;
  IF (SELECT count(*) FROM pipeline_artifacts WHERE version_id = NEW.id) <> 5 THEN
    RAISE EXCEPTION 'frozen version requires five immutable artifacts' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$""")

    op.execute(r"""CREATE CONSTRAINT TRIGGER frozen_version_complete AFTER INSERT OR UPDATE ON pipeline_versions
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION etl_guard_frozen_complete()""")

    op.execute(r"""CREATE FUNCTION etl_guard_artifact_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE frozen boolean; changed_in_transaction boolean;
BEGIN
  SELECT is_immutable, xmin::text::bigint = (txid_current() % 4294967296)
  INTO frozen, changed_in_transaction FROM pipeline_versions WHERE id = NEW.version_id FOR UPDATE;
  IF frozen AND NOT changed_in_transaction THEN
    RAISE EXCEPTION 'cannot add artifacts to a previously frozen version' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$""")

    op.execute(r"""CREATE TRIGGER artifact_insert_guard BEFORE INSERT ON pipeline_artifacts
FOR EACH ROW EXECUTE FUNCTION etl_guard_artifact_insert()""")

    op.execute(r"""CREATE FUNCTION etl_guard_file_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW.project_id, NEW.file_name, NEW.file_path, NEW.file_size, NEW.file_format, NEW.content_digest)
     IS DISTINCT FROM
     (OLD.project_id, OLD.file_name, OLD.file_path, OLD.file_size, OLD.file_format, OLD.content_digest) THEN
    RAISE EXCEPTION 'file replacement requires a new asset' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$""")

    op.execute(r"""CREATE TRIGGER file_identity_guard BEFORE UPDATE ON file_assets
FOR EACH ROW EXECUTE FUNCTION etl_guard_file_identity()""")

    op.execute(r"""CREATE FUNCTION etl_guard_preparation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pipeline_versions v WHERE v.id = NEW.version_id
       AND v.project_id = NEW.project_id AND v.is_immutable) THEN
    RAISE EXCEPTION 'preparation requires a frozen version in the same project' USING ERRCODE = '23514';
  END IF;
  IF NEW.purpose = 'run_l2_benchmark' AND NOT EXISTS (SELECT 1 FROM benchmark_datasets d
       WHERE d.id = (NEW.evaluation_binding->>'dataset_id')::uuid AND d.project_id = NEW.project_id
         AND d.content_digest = NEW.evaluation_binding->>'dataset_digest') THEN
    RAISE EXCEPTION 'L2 preparation must freeze the exact dataset' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$""")

    op.execute(r"""CREATE TRIGGER preparation_guard BEFORE INSERT ON preparations
FOR EACH ROW EXECUTE FUNCTION etl_guard_preparation()""")

    op.execute(r"""CREATE FUNCTION etl_guard_approval() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE prep preparations%ROWTYPE; maker_person uuid; checker_person uuid; expected_role text;
BEGIN
  IF TG_OP = 'UPDATE' AND OLD.status <> 'pending' AND NEW IS DISTINCT FROM OLD THEN
    RAISE EXCEPTION 'approval decision is immutable' USING ERRCODE = '23514';
  END IF;
  SELECT * INTO prep FROM preparations WHERE id = NEW.preparation_id FOR UPDATE;
  IF NOT FOUND OR prep.project_id <> NEW.project_id OR prep.version_id <> NEW.version_id THEN
    RAISE EXCEPTION 'approval preparation/version mismatch' USING ERRCODE = '23514';
  END IF;
  IF NEW.status = 'pending' THEN RETURN NEW; END IF;
  SELECT person_ref INTO maker_person FROM users WHERE id = prep.maker_id;
  SELECT person_ref INTO checker_person FROM users WHERE id = NEW.approver_id AND status = 'enabled';
  IF checker_person IS NULL OR checker_person = maker_person THEN
    RAISE EXCEPTION 'maker and checker must be independent people' USING ERRCODE = '23514';
  END IF;
  IF clock_timestamp() >= prep.expires_at THEN
    RAISE EXCEPTION 'preparation has expired' USING ERRCODE = '23514';
  END IF;
  expected_role := CASE NEW.required_role WHEN 'data' THEN 'data_checker' ELSE 'security_checker' END;
  IF NOT EXISTS (SELECT 1 FROM project_memberships WHERE project_id = NEW.project_id
       AND user_id = NEW.approver_id AND role = expected_role)
     OR NOT EXISTS (SELECT 1 FROM project_role_grants WHERE project_id = NEW.project_id
       AND user_id = NEW.approver_id AND role_slot = NEW.required_role) THEN
    RAISE EXCEPTION 'checker lacks current role or slot' USING ERRCODE = '23514';
  END IF;
  IF EXISTS (SELECT 1 FROM approval_requests a JOIN users u ON u.id = a.approver_id
      WHERE a.preparation_id = NEW.preparation_id AND a.id <> NEW.id
        AND u.person_ref = checker_person) THEN
    RAISE EXCEPTION 'one person cannot occupy both approval slots' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$""")

    op.execute(r"""CREATE TRIGGER approval_guard BEFORE INSERT OR UPDATE ON approval_requests
FOR EACH ROW EXECUTE FUNCTION etl_guard_approval()""")

    op.execute(r"""CREATE FUNCTION etl_guard_capability_binding() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM managed_commands c WHERE c.id = NEW.command_id
      AND c.project_id = NEW.project_id AND c.capability_id = NEW.capability_id
      AND c.preparation_id = NEW.preparation_id AND c.tool = NEW.tool) THEN
    RAISE EXCEPTION 'capability does not bind the exact command' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$""")

    op.execute(r"""CREATE CONSTRAINT TRIGGER capability_binding AFTER INSERT ON capability_authorizations
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION etl_guard_capability_binding()""")

    op.execute(r"""CREATE FUNCTION etl_guard_consumption() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE auth capability_authorizations%ROWTYPE; command_row managed_commands%ROWTYPE;
BEGIN
  SELECT * INTO auth FROM capability_authorizations WHERE capability_id = NEW.capability_id FOR UPDATE;
  SELECT * INTO command_row FROM managed_commands WHERE id = NEW.command_id FOR UPDATE;
  NEW.consumed_at := clock_timestamp();
  IF auth.capability_id IS NULL OR command_row.id IS NULL
    OR auth.command_id <> NEW.command_id OR command_row.capability_id <> NEW.capability_id
    OR auth.project_id <> NEW.project_id OR command_row.project_id <> NEW.project_id
    OR command_row.execution_run_id <> NEW.execution_run_id OR auth.token_digest <> NEW.token_digest
    OR NEW.consumed_at >= auth.expires_at THEN
    RAISE EXCEPTION 'invalid, expired, or mismatched capability consumption' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$""")

    op.execute(r"""CREATE TRIGGER consumption_guard BEFORE INSERT ON capability_consumptions
FOR EACH ROW EXECUTE FUNCTION etl_guard_consumption()""")

    op.execute(r"""CREATE FUNCTION etl_guard_audit_chain() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE last_sequence bigint; last_hash text;
BEGIN
  PERFORM 1 FROM projects WHERE id = NEW.project_id FOR UPDATE;
  SELECT sequence_no, event_hash INTO last_sequence, last_hash FROM audit_events
    WHERE project_id = NEW.project_id ORDER BY sequence_no DESC LIMIT 1;
  IF NEW.sequence_no <> COALESCE(last_sequence, 0) + 1
     OR NEW.prev_event_hash IS DISTINCT FROM last_hash THEN
    RAISE EXCEPTION 'audit chain sequence/predecessor mismatch' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$""")

    op.execute(r"""CREATE TRIGGER audit_chain_guard BEFORE INSERT ON audit_events
FOR EACH ROW EXECUTE FUNCTION etl_guard_audit_chain()""")

    op.execute(r"""CREATE FUNCTION etl_guard_task_result() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE task profile_tasks%ROWTYPE; profile metadata_profiles%ROWTYPE;
BEGIN
  SELECT * INTO task FROM profile_tasks WHERE id = NEW.task_id FOR UPDATE;
  SELECT * INTO profile FROM metadata_profiles WHERE id = NEW.profile_id;
  IF task.project_id IS DISTINCT FROM NEW.project_id OR profile.project_id IS DISTINCT FROM NEW.project_id
    OR profile.connection_id IS DISTINCT FROM task.connection_id
    OR NOT task.resource_names @> to_jsonb(ARRAY[profile.resource_name]) THEN
    RAISE EXCEPTION 'task result must match a requested resource' USING ERRCODE = '23514';
  END IF;
  IF EXISTS (SELECT 1 FROM profile_task_results r JOIN metadata_profiles p ON p.id = r.profile_id
      WHERE r.task_id = NEW.task_id AND p.resource_name = profile.resource_name) THEN
    RAISE EXCEPTION 'task resource already has a result' USING ERRCODE = '23505';
  END IF;
  RETURN NEW;
END $$""")

    op.execute(r"""CREATE TRIGGER task_result_guard BEFORE INSERT ON profile_task_results
FOR EACH ROW EXECUTE FUNCTION etl_guard_task_result()""")

    op.execute(r"""CREATE FUNCTION etl_guard_profile_task() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status IN ('succeeded','failed') AND NEW IS DISTINCT FROM OLD THEN
    RAISE EXCEPTION 'terminal profile task cannot be rewritten' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$""")

    op.execute(r"""CREATE TRIGGER profile_task_terminal_guard BEFORE UPDATE ON profile_tasks
FOR EACH ROW EXECUTE FUNCTION etl_guard_profile_task()""")

    op.execute(r"""CREATE FUNCTION etl_guard_task_complete() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE task profile_tasks%ROWTYPE;
BEGIN
  SELECT * INTO task FROM profile_tasks WHERE id = NEW.id;
  IF task.status = 'succeeded' AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(task.resource_names) n
       WHERE NOT EXISTS (SELECT 1 FROM profile_task_results r JOIN metadata_profiles p ON p.id = r.profile_id
           WHERE r.task_id = task.id AND p.resource_name = n)) THEN
    RAISE EXCEPTION 'successful task lacks resource results' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$""")

    op.execute(r"""CREATE CONSTRAINT TRIGGER task_complete AFTER INSERT OR UPDATE ON profile_tasks
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION etl_guard_task_complete()""")

    op.execute(r"""CREATE FUNCTION etl_guard_benchmark_case() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE run_row benchmark_runs%ROWTYPE; case_row benchmark_cases%ROWTYPE;
BEGIN
  SELECT * INTO run_row FROM benchmark_runs WHERE id = NEW.benchmark_run_id;
  SELECT * INTO case_row FROM benchmark_cases WHERE id = NEW.case_id;
  IF run_row.project_id IS DISTINCT FROM NEW.project_id OR case_row.project_id IS DISTINCT FROM NEW.project_id
    OR case_row.dataset_id IS DISTINCT FROM run_row.dataset_id OR case_row.level IS DISTINCT FROM run_row.level THEN
    RAISE EXCEPTION 'benchmark case does not belong to selected dataset/level' USING ERRCODE = '23514';
  END IF;
  IF TG_TABLE_NAME = 'benchmark_case_executions' THEN
    IF run_row.level <> 'L2' OR NOT EXISTS (SELECT 1 FROM execution_runs e WHERE e.id = NEW.execution_run_id
         AND e.project_id = NEW.project_id AND e.preparation_id = run_row.preparation_id) THEN
      RAISE EXCEPTION 'L2 case execution must use its approved run' USING ERRCODE = '23514';
    END IF;
  ELSIF NEW.execution_run_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM benchmark_case_executions e
        WHERE e.benchmark_run_id = NEW.benchmark_run_id AND e.case_id = NEW.case_id
          AND e.execution_run_id = NEW.execution_run_id AND e.project_id = NEW.project_id) THEN
    RAISE EXCEPTION 'case result must reference its exact execution binding' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$""")

    op.execute(r"""CREATE TRIGGER benchmark_execution_guard BEFORE INSERT ON benchmark_case_executions
FOR EACH ROW EXECUTE FUNCTION etl_guard_benchmark_case()""")

    op.execute(r"""CREATE TRIGGER benchmark_result_guard BEFORE INSERT ON benchmark_case_results
FOR EACH ROW EXECUTE FUNCTION etl_guard_benchmark_case()""")

    op.execute(r"""CREATE FUNCTION etl_guard_evolution_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE evidence_id text;
BEGIN
  FOR evidence_id IN SELECT jsonb_array_elements_text(NEW.benchmark_run_ids) LOOP
    PERFORM 1 FROM benchmark_runs WHERE id = evidence_id::uuid AND project_id = NEW.project_id FOR KEY SHARE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'evolution evidence must belong to same project' USING ERRCODE = '23503';
    END IF;
  END LOOP;
  RETURN NEW;
END $$""")

    op.execute(r"""CREATE TRIGGER evolution_evidence_guard BEFORE INSERT ON evolution_candidates
FOR EACH ROW EXECUTE FUNCTION etl_guard_evolution_evidence()""")

    op.execute(
        r"""CREATE TRIGGER append_only_guard BEFORE UPDATE OR DELETE ON metadata_profiles FOR EACH ROW EXECUTE FUNCTION etl_forbid_mutation()"""
    )

    op.execute(
        r"""CREATE TRIGGER append_only_guard BEFORE UPDATE OR DELETE ON pipeline_artifacts FOR EACH ROW EXECUTE FUNCTION etl_forbid_mutation()"""
    )

    op.execute(
        r"""CREATE TRIGGER append_only_guard BEFORE UPDATE OR DELETE ON conversation_messages FOR EACH ROW EXECUTE FUNCTION etl_forbid_mutation()"""
    )

    op.execute(
        r"""CREATE TRIGGER append_only_guard BEFORE UPDATE OR DELETE ON validation_reports FOR EACH ROW EXECUTE FUNCTION etl_forbid_mutation()"""
    )

    op.execute(
        r"""CREATE TRIGGER append_only_guard BEFORE UPDATE OR DELETE ON preparations FOR EACH ROW EXECUTE FUNCTION etl_forbid_mutation()"""
    )

    op.execute(
        r"""CREATE TRIGGER append_only_guard BEFORE UPDATE OR DELETE ON capability_authorizations FOR EACH ROW EXECUTE FUNCTION etl_forbid_mutation()"""
    )

    op.execute(
        r"""CREATE TRIGGER append_only_guard BEFORE UPDATE OR DELETE ON capability_consumptions FOR EACH ROW EXECUTE FUNCTION etl_forbid_mutation()"""
    )

    op.execute(
        r"""CREATE TRIGGER append_only_guard BEFORE UPDATE OR DELETE ON runtime_supervision_snapshots FOR EACH ROW EXECUTE FUNCTION etl_forbid_mutation()"""
    )

    op.execute(
        r"""CREATE TRIGGER append_only_guard BEFORE UPDATE OR DELETE ON quality_reports FOR EACH ROW EXECUTE FUNCTION etl_forbid_mutation()"""
    )

    op.execute(
        r"""CREATE TRIGGER append_only_guard BEFORE UPDATE OR DELETE ON quality_violations FOR EACH ROW EXECUTE FUNCTION etl_forbid_mutation()"""
    )

    op.execute(
        r"""CREATE TRIGGER append_only_guard BEFORE UPDATE OR DELETE ON diagnostic_reports FOR EACH ROW EXECUTE FUNCTION etl_forbid_mutation()"""
    )

    op.execute(
        r"""CREATE TRIGGER append_only_guard BEFORE UPDATE OR DELETE ON audit_events FOR EACH ROW EXECUTE FUNCTION etl_forbid_mutation()"""
    )

    op.execute(
        r"""CREATE TRIGGER append_only_guard BEFORE UPDATE OR DELETE ON ledger_checkpoints FOR EACH ROW EXECUTE FUNCTION etl_forbid_mutation()"""
    )

    op.execute(
        r"""CREATE TRIGGER append_only_guard BEFORE UPDATE OR DELETE ON benchmark_datasets FOR EACH ROW EXECUTE FUNCTION etl_forbid_mutation()"""
    )

    op.execute(
        r"""CREATE TRIGGER append_only_guard BEFORE UPDATE OR DELETE ON benchmark_cases FOR EACH ROW EXECUTE FUNCTION etl_forbid_mutation()"""
    )

    op.execute(
        r"""CREATE TRIGGER append_only_guard BEFORE UPDATE OR DELETE ON benchmark_case_results FOR EACH ROW EXECUTE FUNCTION etl_forbid_mutation()"""
    )

    op.execute(
        r"""CREATE TRIGGER append_only_guard BEFORE UPDATE OR DELETE ON benchmark_case_executions FOR EACH ROW EXECUTE FUNCTION etl_forbid_mutation()"""
    )

    op.execute(
        r"""CREATE TRIGGER append_only_guard BEFORE UPDATE OR DELETE ON profile_task_results FOR EACH ROW EXECUTE FUNCTION etl_forbid_mutation()"""
    )

    op.execute(
        r"""CREATE TRIGGER append_only_guard BEFORE UPDATE OR DELETE ON evolution_candidates FOR EACH ROW EXECUTE FUNCTION etl_forbid_mutation()"""
    )

    op.execute(
        r"""CREATE TRIGGER append_only_guard BEFORE UPDATE OR DELETE ON schedule_trigger_records FOR EACH ROW EXECUTE FUNCTION etl_forbid_mutation()"""
    )


def downgrade() -> None:
    op.execute(r"""DROP FUNCTION etl_guard_evolution_evidence() CASCADE""")

    op.execute(r"""DROP FUNCTION etl_guard_benchmark_case() CASCADE""")

    op.execute(r"""DROP FUNCTION etl_guard_task_complete() CASCADE""")

    op.execute(r"""DROP FUNCTION etl_guard_profile_task() CASCADE""")

    op.execute(r"""DROP FUNCTION etl_guard_task_result() CASCADE""")

    op.execute(r"""DROP FUNCTION etl_guard_audit_chain() CASCADE""")

    op.execute(r"""DROP FUNCTION etl_guard_consumption() CASCADE""")

    op.execute(r"""DROP FUNCTION etl_guard_capability_binding() CASCADE""")

    op.execute(r"""DROP FUNCTION etl_guard_approval() CASCADE""")

    op.execute(r"""DROP FUNCTION etl_guard_preparation() CASCADE""")

    op.execute(r"""DROP FUNCTION etl_guard_file_identity() CASCADE""")

    op.execute(r"""DROP FUNCTION etl_guard_artifact_insert() CASCADE""")

    op.execute(r"""DROP FUNCTION etl_guard_frozen_complete() CASCADE""")

    op.execute(r"""DROP FUNCTION etl_guard_version() CASCADE""")

    op.execute(r"""DROP FUNCTION etl_forbid_mutation() CASCADE""")

    op.execute(
        r"""ALTER TABLE validation_reports DROP CONSTRAINT fk_validation_reports_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE validation_reports DROP CONSTRAINT fk_pipeline_versions_version_id_project"""
    )

    op.execute(r"""ALTER TABLE target_heads DROP CONSTRAINT fk_target_heads_project_id_projects""")

    op.execute(
        r"""ALTER TABLE target_heads DROP CONSTRAINT fk_publication_records_current_publication_id_project"""
    )

    op.execute(
        r"""ALTER TABLE secret_references DROP CONSTRAINT fk_secret_references_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE schedule_trigger_records DROP CONSTRAINT fk_schedule_trigger_records_schedule_id_schedule_definitions"""
    )

    op.execute(
        r"""ALTER TABLE schedule_trigger_records DROP CONSTRAINT fk_schedule_trigger_records_preparation_id_preparations"""
    )

    op.execute(
        r"""ALTER TABLE schedule_definitions DROP CONSTRAINT fk_schedule_definitions_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE schedule_definitions DROP CONSTRAINT fk_pipelines_pipeline_id_project"""
    )

    op.execute(
        r"""ALTER TABLE schedule_definitions DROP CONSTRAINT fk_pipeline_versions_version_id_project"""
    )

    op.execute(
        r"""ALTER TABLE runtime_supervision_snapshots DROP CONSTRAINT fk_runtime_supervision_snapshots_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE runtime_supervision_snapshots DROP CONSTRAINT fk_execution_runs_execution_run_id_project"""
    )

    op.execute(
        r"""ALTER TABLE run_operations DROP CONSTRAINT fk_run_operations_requested_by_users"""
    )

    op.execute(
        r"""ALTER TABLE run_operations DROP CONSTRAINT fk_run_operations_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE run_operations DROP CONSTRAINT fk_publication_records_publication_id_project"""
    )

    op.execute(
        r"""ALTER TABLE run_operations DROP CONSTRAINT fk_managed_commands_command_id_project"""
    )

    op.execute(
        r"""ALTER TABLE run_operations DROP CONSTRAINT fk_execution_runs_execution_run_id_project"""
    )

    op.execute(
        r"""ALTER TABLE quality_violations DROP CONSTRAINT fk_quality_violations_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE quality_violations DROP CONSTRAINT fk_execution_runs_execution_run_id_project"""
    )

    op.execute(
        r"""ALTER TABLE quality_reports DROP CONSTRAINT fk_quality_reports_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE quality_reports DROP CONSTRAINT fk_execution_runs_execution_run_id_project"""
    )

    op.execute(
        r"""ALTER TABLE publication_records DROP CONSTRAINT fk_publication_records_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE publication_records DROP CONSTRAINT fk_managed_commands_command_id_project"""
    )

    op.execute(
        r"""ALTER TABLE publication_records DROP CONSTRAINT fk_execution_runs_execution_run_id_project"""
    )

    op.execute(
        r"""ALTER TABLE project_role_grants DROP CONSTRAINT fk_project_role_grants_user_id_users"""
    )

    op.execute(
        r"""ALTER TABLE project_role_grants DROP CONSTRAINT fk_project_role_grants_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE project_memberships DROP CONSTRAINT fk_project_memberships_user_id_users"""
    )

    op.execute(
        r"""ALTER TABLE project_memberships DROP CONSTRAINT fk_project_memberships_project_id_projects"""
    )

    op.execute(r"""ALTER TABLE profile_tasks DROP CONSTRAINT fk_profile_tasks_requested_by_users""")

    op.execute(
        r"""ALTER TABLE profile_tasks DROP CONSTRAINT fk_profile_tasks_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE profile_tasks DROP CONSTRAINT fk_connections_connection_id_project"""
    )

    op.execute(
        r"""ALTER TABLE profile_task_results DROP CONSTRAINT fk_profile_tasks_task_id_project"""
    )

    op.execute(
        r"""ALTER TABLE profile_task_results DROP CONSTRAINT fk_metadata_profiles_profile_id_project"""
    )

    op.execute(r"""ALTER TABLE preparations DROP CONSTRAINT fk_preparations_project_id_projects""")

    op.execute(r"""ALTER TABLE preparations DROP CONSTRAINT fk_preparations_maker_id_users""")

    op.execute(
        r"""ALTER TABLE preparations DROP CONSTRAINT fk_pipeline_versions_version_id_project"""
    )

    op.execute(
        r"""ALTER TABLE preparations DROP CONSTRAINT fk_execution_runs_execution_run_id_project"""
    )

    op.execute(r"""ALTER TABLE pipelines DROP CONSTRAINT fk_pipelines_project_id_projects""")

    op.execute(
        r"""ALTER TABLE pipeline_versions DROP CONSTRAINT fk_validation_reports_validation_report_id_project"""
    )

    op.execute(
        r"""ALTER TABLE pipeline_versions DROP CONSTRAINT fk_pipelines_pipeline_id_project"""
    )

    op.execute(
        r"""ALTER TABLE pipeline_versions DROP CONSTRAINT fk_pipeline_versions_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE pipeline_artifacts DROP CONSTRAINT fk_pipeline_versions_version_id_project"""
    )

    op.execute(
        r"""ALTER TABLE pipeline_artifacts DROP CONSTRAINT fk_pipeline_artifacts_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE outbox_messages DROP CONSTRAINT fk_outbox_messages_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE outbox_messages DROP CONSTRAINT fk_managed_commands_command_id_project"""
    )

    op.execute(
        r"""ALTER TABLE metadata_profiles DROP CONSTRAINT fk_metadata_profiles_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE metadata_profiles DROP CONSTRAINT fk_file_assets_file_asset_id_project"""
    )

    op.execute(
        r"""ALTER TABLE metadata_profiles DROP CONSTRAINT fk_connections_connection_id_project"""
    )

    op.execute(
        r"""ALTER TABLE managed_commands DROP CONSTRAINT fk_preparations_preparation_id_project"""
    )

    op.execute(
        r"""ALTER TABLE managed_commands DROP CONSTRAINT fk_managed_commands_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE managed_commands DROP CONSTRAINT fk_execution_runs_execution_run_id_project"""
    )

    op.execute(
        r"""ALTER TABLE managed_commands DROP CONSTRAINT fk_capability_authorizations_capability_id_project"""
    )

    op.execute(
        r"""ALTER TABLE local_credentials DROP CONSTRAINT fk_local_credentials_user_id_users"""
    )

    op.execute(
        r"""ALTER TABLE ledger_checkpoints DROP CONSTRAINT fk_ledger_checkpoints_project_id_projects"""
    )

    op.execute(r"""ALTER TABLE file_assets DROP CONSTRAINT fk_file_assets_project_id_projects""")

    op.execute(
        r"""ALTER TABLE execution_runs DROP CONSTRAINT fk_preparations_preparation_id_project"""
    )

    op.execute(
        r"""ALTER TABLE execution_runs DROP CONSTRAINT fk_pipeline_versions_version_id_project"""
    )

    op.execute(
        r"""ALTER TABLE execution_runs DROP CONSTRAINT fk_execution_runs_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE execution_runs DROP CONSTRAINT fk_execution_runs_original_run_id_project"""
    )

    op.execute(
        r"""ALTER TABLE evolution_candidates DROP CONSTRAINT fk_evolution_candidates_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE evolution_authorizations DROP CONSTRAINT fk_evolution_candidates_candidate_id_project"""
    )

    op.execute(
        r"""ALTER TABLE evolution_authorizations DROP CONSTRAINT fk_evolution_authorizations_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE evolution_authorizations DROP CONSTRAINT fk_evolution_authorizations_approved_by_users"""
    )

    op.execute(
        r"""ALTER TABLE diagnostic_reports DROP CONSTRAINT fk_execution_runs_execution_run_id_project"""
    )

    op.execute(
        r"""ALTER TABLE diagnostic_reports DROP CONSTRAINT fk_diagnostic_reports_project_id_projects"""
    )

    op.execute(r"""ALTER TABLE conversations DROP CONSTRAINT fk_pipelines_pipeline_id_project""")

    op.execute(
        r"""ALTER TABLE conversations DROP CONSTRAINT fk_conversations_project_id_projects"""
    )

    op.execute(r"""ALTER TABLE conversations DROP CONSTRAINT fk_conversations_creator_id_users""")

    op.execute(
        r"""ALTER TABLE conversation_messages DROP CONSTRAINT fk_conversations_conversation_id_project"""
    )

    op.execute(
        r"""ALTER TABLE conversation_messages DROP CONSTRAINT fk_conversation_messages_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE connections DROP CONSTRAINT fk_secret_references_secret_ref_id_project"""
    )

    op.execute(r"""ALTER TABLE connections DROP CONSTRAINT fk_connections_project_id_projects""")

    op.execute(
        r"""ALTER TABLE connection_tests DROP CONSTRAINT fk_connections_connection_id_project"""
    )

    op.execute(
        r"""ALTER TABLE connection_tests DROP CONSTRAINT fk_connection_tests_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE clarifications DROP CONSTRAINT fk_clarifications_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE clarifications DROP CONSTRAINT fk_clarifications_answered_by_users"""
    )

    op.execute(r"""ALTER TABLE clarifications DROP CONSTRAINT fk_agent_runs_agent_run_id_project""")

    op.execute(
        r"""ALTER TABLE capability_consumptions DROP CONSTRAINT fk_managed_commands_command_id_project"""
    )

    op.execute(
        r"""ALTER TABLE capability_consumptions DROP CONSTRAINT fk_execution_runs_execution_run_id_project"""
    )

    op.execute(
        r"""ALTER TABLE capability_consumptions DROP CONSTRAINT fk_capability_consumptions_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE capability_consumptions DROP CONSTRAINT fk_capability_authorizations_capability_id_project"""
    )

    op.execute(
        r"""ALTER TABLE capability_authorizations DROP CONSTRAINT fk_preparations_preparation_id_project"""
    )

    op.execute(
        r"""ALTER TABLE capability_authorizations DROP CONSTRAINT fk_managed_commands_parent_command_id_project"""
    )

    op.execute(
        r"""ALTER TABLE capability_authorizations DROP CONSTRAINT fk_managed_commands_command_id_project"""
    )

    op.execute(
        r"""ALTER TABLE capability_authorizations DROP CONSTRAINT fk_capability_authorizations_subject_id_users"""
    )

    op.execute(
        r"""ALTER TABLE capability_authorizations DROP CONSTRAINT fk_capability_authorizations_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE benchmark_runs DROP CONSTRAINT fk_preparations_preparation_id_project"""
    )

    op.execute(
        r"""ALTER TABLE benchmark_runs DROP CONSTRAINT fk_benchmark_runs_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE benchmark_runs DROP CONSTRAINT fk_benchmark_datasets_dataset_id_project"""
    )

    op.execute(
        r"""ALTER TABLE benchmark_datasets DROP CONSTRAINT fk_benchmark_datasets_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE benchmark_cases DROP CONSTRAINT fk_benchmark_datasets_dataset_id_project"""
    )

    op.execute(
        r"""ALTER TABLE benchmark_cases DROP CONSTRAINT fk_benchmark_cases_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE benchmark_case_results DROP CONSTRAINT fk_execution_runs_execution_run_id_project"""
    )

    op.execute(
        r"""ALTER TABLE benchmark_case_results DROP CONSTRAINT fk_benchmark_runs_benchmark_run_id_project"""
    )

    op.execute(
        r"""ALTER TABLE benchmark_case_results DROP CONSTRAINT fk_benchmark_cases_case_id_project"""
    )

    op.execute(
        r"""ALTER TABLE benchmark_case_results DROP CONSTRAINT fk_benchmark_case_results_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE benchmark_case_executions DROP CONSTRAINT fk_execution_runs_execution_run_id_project"""
    )

    op.execute(
        r"""ALTER TABLE benchmark_case_executions DROP CONSTRAINT fk_benchmark_runs_benchmark_run_id_project"""
    )

    op.execute(
        r"""ALTER TABLE benchmark_case_executions DROP CONSTRAINT fk_benchmark_cases_case_id_project"""
    )

    op.execute(r"""ALTER TABLE auth_sessions DROP CONSTRAINT fk_auth_sessions_user_id_users""")

    op.execute(r"""ALTER TABLE audit_events DROP CONSTRAINT fk_audit_events_project_id_projects""")

    op.execute(r"""ALTER TABLE audit_events DROP CONSTRAINT fk_audit_events_actor_id_users""")

    op.execute(
        r"""ALTER TABLE approval_requests DROP CONSTRAINT fk_preparations_preparation_id_project"""
    )

    op.execute(
        r"""ALTER TABLE approval_requests DROP CONSTRAINT fk_pipeline_versions_version_id_project"""
    )

    op.execute(
        r"""ALTER TABLE approval_requests DROP CONSTRAINT fk_approval_requests_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE approval_requests DROP CONSTRAINT fk_approval_requests_approver_id_users"""
    )

    op.execute(
        r"""ALTER TABLE api_idempotency_records DROP CONSTRAINT fk_api_idempotency_records_subject_id_users"""
    )

    op.execute(
        r"""ALTER TABLE api_idempotency_records DROP CONSTRAINT fk_api_idempotency_records_project_id_projects"""
    )

    op.execute(
        r"""ALTER TABLE agent_runs DROP CONSTRAINT fk_pipeline_versions_version_id_project"""
    )

    op.execute(
        r"""ALTER TABLE agent_runs DROP CONSTRAINT fk_conversations_conversation_id_project"""
    )

    op.execute(r"""ALTER TABLE agent_runs DROP CONSTRAINT fk_agent_runs_project_id_projects""")

    op.execute(r"""DROP TABLE schedule_trigger_records""")

    op.execute(r"""DROP TABLE validation_reports""")

    op.execute(r"""DROP TABLE target_heads""")

    op.execute(r"""DROP TABLE secret_references""")

    op.execute(r"""DROP TABLE schedule_definitions""")

    op.execute(r"""DROP TABLE runtime_supervision_snapshots""")

    op.execute(r"""DROP TABLE run_operations""")

    op.execute(r"""DROP TABLE quality_violations""")

    op.execute(r"""DROP TABLE quality_reports""")

    op.execute(r"""DROP TABLE publication_records""")

    op.execute(r"""DROP TABLE project_role_grants""")

    op.execute(r"""DROP TABLE project_memberships""")

    op.execute(r"""DROP TABLE profile_tasks""")

    op.execute(r"""DROP TABLE preparations""")

    op.execute(r"""DROP TABLE pipelines""")

    op.execute(r"""DROP TABLE pipeline_versions""")

    op.execute(r"""DROP TABLE pipeline_artifacts""")

    op.execute(r"""DROP TABLE outbox_messages""")

    op.execute(r"""DROP TABLE metadata_profiles""")

    op.execute(r"""DROP TABLE managed_commands""")

    op.execute(r"""DROP TABLE local_credentials""")

    op.execute(r"""DROP TABLE ledger_checkpoints""")

    op.execute(r"""DROP TABLE file_assets""")

    op.execute(r"""DROP TABLE execution_runs""")

    op.execute(r"""DROP TABLE evolution_candidates""")

    op.execute(r"""DROP TABLE evolution_authorizations""")

    op.execute(r"""DROP TABLE diagnostic_reports""")

    op.execute(r"""DROP TABLE conversations""")

    op.execute(r"""DROP TABLE conversation_messages""")

    op.execute(r"""DROP TABLE connections""")

    op.execute(r"""DROP TABLE connection_tests""")

    op.execute(r"""DROP TABLE clarifications""")

    op.execute(r"""DROP TABLE capability_consumptions""")

    op.execute(r"""DROP TABLE capability_authorizations""")

    op.execute(r"""DROP TABLE benchmark_runs""")

    op.execute(r"""DROP TABLE benchmark_datasets""")

    op.execute(r"""DROP TABLE benchmark_cases""")

    op.execute(r"""DROP TABLE benchmark_case_results""")

    op.execute(r"""DROP TABLE auth_sessions""")

    op.execute(r"""DROP TABLE audit_events""")

    op.execute(r"""DROP TABLE approval_requests""")

    op.execute(r"""DROP TABLE api_idempotency_records""")

    op.execute(r"""DROP TABLE agent_runs""")

    op.execute(r"""DROP TABLE users""")

    op.execute(r"""DROP TABLE projects""")

    op.execute(r"""DROP TABLE profile_task_results""")

    op.execute(r"""DROP TABLE login_throttles""")

    op.execute(r"""DROP TABLE benchmark_case_executions""")
