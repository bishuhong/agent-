"""Approved control-plane endpoints and explicit G-contract additions."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from sqlalchemy import select

from etl_agent.api.dependencies import (
    ActorDep,
    IdentityDep,
    SessionDep,
    connection_list_query,
    cursor_query,
    envelope,
    get_runtime,
    paginate,
    request_context,
)
from etl_agent.api.schemas import contracts as c
from etl_agent.infrastructure.persistence import models as m
from etl_agent.infrastructure.persistence.access import (
    compare_revision,
    project_entity,
    public_record,
)
from etl_agent.modules.identity_access.domain import assert_action
from etl_agent.shared.errors import ApplicationError, invalid, unavailable
from etl_agent.shared.security import entity_etag, match_etag

router = APIRouter()
Cursor = Annotated[c.CursorQuery, Depends(cursor_query)]


@router.post("/api/v1/auth/login", response_model=c.DataEnvelope[c.LoginResponse])
def login(payload: c.LocalLoginRequest, request: Request, response: Response) -> dict:
    rt = get_runtime(request)
    if rt.auth is None:
        raise unavailable("身份数据库")
    raw, result = rt.auth.login(
        payload.username,
        payload.password.get_secret_value(),
        request.client.host if request.client else "unknown",
    )
    response.set_cookie(
        rt.settings.cookie_name,
        raw,
        max_age=28800,
        path="/",
        secure=rt.settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return envelope(result, request, etag=False)


@router.get("/api/v1/auth/me", response_model=c.DataEnvelope[c.CurrentIdentityResponse])
def me(request: Request, session: SessionDep, identity: IdentityDep) -> dict:
    return envelope(
        get_runtime(request).required_auth.current(session, identity), request, etag=False
    )


@router.post("/api/v1/auth/logout", response_model=c.DataEnvelope[c.LogoutResponse])
def logout(
    request: Request, response: Response, session: SessionDep, identity: IdentityDep
) -> dict:
    rt = get_runtime(request)
    result = rt.required_auth.logout(session, identity)
    response.delete_cookie(
        rt.settings.cookie_name,
        path="/",
        secure=rt.settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return envelope(result, request, etag=False)


@router.get("/health", response_model=c.DataEnvelope[c.HealthResult])
def health(request: Request, response: Response) -> dict:
    result = get_runtime(request).health_result(request)
    response.status_code = 200 if result["status"] == "ready" else 503
    return envelope(result, request, etag=False)


@router.get("/api/v1/projects", response_model=c.CursorPage[c.ProjectResponse])
def projects(request: Request, session: SessionDep, actor: ActorDep, query: Cursor) -> dict:
    rt = get_runtime(request)
    result = paginate(
        session,
        actor,
        None,
        m.Project,
        c.ProjectResponse,
        query,
        rt,
        request,
        statement=select(m.Project).where(m.Project.id.in_(list(actor.project_roles))),
    )
    for item in result["items"]:
        item["available_actions"] = rt.projects.actions(actor, UUID(item["id"]))
    return result


@router.get("/api/v1/projects/{project_id}", response_model=c.DataEnvelope[c.ProjectResponse])
def project(
    project_id: UUID, request: Request, response: Response, session: SessionDep, actor: ActorDep
) -> dict:
    assert_action(actor, project_id, "view")
    row = session.get(m.Project, project_id)
    if row is None:
        raise ApplicationError("RESOURCE_NOT_FOUND", "项目不存在", 404)
    return envelope(
        public_record(
            row,
            c.ProjectResponse,
            available_actions=get_runtime(request).projects.actions(actor, project_id),
        ),
        request,
        response,
    )


@router.post("/api/v1/projects/{project_id}/memberships", status_code=201)
def grant(
    project_id: UUID,
    payload: c.MemberGrantRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    actor: ActorDep,
) -> dict:
    row = get_runtime(request).projects.grant(
        session,
        actor,
        project_id,
        payload.user_id,
        payload.role.value if payload.role else None,
        payload.role_slot.value if payload.role_slot else None,
        payload.expected_revision,
    )
    return envelope(
        public_record(
            row,
            c.ProjectMembershipResponse
            if isinstance(row, m.ProjectMembership)
            else c.ProjectRoleGrantResponse,
        ),
        request,
        response,
    )


@router.get(
    "/api/v1/projects/{project_id}/connections", response_model=c.CursorPage[c.ConnectionResponse]
)
def connections(
    project_id: UUID,
    request: Request,
    session: SessionDep,
    actor: ActorDep,
    query: Annotated[c.ConnectionListQuery, Depends(connection_list_query)],
) -> dict:
    statement = select(m.Connection)
    if query.connector_type:
        statement = statement.where(m.Connection.connector_type == query.connector_type.value)
    return paginate(
        session,
        actor,
        project_id,
        m.Connection,
        c.ConnectionResponse,
        query,
        get_runtime(request),
        request,
        statement=statement,
        filters={"connector_type": query.connector_type},
    )


@router.post(
    "/api/v1/connections", status_code=201, response_model=c.DataEnvelope[c.ConnectionResponse]
)
def create_connection(
    payload: c.ConnectionCreateRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    actor: ActorDep,
) -> dict:
    return envelope(
        get_runtime(request).assets.create_connection(session, actor, payload), request, response
    )


@router.get("/api/v1/connections/{id}", response_model=c.DataEnvelope[c.ConnectionResponse])
def connection(
    id: UUID, request: Request, response: Response, session: SessionDep, actor: ActorDep
) -> dict:
    return envelope(
        get_runtime(request).assets.get_connection(session, actor, id), request, response
    )


@router.put("/api/v1/connections/{id}", response_model=c.DataEnvelope[c.ConnectionResponse])
def replace_connection(
    id: UUID,
    payload: c.ConnectionReplaceRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    actor: ActorDep,
) -> dict:
    service = get_runtime(request).assets
    before = service.get_connection(session, actor, id)
    match_etag(request.headers.get("If-Match"), entity_etag(before))
    return envelope(
        service.replace_connection(session, actor, id, payload, before["revision"]),
        request,
        response,
    )


@router.post(
    "/api/v1/connections/{id}/tests",
    status_code=202,
    response_model=c.DataEnvelope[c.ConnectionTestResponse],
)
def test_connection(id: UUID, request: Request, session: SessionDep, actor: ActorDep) -> dict:
    return envelope(get_runtime(request).assets.start_test(session, actor, id), request)


@router.post(
    "/api/v1/connections/{id}/profiles",
    status_code=202,
    response_model=c.DataEnvelope[c.ProfileTaskAccepted],
)
def probe(
    id: UUID,
    payload: c.ProfileCreateRequest,
    request: Request,
    session: SessionDep,
    actor: ActorDep,
) -> dict:
    return envelope(get_runtime(request).assets.start_profile(session, actor, id, payload), request)


@router.post(
    "/api/v1/secret-references/{id}/rotate",
    response_model=c.DataEnvelope[c.SecretReferenceResponse],
)
def rotate(
    id: UUID,
    payload: c.RotateCredentialRequest,
    request: Request,
    session: SessionDep,
    actor: ActorDep,
) -> dict:
    if id != payload.secret_ref_id:
        raise invalid("Secret引用不一致")
    return envelope(get_runtime(request).assets.rotate_secret(session, actor, payload), request)


@router.post(
    "/api/v1/file-assets", status_code=201, response_model=c.DataEnvelope[c.FileAssetResponse]
)
def upload(
    request: Request,
    session: SessionDep,
    actor: ActorDep,
    project_id: Annotated[UUID, Form()],
    file: Annotated[UploadFile, File()],
) -> dict:
    rt = get_runtime(request)
    data = file.file.read(rt.settings.max_upload_bytes + 1)
    if len(data) > rt.settings.max_upload_bytes:
        raise ApplicationError("VALIDATION_FAILED", "文件超过上传上限", 413)
    return envelope(
        rt.assets.upload(
            session,
            actor,
            c.FileAssetUploadRequest(project_id=project_id, file=data),
            file.filename or "asset",
        ),
        request,
    )


@router.post(
    "/api/v1/pipelines", status_code=201, response_model=c.DataEnvelope[c.PipelineCreateResponse]
)
def create_pipeline(
    payload: c.PipelineCreateRequest, request: Request, session: SessionDep, actor: ActorDep
) -> dict:
    return envelope(get_runtime(request).pipelines.create(session, actor, payload), request)


@router.post(
    "/api/v1/pipelines/{id}/versions",
    status_code=201,
    response_model=c.DataEnvelope[c.PipelineVersionResponse],
)
def candidate(
    id: UUID,
    request: Request,
    session: SessionDep,
    actor: ActorDep,
    base_version_id: UUID | None = None,
) -> dict:
    return envelope(
        get_runtime(request).pipelines.new_candidate(session, actor, id, base_version_id), request
    )


@router.post(
    "/api/v1/versions/{version_id}/generation",
    status_code=202,
    response_model=c.DataEnvelope[c.AgentRunResponse],
)
def generate(
    version_id: UUID,
    payload: c.GenerationRequest,
    request: Request,
    session: SessionDep,
    actor: ActorDep,
) -> dict:
    rt = get_runtime(request)
    before = rt.pipelines.design(session, actor, version_id)
    match_etag(request.headers.get("If-Match"), entity_etag(before))
    return envelope(
        rt.generation.start(session, actor, version_id, payload, before["version"]["revision"]),
        request,
    )


@router.post(
    "/api/v1/agent-runs/{run_id}/answers",
    status_code=202,
    response_model=c.DataEnvelope[c.AgentRunResponse],
)
def answer(
    run_id: UUID,
    payload: c.ClarificationAnswerRequest,
    request: Request,
    session: SessionDep,
    actor: ActorDep,
) -> dict:
    return envelope(
        get_runtime(request).generation.answer(session, actor, run_id, payload), request
    )


@router.get("/api/v1/versions/{version_id}/design", response_model=c.DataEnvelope[c.DesignResponse])
def design(
    version_id: UUID, request: Request, response: Response, session: SessionDep, actor: ActorDep
) -> dict:
    return envelope(
        get_runtime(request).pipelines.design(session, actor, version_id), request, response
    )


@router.post(
    "/api/v1/versions/{version_id}/freeze", response_model=c.DataEnvelope[c.PipelineVersionResponse]
)
def freeze(
    version_id: UUID,
    payload: c.FreezeVersionRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    actor: ActorDep,
) -> dict:
    if payload.version_id != version_id:
        raise invalid("版本引用不一致")
    return envelope(
        get_runtime(request).pipelines.freeze(session, actor, payload), request, response
    )


@router.get(
    "/api/v1/versions/{version_id}/diff", response_model=c.DataEnvelope[c.VersionDiffResponse]
)
def diff(
    version_id: UUID, base_version_id: UUID, request: Request, session: SessionDep, actor: ActorDep
) -> dict:
    return envelope(
        get_runtime(request).pipelines.diff(session, actor, base_version_id, version_id), request
    )


@router.post(
    "/api/v1/versions/{version_id}/trials",
    response_model=c.DataEnvelope[c.ValidationReportResponse],
)
def trial(
    version_id: UUID,
    payload: c.TrialRequest,
    request: Request,
    session: SessionDep,
    actor: ActorDep,
) -> dict:
    if payload.version_id != version_id or payload.mode != "static":
        raise invalid("真实隔离试运行必须经Prepare、审批与Commit")
    return envelope(
        get_runtime(request).pipelines.validate_static(session, actor, version_id), request
    )


@router.post(
    "/api/v1/versions/{version_id}/prepare",
    status_code=201,
    response_model=c.DataEnvelope[c.PreparationResponse],
)
def prepare(
    version_id: UUID,
    payload: c.PrepareRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    actor: ActorDep,
) -> dict:
    return envelope(
        get_runtime(request).governance.prepare(session, actor, version_id, payload),
        request,
        response,
    )


@router.get("/api/v1/preparations/{id}", response_model=c.DataEnvelope[c.PreparationResponse])
def preparation(
    id: UUID, request: Request, response: Response, session: SessionDep, actor: ActorDep
) -> dict:
    return envelope(get_runtime(request).governance.get(session, actor, id), request, response)


@router.post(
    "/api/v1/approval-requests/{approval_id}/decisions",
    response_model=c.DataEnvelope[c.ApprovalResponse],
)
def decide(
    approval_id: UUID,
    payload: c.ApprovalDecisionRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    actor: ActorDep,
) -> dict:
    before = public_record(
        project_entity(session, m.ApprovalRequest, approval_id, actor), c.ApprovalResponse
    )
    match_etag(request.headers.get("If-Match"), entity_etag(before))
    return envelope(
        get_runtime(request).governance.decide(
            session, actor, approval_id, payload, before["revision"]
        ),
        request,
        response,
    )


@router.post(
    "/api/v1/preparations/{preparation_id}/commit",
    status_code=202,
    response_model=c.DataEnvelope[c.CommitAcceptedResponse],
)
def commit(
    preparation_id: UUID,
    payload: c.CommitRequest,
    request: Request,
    session: SessionDep,
    actor: ActorDep,
) -> dict:
    return envelope(
        get_runtime(request).commits.commit(
            session, actor, preparation_id, payload, request_context(request)
        ),
        request,
    )


@router.get("/api/v1/execution-runs/{id}", response_model=c.DataEnvelope[c.ExecutionRunResponse])
def execution(
    id: UUID, request: Request, response: Response, session: SessionDep, actor: ActorDep
) -> dict:
    return envelope(get_runtime(request).execution.get(session, actor, id), request, response)


@router.post(
    "/api/v1/execution-runs/{id}/cancel",
    status_code=202,
    response_model=c.DataEnvelope[c.RunOperationResponse],
)
def cancel(
    id: UUID, payload: c.RunActionRequest, request: Request, session: SessionDep, actor: ActorDep
) -> dict:
    return envelope(
        public_record(
            get_runtime(request).commits.operation(
                session, actor, id, "cancel", payload.expected_revision, request_context(request)
            ),
            c.RunOperationResponse,
        ),
        request,
    )


@router.post(
    "/api/v1/execution-runs/{id}/rollback",
    status_code=202,
    response_model=c.DataEnvelope[c.RunOperationResponse],
)
def rollback(
    id: UUID, payload: c.RollbackRequest, request: Request, session: SessionDep, actor: ActorDep
) -> dict:
    row = get_runtime(request).commits.operation(
        session,
        actor,
        id,
        "rollback",
        payload.expected_revision,
        request_context(request),
        preparation_id=payload.preparation_id,
        publication_id=payload.publication_id,
    )
    return envelope(public_record(row, c.RunOperationResponse), request)


def _operation(kind: str):
    def endpoint(
        id: UUID,
        payload: c.RunActionRequest,
        request: Request,
        session: SessionDep,
        actor: ActorDep,
    ) -> dict:
        row = get_runtime(request).commits.operation(
            session, actor, id, kind, payload.expected_revision, request_context(request)
        )
        return envelope(public_record(row, c.RunOperationResponse), request)

    endpoint.__name__ = kind + "_run"
    return endpoint


for _kind in ("pause", "resume"):
    router.add_api_route(
        "/api/v1/execution-runs/{id}/" + _kind,
        _operation(_kind),
        methods=["POST"],
        status_code=202,
        response_model=c.DataEnvelope[c.RunOperationResponse],
    )


@router.post(
    "/api/v1/execution-runs/{id}/rerun",
    status_code=202,
    response_model=c.DataEnvelope[c.CommitAcceptedResponse],
)
def rerun(
    id: UUID, payload: c.RerunRequest, request: Request, session: SessionDep, actor: ActorDep
) -> dict:
    run = project_entity(session, m.ExecutionRun, id, actor, "rerun")
    if id != payload.original_run_id:
        raise invalid("原运行引用不一致")
    compare_revision(run.revision, payload.expected_revision)
    prep = project_entity(session, m.Preparation, payload.preparation_id, actor)
    return envelope(
        get_runtime(request).commits.commit(
            session,
            actor,
            prep.id,
            c.CommitRequest(expected_input_fingerprint=prep.input_fingerprint),
            request_context(request),
            original_run_id=id,
        ),
        request,
    )


def _get(model: Any, schema: Any):
    def endpoint(
        id: UUID, request: Request, response: Response, session: SessionDep, actor: ActorDep
    ) -> dict:
        return envelope(
            public_record(project_entity(session, model, id, actor), schema), request, response
        )

    endpoint.__name__ = "get_" + model.__tablename__
    return endpoint


READS = [
    ("secret-references", m.SecretReference, c.SecretReferenceResponse),
    ("pipelines", m.Pipeline, c.PipelineResponse),
    ("connection-tests", m.ConnectionTestResult, c.ConnectionTestResponse),
    ("metadata-profiles", m.MetadataProfile, c.MetadataProfileResponse),
    ("file-assets", m.FileAsset, c.FileAssetResponse),
    ("agent-runs", m.AgentRun, c.AgentRunResponse),
    ("clarifications", m.Clarification, c.ClarificationResponse),
    ("validation-reports", m.ValidationReport, c.ValidationReportResponse),
    ("approval-requests", m.ApprovalRequest, c.ApprovalResponse),
    ("run-operations", m.RunOperation, c.RunOperationResponse),
    ("publications", m.PublicationRecord, c.PublicationResponse),
    ("schedules", m.ScheduleDefinition, c.ScheduleDefinitionResponse),
    ("audit-events", m.AuditEvent, c.AuditEventResponse),
    ("benchmark-datasets", m.BenchmarkDataset, c.BenchmarkDatasetResponse),
    ("benchmark-cases", m.BenchmarkCase, c.BenchmarkCaseResponse),
    ("benchmark-runs", m.BenchmarkRun, c.BenchmarkRunResponse),
    ("evolution-candidates", m.EvolutionCandidate, c.EvolutionCandidateResponse),
    ("evolution-authorizations", m.EvolutionAuthorization, c.EvolutionAuthorizationResponse),
]
for _path, _model, _schema in READS:
    router.add_api_route(
        "/api/v1/" + _path + "/{id}",
        _get(_model, _schema),
        methods=["GET"],
        response_model=c.DataEnvelope.__class_getitem__(_schema),
    )


def _list(model: Any, schema: Any):
    def endpoint(
        project_id: UUID, request: Request, session: SessionDep, actor: ActorDep, query: Cursor
    ) -> dict:
        return paginate(
            session, actor, project_id, model, schema, query, get_runtime(request), request
        )

    endpoint.__name__ = "list_" + model.__tablename__
    return endpoint


LISTS = [
    ("memberships", m.ProjectMembership, c.ProjectMembershipResponse),
    ("file-assets", m.FileAsset, c.FileAssetResponse),
    ("metadata-profiles", m.MetadataProfile, c.MetadataProfileResponse),
    ("pipelines", m.Pipeline, c.PipelineResponse),
    ("audit-events", m.AuditEvent, c.AuditEventResponse),
    ("schedules", m.ScheduleDefinition, c.ScheduleDefinitionResponse),
    ("benchmark-datasets", m.BenchmarkDataset, c.BenchmarkDatasetResponse),
    ("benchmark-runs", m.BenchmarkRun, c.BenchmarkRunResponse),
    ("evolution-candidates", m.EvolutionCandidate, c.EvolutionCandidateResponse),
    ("evolution-authorizations", m.EvolutionAuthorization, c.EvolutionAuthorizationResponse),
]
for _path, _model, _schema in LISTS:
    router.add_api_route(
        "/api/v1/projects/{project_id}/" + _path,
        _list(_model, _schema),
        methods=["GET"],
        response_model=c.CursorPage.__class_getitem__(_schema),
    )


@router.post(
    "/api/v1/benchmarks/run", status_code=202, response_model=c.DataEnvelope[c.BenchmarkRunResponse]
)
def benchmark(
    payload: c.BenchmarkRunRequest, request: Request, session: SessionDep, actor: ActorDep
) -> dict:
    return envelope(
        get_runtime(request).benchmarks.start(session, actor, payload, request_context(request)),
        request,
    )


@router.get("/api/v1/benchmark-runs/{id}/report")
def benchmark_report(id: UUID, request: Request, session: SessionDep, actor: ActorDep) -> dict:
    return envelope(get_runtime(request).benchmarks.report(session, actor, id), request)


@router.get(
    "/api/v1/projects/{project_id}/overview",
    response_model=c.DataEnvelope[c.ProjectOverviewResponse],
)
def overview(project_id: UUID, request: Request, session: SessionDep, actor: ActorDep) -> dict:
    return envelope(get_runtime(request).reporting.overview(session, actor, project_id), request)


@router.post(
    "/api/v1/schedules",
    status_code=201,
    response_model=c.DataEnvelope[c.ScheduleDefinitionResponse],
)
def create_schedule(
    payload: c.ScheduleDefinitionRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    actor: ActorDep,
) -> dict:
    return envelope(get_runtime(request).schedules.save(session, actor, payload), request, response)


@router.put("/api/v1/schedules/{id}", response_model=c.DataEnvelope[c.ScheduleDefinitionResponse])
def replace_schedule(
    id: UUID,
    payload: c.ScheduleDefinitionRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    actor: ActorDep,
) -> dict:
    rt = get_runtime(request)
    before = rt.schedules.get(session, actor, id)
    match_etag(request.headers.get("If-Match"), entity_etag(before))
    return envelope(
        rt.schedules.save(
            session, actor, payload, schedule_id=id, expected_revision=before["revision"]
        ),
        request,
        response,
    )


@router.get("/api/v1/profile-tasks/{id}", response_model=c.DataEnvelope[c.ProfileTaskResponse])
def profile_task(id: UUID, request: Request, session: SessionDep, actor: ActorDep) -> dict:
    return envelope(get_runtime(request).assets.get_task(session, actor, id), request)


@router.get(
    "/api/v1/projects/{project_id}/ledger/verification",
    response_model=c.DataEnvelope[c.LedgerVerificationResponse],
)
def verify_ledger(project_id: UUID, request: Request, session: SessionDep, actor: ActorDep) -> dict:
    assert_action(actor, project_id, "view")
    rt = get_runtime(request)
    return envelope(
        rt.ledger.verify(session, project_id, rt.signer, rt.settings.checkpoint_anchor_root),
        request,
    )


@router.post(
    "/api/v1/evolution-authorizations",
    status_code=201,
    response_model=c.DataEnvelope[c.EvolutionAuthorizationResponse],
)
def authorize_evolution(
    payload: c.EvolutionAuthorizationRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    actor: ActorDep,
) -> dict:
    return envelope(
        get_runtime(request).benchmarks.authorize_evolution(
            session, actor, payload, request_context(request)
        ),
        request,
        response,
    )


@router.post(
    "/api/v1/evolution-authorizations/{id}/revoke",
    response_model=c.DataEnvelope[c.EvolutionAuthorizationResponse],
)
def revoke_evolution(
    id: UUID, request: Request, response: Response, session: SessionDep, actor: ActorDep
) -> dict:
    before = public_record(
        project_entity(session, m.EvolutionAuthorization, id, actor),
        c.EvolutionAuthorizationResponse,
    )
    match_etag(request.headers.get("If-Match"), entity_etag(before))
    return envelope(
        get_runtime(request).benchmarks.revoke_evolution(session, actor, id, before["revision"]),
        request,
        response,
    )


@router.get("/api/v1/execution-runs/{id}/logs", response_model=c.CursorPage[c.LogEntryResponse])
def logs(id: UUID, request: Request, session: SessionDep, actor: ActorDep, query: Cursor) -> dict:
    return get_runtime(request).execution.logs(session, actor, id, query, request)


@router.get(
    "/api/v1/execution-runs/{id}/quality-report",
    response_model=c.DataEnvelope[c.QualityReportResponse | None],
)
def quality(id: UUID, request: Request, session: SessionDep, actor: ActorDep) -> dict:
    return envelope(get_runtime(request).quality.get(session, actor, id), request)


@router.get(
    "/api/v1/execution-runs/{id}/diagnostic-report",
    response_model=c.DataEnvelope[c.DiagnosticReportResponse | None],
)
def diagnostic(id: UUID, request: Request, session: SessionDep, actor: ActorDep) -> dict:
    return envelope(get_runtime(request).execution.diagnose(session, actor, id), request)


def _children(parent: Any, model: Any, foreign_key: Any, schema: Any):
    def endpoint(
        id: UUID, request: Request, session: SessionDep, actor: ActorDep, query: Cursor
    ) -> dict:
        owner = project_entity(session, parent, id, actor)
        return paginate(
            session,
            actor,
            owner.project_id,
            model,
            schema,
            query,
            get_runtime(request),
            request,
            statement=select(model).where(foreign_key == id),
            filters={"parent_id": str(id)},
            sort_field="version_number" if model is m.PipelineVersion else "created_at",
        )

    endpoint.__name__ = "list_" + parent.__tablename__ + "_" + model.__tablename__
    return endpoint


CHILDREN = [
    (
        "pipelines",
        "versions",
        m.Pipeline,
        m.PipelineVersion,
        m.PipelineVersion.pipeline_id,
        c.PipelineVersionResponse,
    ),
    (
        "agent-runs",
        "clarifications",
        m.AgentRun,
        m.Clarification,
        m.Clarification.agent_run_id,
        c.ClarificationResponse,
    ),
    (
        "benchmark-datasets",
        "cases",
        m.BenchmarkDataset,
        m.BenchmarkCase,
        m.BenchmarkCase.dataset_id,
        c.BenchmarkCaseResponse,
    ),
    (
        "benchmark-runs",
        "case-results",
        m.BenchmarkRun,
        m.BenchmarkCaseResult,
        m.BenchmarkCaseResult.benchmark_run_id,
        c.CaseResultResponse,
    ),
    (
        "execution-runs",
        "operations",
        m.ExecutionRun,
        m.RunOperation,
        m.RunOperation.execution_run_id,
        c.RunOperationResponse,
    ),
    (
        "execution-runs",
        "quality-violations",
        m.ExecutionRun,
        m.QualityViolation,
        m.QualityViolation.execution_run_id,
        c.QualityViolationResponse,
    ),
]
for _parent_path, _child_path, _parent, _model, _fk, _schema in CHILDREN:
    router.add_api_route(
        "/api/v1/" + _parent_path + "/{id}/" + _child_path,
        _children(_parent, _model, _fk, _schema),
        methods=["GET"],
        response_model=c.CursorPage.__class_getitem__(_schema),
    )


@router.get(
    "/api/v1/projects/{project_id}/preparations", response_model=c.CursorPage[c.PreparationSummary]
)
def preparations(
    project_id: UUID, request: Request, session: SessionDep, actor: ActorDep, query: Cursor
) -> dict:
    rt = get_runtime(request)

    def project_row(row):
        detail = rt.governance.get(session, actor, row.id)
        validity = detail["validity"]
        approvals = detail["approvals"]
        submitted = session.scalar(
            select(m.ExecutionRun.id).where(m.ExecutionRun.preparation_id == row.id).limit(1)
        )
        status = (
            validity
            if validity in {"expired", "stale"}
            else "rejected"
            if any(item["status"] == "rejected" for item in approvals)
            else "submitted"
            if submitted
            else "approved"
            if approvals and all(item["status"] == "approved" for item in approvals)
            else "pending_approval"
        )
        return public_record(row, c.PreparationSummary, validity=validity, approval_status=status)

    return paginate(
        session,
        actor,
        project_id,
        m.Preparation,
        c.PreparationSummary,
        query,
        rt,
        request,
        projector=project_row,
    )


@router.get(
    "/api/v1/projects/{project_id}/execution-runs",
    response_model=c.CursorPage[c.ExecutionRunSummary],
)
def executions(
    project_id: UUID, request: Request, session: SessionDep, actor: ActorDep, query: Cursor
) -> dict:
    rt = get_runtime(request)

    def project_row(row):
        detail = rt.execution.get(session, actor, row.id)
        return public_record(
            row,
            c.ExecutionRunSummary,
            publication_status=detail["publication"]["status"]
            if detail["publication"]
            else "not_published",
            rollback_status=detail["latest_rollback"]["status"]
            if detail["latest_rollback"]
            else None,
        )

    return paginate(
        session,
        actor,
        project_id,
        m.ExecutionRun,
        c.ExecutionRunSummary,
        query,
        rt,
        request,
        projector=project_row,
    )


router.add_api_route(
    "/api/v1/projects/{project_id}/approval-requests",
    _list(m.ApprovalRequest, c.ApprovalResponse),
    methods=["GET"],
    response_model=c.CursorPage[c.ApprovalResponse],
)
router.add_api_route(
    "/api/v1/projects/{project_id}/role-grants",
    _list(m.ProjectRoleGrant, c.ProjectRoleGrantResponse),
    methods=["GET"],
    response_model=c.CursorPage[c.ProjectRoleGrantResponse],
)
