"""Request tracing, response envelopes, and RFC 9457 errors."""

from __future__ import annotations

import json
import math
import time
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.openapi.utils import get_openapi
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from src.core.logging import get_logger
from dashboard.api.schemas.control_plane import ProblemDetail

logger = get_logger(__name__)


def _problem(
    request: Request,
    *,
    status: int,
    title: str,
    detail: str,
    code: str,
    field_errors: list[dict] | None = None,
) -> JSONResponse:
    payload = {
        "type": f"https://globalid.dev/problems/{code}",
        "title": title,
        "status": status,
        "detail": detail,
        "code": code,
        "instance": request.url.path,
        "request_id": getattr(request.state, "request_id", None),
    }
    if field_errors:
        payload["field_errors"] = field_errors
    return JSONResponse(payload, status_code=status, media_type="application/problem+json")


class ControlPlaneHTTPMiddleware(BaseHTTPMiddleware):
    """Apply correlation headers and the v1 data/meta response envelope."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id", "").strip() or str(uuid4())
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["Server-Timing"] = f"app;dur={duration_ms:.1f}"
        logger.bind(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            resource_ids=dict(request.path_params),
            status=response.status_code,
            duration_ms=round(duration_ms, 1),
        ).info("http_request")

        content_type = response.headers.get("content-type", "")
        should_wrap = (
            request.url.path.startswith("/api/v1")
            and request.url.path not in {"/api/v1/health", "/api/v1/events/stream"}
            and 200 <= response.status_code < 300
            and "application/json" in content_type
        )
        if not should_wrap:
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk, bytes) else chunk.encode()
        try:
            payload = json.loads(body or b"null")
        except json.JSONDecodeError:
            headers = dict(response.headers)
            headers.pop("content-length", None)
            return Response(
                content=body,
                status_code=response.status_code,
                headers=headers,
                media_type=response.media_type,
            )
        if isinstance(payload, dict) and "data" in payload and "meta" in payload:
            headers = dict(response.headers)
            headers.pop("content-length", None)
            return JSONResponse(payload, status_code=response.status_code, headers=headers)

        meta: dict = {"request_id": request_id}
        total_raw = response.headers.get("x-total-count")
        if total_raw and total_raw.isdigit():
            total = int(total_raw)
            page_size = int(response.headers.get("x-limit") or len(payload) or 1)
            offset = int(response.headers.get("x-offset") or 0)
            meta["pagination"] = {
                "page": offset // page_size + 1,
                "page_size": page_size,
                "total": total,
                "total_pages": math.ceil(total / page_size) if total else 0,
            }
        headers = dict(response.headers)
        headers.pop("content-length", None)
        return JSONResponse(
            {"data": payload, "meta": meta},
            status_code=response.status_code,
            headers=headers,
        )


def install_http_contract(app: FastAPI) -> None:
    app.add_middleware(ControlPlaneHTTPMiddleware)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, default=str)
        return _problem(
            request,
            status=exc.status_code,
            title="Request failed",
            detail=detail,
            code=f"http_{exc.status_code}",
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        fields = [
            {
                "field": ".".join(str(item) for item in error.get("loc", [])[1:]),
                "message": error.get("msg", "Invalid value"),
                "type": error.get("type"),
            }
            for error in exc.errors()
        ]
        return _problem(
            request,
            status=422,
            title="Validation failed",
            detail="One or more request fields are invalid.",
            code="validation_error",
            field_errors=fields,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.bind(
            request_id=getattr(request.state, "request_id", None),
            path=request.url.path,
        ).exception("unhandled_request_error: {}", exc)
        return _problem(
            request,
            status=500,
            title="Internal server error",
            detail="The request could not be completed.",
            code="internal_error",
        )

    def contract_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        components.setdefault("ProblemDetail", ProblemDetail.model_json_schema())
        for path, methods in schema.get("paths", {}).items():
            if not path.startswith("/api/v1") or path in {"/api/v1/health", "/api/v1/events/stream"}:
                continue
            for operation in methods.values():
                if not isinstance(operation, dict):
                    continue
                for code, response in operation.get("responses", {}).items():
                    if not str(code).startswith("2"):
                        continue
                    content = response.get("content", {}).get("application/json")
                    if not content or "schema" not in content:
                        continue
                    response_schema = content["schema"]
                    ref = response_schema.get("$ref", "") if isinstance(response_schema, dict) else ""
                    if "DataResponse_" in ref:
                        continue
                    content["schema"] = {
                        "type": "object",
                        "required": ["data", "meta"],
                        "properties": {
                            "data": response_schema,
                            "meta": {"$ref": "#/components/schemas/ResponseMeta"},
                        },
                    }
                for status_code in (400, 401, 404, 409, 422, 500):
                    operation.setdefault("responses", {}).setdefault(
                        str(status_code),
                        {
                            "description": "Problem detail",
                            "content": {
                                "application/problem+json": {
                                    "schema": {"$ref": "#/components/schemas/ProblemDetail"}
                                }
                            },
                        },
                    )
        schema["x-gids-contract-version"] = "2026-08"
        app.openapi_schema = schema
        return schema

    app.openapi = contract_openapi


__all__ = ["ControlPlaneHTTPMiddleware", "install_http_contract"]
