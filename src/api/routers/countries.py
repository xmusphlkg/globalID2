"""Countries router."""

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_db
from src.api.schemas.country import CountryOut
from src.domain.country import Country

router = APIRouter()


@router.get("/countries", response_model=List[CountryOut])
async def list_countries(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Country).where(Country.is_active.is_(True)).order_by(Country.name)
    )
    return result.scalars().all()


@router.get("/countries/{country_id}", response_model=CountryOut)
async def get_country(country_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Country).where(Country.id == country_id))
    country = result.scalar_one_or_none()
    if not country:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Country not found")
    return country
