"""HTTP transaction and identity boundary; no service issues its own HTTP errors."""

from collections.abc import Iterator
from typing import TYPE_CHECKING, Annotated, Any
from uuid import UUID

from fastapi import Depends, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy import literal, select, tuple_
from sqlalchemy.orm import Session

from etl_agent.api.schemas import contracts as c
from etl_agent.infrastructure.persistence.access import public_record
from etl_agent.modules.identity_access.domain import assert_action
from etl_agent.modules.identity_access.service import AuthenticatedSession
from etl_agent.shared.canonical import json_value
from etl_agent.shared.context import ActorContext, RequestContext
from etl_agent.shared.errors import ApplicationError, invalid, unavailable
from etl_agent.shared.security import entity_etag

if TYPE_CHECKING:
    from etl_agent.bootstrap import BackendRuntime


def get_runtime(request: Request) -> "BackendRuntime":
    return request.app.state.backend


def session_dependency(request: Request) -> Iterator[Session]:
    database = request.app.state.backend.database
    if database is None:
        raise unavailable("PostgreSQL")
    with database.session_factory() as session:
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise


SessionDep = Annotated[Session, Depends(session_dependency, scope="function")]


def identity_dependency(request: Request) -> AuthenticatedSession:
    runtime = get_runtime(request)
    if runtime.auth is None:
        raise unavailable("身份服务")
    # Touch/revoke checks are a separate short transaction; business I/O must not
    # retain a dirty session row or its lock while calling a remote SecretProvider.
    with runtime.required_database.session_factory.begin() as auth_session:
        identity = runtime.auth.authenticate(
            auth_session, request.cookies.get(runtime.settings.cookie_name)
        )
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            runtime.auth.validate_csrf(identity, request.headers.get("X-CSRF-Token"))
        return identity


IdentityDep = Annotated[AuthenticatedSession, Depends(identity_dependency)]


def actor_dependency(identity: IdentityDep) -> ActorContext:
    return identity.actor


ActorDep = Annotated[ActorContext, Depends(actor_dependency)]


def _http_query(request: Request, schema: Any) -> Any:
    values: dict[str, Any] = dict(request.query_params)
    if any(len(request.query_params.getlist(name)) != 1 for name in request.query_params):
        raise invalid("查询参数不允许重复")
    if "limit" in values:
        raw = values["limit"]
        if not raw.isascii() or not raw.isdecimal() or len(raw) > 3:
            raise invalid("分页数量必须为1—100的十进制整数", "limit")
        values["limit"] = int(raw)
    try:
        return schema.model_validate(values)
    except ValidationError as error:
        raise invalid("查询参数不符合接口契约") from error


def cursor_query(
    request: Request,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    query: str | None = None,
) -> c.CursorQuery:
    # URL query values are strings. Convert only at the HTTP boundary; keep the
    # shared DTO strict so JSON callers cannot pass floats/booleans as integers.
    return _http_query(request, c.CursorQuery)


def connection_list_query(
    request: Request,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    query: str | None = None,
    connector_type: c.ConnectorType | None = None,
) -> c.ConnectionListQuery:
    return _http_query(request, c.ConnectionListQuery)


def request_context(request: Request) -> RequestContext:
    return RequestContext(
        request.state.request_id,
        request.headers.get("Idempotency-Key"),
        request.headers.get("If-Match"),
    )


def envelope(
    data: Any, request: Request, response: Response | None = None, *, etag: bool = True
) -> dict:
    safe = json_value(data)
    if response is not None and etag:
        response.headers["ETag"] = entity_etag(safe)
    return {"data": safe, "request_id": request.state.request_id}


def page_response(items: list[Any], request: Request, next_cursor: str | None) -> dict:
    return {
        "items": json_value(items),
        "next_cursor": next_cursor,
        "has_more": next_cursor is not None,
        "request_id": request.state.request_id,
    }


def paginate(
    session: Session,
    actor: ActorContext,
    project_id: UUID | None,
    model: Any,
    schema: Any,
    query: c.CursorQuery,
    runtime: Any,
    request: Request,
    *,
    statement: Any = None,
    filters: dict[str, Any] | None = None,
    projector: Any = None,
    sort_field: str = "created_at",
) -> dict:
    if project_id is not None:
        assert_action(actor, project_id, "view")
    stmt = (
        statement if statement is not None else select(model).where(model.project_id == project_id)
    )
    if project_id is not None:
        stmt = stmt.where(model.project_id == project_id)
    binding = {
        "user_id": str(actor.user_id),
        "project_id": str(project_id) if project_id else None,
        "resource": model.__tablename__,
        "filters": filters or {},
        "query": query.query,
        "limit": query.limit,
        "order": sort_field + "_desc_id_desc",
    }
    if query.query:
        if not hasattr(model, "name"):
            raise invalid("该查询尚未定义名称搜索条件", "query")
        stmt = stmt.where(
            model.name.ilike(
                "%" + query.query.replace("%", "\\%").replace("_", "\\_") + "%", escape="\\"
            )
        )
    if query.cursor:
        position = runtime.cursors.decode(query.cursor, binding)
        try:
            from datetime import datetime

            when = (
                datetime.fromisoformat(position["created_at"].replace("Z", "+00:00"))
                if sort_field == "created_at"
                else int(position[sort_field])
            )
            item_id = UUID(position["id"])
        except (ValueError, KeyError, TypeError) as exc:
            raise ApplicationError("INVALID_CURSOR", "游标位置无效", 400) from exc
        stmt = stmt.where(
            tuple_(getattr(model, sort_field), model.id) < tuple_(literal(when), literal(item_id))
        )
    rows = list(
        session.scalars(
            stmt.order_by(getattr(model, sort_field).desc(), model.id.desc()).limit(query.limit + 1)
        )
    )
    has_more = len(rows) > query.limit
    rows = rows[: query.limit]
    cursor = None
    if has_more:
        last = rows[-1]
        cursor = runtime.cursors.encode(
            {sort_field: json_value(getattr(last, sort_field)), "id": str(last.id)}, binding
        )
    return page_response(
        [(projector(row) if projector else public_record(row, schema)) for row in rows],
        request,
        cursor,
    )
