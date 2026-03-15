"""FastAPI application factory for GlobalID V2 API."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.core.database import get_engine
from src.core.logging import get_logger

from .routers import countries, diseases, explorer, overview, quality, reports, sources, tasks

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hooks."""
    logger.info("API starting up")
    # Ensure the engine is created eagerly so the pool is ready.
    get_engine()
    yield
    logger.info("API shutting down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="GlobalID V2 API",
        version="0.1.0",
        description="REST API for the GlobalID disease surveillance dashboard",
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
    app.include_router(quality.router, prefix=prefix, tags=["Quality"])
    app.include_router(sources.router, prefix=prefix, tags=["Sources"])
    app.include_router(explorer.router, prefix=prefix, tags=["Explorer"])

    @app.get("/api/v1/health", tags=["Health"])
    @app.get("/health", tags=["Health"])
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
