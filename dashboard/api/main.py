"""FastAPI application factory for GIDS V2 API."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.core.database import get_engine
from src.core.logging import get_logger

from .routers import (
    ai,
    agent_runs,
    countries,
    crawl,
    diseases,
    explorer,
    overview,
    quality,
    release,
    reports,
    settings,
    sources,
    tasks,
)

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hooks."""
    logger.info("API starting up")
    # Ensure the engine is created eagerly so the pool is ready.
    get_engine()

    # Wire task_manager broadcast hook → WebSocket hub
    from src.core.task_manager import task_manager
    from .routers.tasks import task_hub
    from src.services.automation_service import automation_service
    from src.services.data_release_service import data_release_service
    task_manager.set_broadcast_hook(task_hub.broadcast)
    logger.info("Task broadcast hook registered")

    await automation_service.start()
    await data_release_service.start()

    yield
    await data_release_service.stop()
    await automation_service.stop()
    logger.info("API shutting down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="GIDS V2 API",
        version="0.1.0",
        description="REST API for the GIDS disease surveillance dashboard",
        lifespan=lifespan,
    )

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

    # Mount routers
    prefix = "/api/v1"
    app.include_router(countries.router, prefix=prefix, tags=["Countries"])
    app.include_router(overview.router, prefix=prefix, tags=["Overview"])
    app.include_router(diseases.router, prefix=prefix, tags=["Diseases"])
    app.include_router(reports.router, prefix=prefix, tags=["Reports"])
    app.include_router(tasks.router, prefix=prefix, tags=["Tasks"])
    app.include_router(crawl.router, prefix=prefix, tags=["Crawl"])
    app.include_router(ai.router, prefix=prefix, tags=["AI"])
    app.include_router(agent_runs.router, prefix=prefix, tags=["Agent Workflow"])
    app.include_router(quality.router, prefix=prefix, tags=["Quality"])
    app.include_router(sources.router, prefix=prefix, tags=["Sources"])
    app.include_router(release.router, prefix=prefix, tags=["Data Release"])
    app.include_router(settings.router, prefix=prefix, tags=["Settings"])
    app.include_router(explorer.router, prefix=prefix, tags=["Explorer"])

    @app.get("/api/v1/health", tags=["Health"])
    @app.get("/health", tags=["Health"])
    async def health():
        from sqlalchemy import text
        from src.core import get_database
        db_ok = False
        try:
            async with get_database() as db:
                await db.execute(text("SELECT 1"))
            db_ok = True
        except Exception:
            pass
        status = "ok" if db_ok else "degraded"
        return {"status": status, "db": "ok" if db_ok else "error"}

    return app


app = create_app()
