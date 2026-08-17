"""FastAPI application factory for the GIDS control-plane API."""

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.core.database import dispose_database, get_engine
from src.core.logging import get_logger
from .deps import require_dashboard_api_key
from .http import install_http_contract

from .routers import (
    ai,
    agent_runs,
    countries,
    diseases,
    explorer,
    mappings,
    overview,
    quality,
    release,
    reports,
    settings,
    situation,
    situation_v3,
    sources,
    subscriptions,
    tasks,
    control_plane,
    operations,
    literature,
)
from src.control_plane.events import control_plane_events
from src.control_plane.health import readiness_payload
from src.control_plane.runtime import runtime_registry
from src.core.task_manager import task_manager

logger = get_logger(__name__)
AUTH_EXEMPT_PATHS = {"/health", "/api/v1/health"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hooks."""
    logger.info("API starting up")
    get_engine()
    instance_id = runtime_registry.new_instance_id("api")
    stop_event = asyncio.Event()
    heartbeat = asyncio.create_task(
        runtime_registry.run_heartbeat("api", instance_id, stop_event),
        name="control-plane-api-heartbeat",
    )
    task_manager.set_broadcast_hook(control_plane_events.publish_task_event)
    await control_plane_events.publish("runtime.started", resource_type="runtime", resource_id=instance_id)
    try:
        yield
    finally:
        stop_event.set()
        await heartbeat
        task_manager.set_broadcast_hook(None)
        await control_plane_events.publish("runtime.stopped", resource_type="runtime", resource_id=instance_id)
        await runtime_registry.close()
        await control_plane_events.close()
        await dispose_database()
        logger.info("API shutting down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="GIDS Control Plane API",
        version="1.0.0",
        description="Operational control-plane API for the GIDS surveillance platform",
        lifespan=lifespan,
    )

    install_http_contract(app)

    # CORS – allow the Next.js dev server and common local ports.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def dashboard_api_key_middleware(request: Request, call_next):
        if (
            request.method == "OPTIONS"
            or not request.url.path.startswith("/api/v1")
            or request.url.path in AUTH_EXEMPT_PATHS
        ):
            return await call_next(request)

        try:
            require_dashboard_api_key(request)
        except HTTPException as exc:
            request_id = getattr(request.state, "request_id", None) or request.headers.get("x-request-id") or str(uuid4())
            return JSONResponse(
                status_code=exc.status_code,
                media_type="application/problem+json",
                headers={"X-Request-ID": request_id},
                content={
                    "type": "https://globalid.dev/problems/authentication_required",
                    "title": "Authentication required",
                    "status": exc.status_code,
                    "detail": str(exc.detail),
                    "code": "authentication_required",
                    "instance": request.url.path,
                    "request_id": request_id,
                },
            )

        return await call_next(request)

    # Mount routers
    prefix = "/api/v1"
    app.include_router(countries.router, prefix=prefix, tags=["Countries"])
    app.include_router(mappings.router, prefix=prefix, tags=["Disease Mapping Registry v3"])
    app.include_router(overview.router, prefix=prefix, tags=["Overview"])
    app.include_router(diseases.router, prefix=prefix, tags=["Diseases"])
    app.include_router(reports.router, prefix=prefix, tags=["Reports"])
    app.include_router(situation.router, prefix=prefix, tags=["Situation Room"])
    app.include_router(situation_v3.router, prefix=prefix, tags=["Situation Room v3"])
    app.include_router(tasks.router, prefix=prefix, tags=["Tasks"])
    app.include_router(ai.router, prefix=prefix, tags=["AI"])
    app.include_router(agent_runs.router, prefix=prefix, tags=["Agent Workflow"])
    app.include_router(quality.router, prefix=prefix, tags=["Quality"])
    app.include_router(sources.router, prefix=prefix, tags=["Sources"])
    app.include_router(release.router, prefix=prefix, tags=["Data Release"])
    app.include_router(settings.router, prefix=prefix, tags=["Settings"])
    app.include_router(subscriptions.router, prefix=prefix, tags=["Subscriptions"])
    app.include_router(explorer.router, prefix=prefix, tags=["Explorer"])
    app.include_router(control_plane.router, prefix=prefix, tags=["Control Plane"])
    app.include_router(operations.router, prefix=prefix, tags=["Operations"])
    app.include_router(literature.router, prefix=prefix, tags=["Research Radar"])

    @app.get("/health/live", tags=["Health"])
    async def liveness():
        return {"status": "ok"}

    @app.get("/health/ready", tags=["Health"])
    async def readiness():
        return await readiness_payload()

    @app.get("/api/v1/health", tags=["Health"])
    @app.get("/health", tags=["Health"])
    async def health():
        return await readiness_payload()

    return app


app = create_app()
