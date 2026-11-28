"""FastAPI dependency injection helpers."""

import hmac
import os
from typing import AsyncGenerator

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_config
from src.core.database import get_session_maker

DASHBOARD_API_KEY_HEADER = "x-dashboard-api-key"
ALLOW_UNAUTHENTICATED_PRODUCTION_ENV = "DASHBOARD_ALLOW_UNAUTHENTICATED_PRODUCTION"


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session for request-scoped usage."""
    session_maker = get_session_maker()
    async with session_maker() as session:
        try:
            yield session
        finally:
            await session.close()


def configured_dashboard_api_key() -> str:
    return get_config().dashboard_api_key.strip()


def dashboard_auth_enabled() -> bool:
    return bool(configured_dashboard_api_key())


def _request_api_key(request: Request) -> str:
    header_key = request.headers.get(DASHBOARD_API_KEY_HEADER, "").strip()
    if header_key:
        return header_key

    authorization = request.headers.get("authorization", "").strip()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return ""


def _allow_unauthenticated_production() -> bool:
    return os.environ.get(ALLOW_UNAUTHENTICATED_PRODUCTION_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def require_dashboard_api_key(request: Request) -> None:
    config = get_config()
    expected = (getattr(config, "dashboard_api_key", "") or "").strip()
    if not expected:
        if bool(getattr(config, "is_production", False)) and not _allow_unauthenticated_production():
            raise HTTPException(
                status_code=503,
                detail="Dashboard API key must be configured in production",
            )
        return

    provided = _request_api_key(request)
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Dashboard API key required")
