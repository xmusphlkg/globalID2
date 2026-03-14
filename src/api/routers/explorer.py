"""Data explorer router – safe, allow-listed table browsing."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_db

router = APIRouter()

# Only these tables may be browsed directly.
_ALLOWED_TABLES = frozenset(
    {
        "disease_records",
        "diseases",
        "countries",
        "standard_diseases",
        "disease_mappings",
    }
)


@router.get("/explorer/tables")
async def list_tables():
    return {"tables": sorted(_ALLOWED_TABLES)}


@router.get("/explorer/browse")
async def browse_table(
    table: str = Query(...),
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    if table not in _ALLOWED_TABLES:
        raise HTTPException(400, f"Table '{table}' is not browsable")

    # table name is validated against the allow-list above – safe to interpolate.
    count_q = text(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
    total = (await db.execute(count_q)).scalar() or 0

    data_q = text(f"SELECT * FROM {table} ORDER BY 1 DESC LIMIT :lim OFFSET :off")  # noqa: S608
    rows = (await db.execute(data_q, {"lim": limit, "off": offset})).mappings().all()

    return {
        "table": table,
        "total": total,
        "limit": limit,
        "offset": offset,
        "data": [dict(r) for r in rows],
    }
