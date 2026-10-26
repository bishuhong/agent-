"""ASGI entry point with safe errors, same-origin writes and request correlation."""

import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, OperationalError
from starlette.exceptions import HTTPException

from etl_agent.api.schemas.contracts import ErrorEnvelope
from etl_agent.bootstrap import BackendRuntime
from etl_agent.config import Settings
from etl_agent.shared.errors import ApplicationError, FieldError

logger = logging.getLogger("etl_agent.http")


def create_app(
    settings: Settings | None = None, *, runtime: BackendRuntime | None = None
) -> FastAPI:
    from etl_agent.api.routes.control import router

    config = settings or (runtime.settings if runtime else Settings())
    app = FastAPI(
        title="ETL-Agent backend",
        version="0.1.0",
        debug=False,
        responses={
            code: {"model": ErrorEnvelope}
            for code in (400, 401, 403, 404, 409, 412, 413, 422, 429, 500, 503)
        },
    )
    app.state.backend = runtime or BackendRuntime(config)
    if config.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "OPTIONS"],
            allow_headers=["Content-Type", "X-CSRF-Token", "If-Match", "Idempotency-Key"],
            expose_headers=["ETag", "X-Request-ID"],
        )

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        # Do not echo attacker-supplied correlation IDs or record request bodies.
        request.state.request_id = str(uuid4())
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin not in config.allowed_origins:
                return error_response(
                    ApplicationError("FORBIDDEN", "写请求Origin未获允许", 403), request
                )
        try:
            response = await call_next(request)
        except Exception:
            logger.error("unhandled_request request_id=%s", request.state.request_id)
            return error_response(
                ApplicationError("INTERNAL_ERROR", "服务暂时无法处理请求", 500), request
            )
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def error_response(error: ApplicationError, request: Request) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"error": error.safe_payload(), "request_id": request.state.request_id},
            headers={"X-Request-ID": request.state.request_id, "Cache-Control": "no-store"},
        )

    @app.exception_handler(ApplicationError)
    async def application_error(request: Request, error: ApplicationError):
        return error_response(error, request)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        # Pydantic's input/context can contain passwords and connector credentials.
        fields = [
            FieldError(".".join(str(v) for v in item["loc"]), "INVALID_VALUE", "字段格式或取值无效")
            for item in error.errors()
        ]
        return error_response(
            ApplicationError("VALIDATION_FAILED", "请求参数无效", 422, field_errors=fields), request
        )

    @app.exception_handler(HTTPException)
    async def protocol_error(request: Request, error: HTTPException):
        code = "RESOURCE_NOT_FOUND" if error.status_code == 404 else "HTTP_ERROR"
        return error_response(
            ApplicationError(code, "请求的资源或方法不可用", error.status_code), request
        )

    @app.exception_handler(OperationalError)
    async def database_unavailable(request: Request, error: OperationalError):
        return error_response(
            ApplicationError("DEPENDENCY_UNAVAILABLE", "控制数据库暂不可用", 503, retryable=True),
            request,
        )

    @app.exception_handler(IntegrityError)
    async def database_conflict(request: Request, error: IntegrityError):
        return error_response(
            ApplicationError("STATE_CONFLICT", "数据约束冲突，请读取最新状态后重试", 409), request
        )

    app.include_router(router)
    return app


app = create_app()
