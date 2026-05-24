"""FastAPI dependency injection helpers."""

import hmac
from typing import AsyncGenerator

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_config
from src.core.database import get_session_maker

DASHBOARD_API_KEY_HEADER = "x-dashboard-api-key"


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


def require_dashboard_api_key(request: Request) -> None:
    expected = configured_dashboard_api_key()
    if not expected:
        return

    provided = _request_api_key(request)
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Dashboard API key required")
