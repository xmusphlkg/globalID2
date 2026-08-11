"""Control-plane liveness dependencies kept outside HTTP delivery."""

from __future__ import annotations

from sqlalchemy import text

from src.core import get_database
from src.core.disease_cutover import get_disease_cutover_config


async def readiness_payload() -> dict:
    database_ready = False
    try:
        async with get_database() as db:
            await db.execute(text("SELECT 1"))
        database_ready = True
    except Exception:
        pass
    return {
        "status": "ok" if database_ready else "degraded",
        "db": "ok" if database_ready else "error",
        "disease_cutover": get_disease_cutover_config().operational_summary(),
    }


__all__ = ["readiness_payload"]
