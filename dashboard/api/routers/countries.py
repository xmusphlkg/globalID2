"""Countries router."""

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_db
from ..location_codes import PUBLIC_COUNTRY_REGION_CODE_DB_PATTERN
from ..schemas.country import CountryOut
from src.core.country_library import get_country_display_name
from src.domain.country import Country

router = APIRouter()


def _contains_chinese(text: str | None) -> bool:
    if not text:
        return False
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _country_name_zh(country: Country) -> str:
    resolved = get_country_display_name(country.code, "zh")
    if not _contains_chinese(resolved) and _contains_chinese(country.name_local):
        return country.name_local
    return resolved


def _country_to_out(country: Country) -> dict:
    return {
        "id": country.id,
        "code": country.code,
        "name": country.name,
        "name_en": country.name_en,
        "name_zh": _country_name_zh(country),
        "name_local": country.name_local,
        "language": country.language,
        "timezone": country.timezone,
        "is_active": country.is_active,
    }


@router.get("/countries", response_model=List[CountryOut])
async def list_countries(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Country)
        .where(
            Country.is_active.is_(True),
            Country.code.op("~")(PUBLIC_COUNTRY_REGION_CODE_DB_PATTERN),
        )
        .order_by(Country.name)
    )
    return [_country_to_out(country) for country in result.scalars().all()]


@router.get("/countries/{country_id}", response_model=CountryOut)
async def get_country(country_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Country).where(Country.id == country_id))
    country = result.scalar_one_or_none()
    if not country:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Country not found")
    return _country_to_out(country)
