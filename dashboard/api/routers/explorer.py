"""Data explorer router – safe, allow-listed table browsing."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_db

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


@router.get("/catalog/tables")
async def list_tables():
    return {"tables": sorted(_ALLOWED_TABLES)}


@router.get("/catalog/browse")
async def browse_table(
    response: Response,
    table: str = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
):
    if table not in _ALLOWED_TABLES:
        raise HTTPException(400, f"Table '{table}' is not browsable")

    # table name is validated against the allow-list above – safe to interpolate.
    count_q = text(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
    total = (await db.execute(count_q)).scalar() or 0

    offset = (page - 1) * page_size
    data_q = text(f"SELECT * FROM {table} ORDER BY 1 DESC LIMIT :lim OFFSET :off")  # noqa: S608
    rows = (await db.execute(data_q, {"lim": page_size, "off": offset})).mappings().all()
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Limit"] = str(page_size)
    response.headers["X-Offset"] = str(offset)
    return [dict(r) for r in rows]
