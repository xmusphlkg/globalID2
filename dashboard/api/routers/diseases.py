"""Diseases router – catalogue, ontology, records, and comparison."""

from functools import lru_cache
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_db
from ..location_codes import COUNTRY_REGION_CODE_MAX_LENGTH
from ..schemas.disease import DiseaseListItem, DiseaseOut
from ..schemas.disease_record import DiseaseRecordOut
from ..services.disease_series_projection import (
    SERIES_CASE_COUNT_METRICS,
    load_series_first_records,
    monthly_comparison_points,
)
from src.domain.country import Country
from src.domain.disease import Disease
from src.domain.disease_ontology import (
    DiseaseSeriesObservation,
    DiseaseSurveillanceSeries,
)
from src.domain.disease_record import DiseaseRecord
from src.domain.standard_disease import StandardDisease
from src.ontology import DiseaseOntology, load_disease_ontology

router = APIRouter()


async def _resolve_country(country_code: str, db: AsyncSession) -> Country:
    country = (
        await db.execute(
            select(Country).where(func.upper(Country.code) == country_code.strip().upper())
        )
    ).scalar_one_or_none()
    if country is None:
        raise HTTPException(404, "Country not found")
    return country


@lru_cache(maxsize=1)
def _ontology() -> DiseaseOntology:
    """Load the validated configuration registry once per API process."""

    return load_disease_ontology()


@router.get("/disease-ontology", response_model=dict)
async def get_disease_ontology():
    """Return the complete versioned disease ontology registry."""

    return _ontology().to_dict()


@router.get("/disease-ontology/facets", response_model=list[dict])
async def get_disease_ontology_facets():
    """Return every faceted taxonomy as a nested DAG projection."""

    result = _ontology().facet_tree()
    return result if isinstance(result, list) else [result]


@router.get("/disease-ontology/concepts/{disease_code}", response_model=dict)
async def get_disease_ontology_concept(disease_code: str):
    """Return one canonical concept with facets, relations, and source series."""

    try:
        return _ontology().concept_detail(disease_code.upper())
    except KeyError as exc:
        raise HTTPException(404, "Disease ontology concept not found") from exc


@router.get("/disease-ontology/series", response_model=list[dict])
async def list_disease_ontology_series(
    source_id: Optional[str] = Query(None),
    country_code: Optional[str] = Query(
        None, min_length=2, max_length=COUNTRY_REGION_CODE_MAX_LENGTH
    ),
    local_code: Optional[str] = Query(None),
    local_label: Optional[str] = Query(None),
    concept_id: Optional[str] = Query(None),
    group_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    availability_status: Optional[str] = Query(None),
):
    """Search source-specific series without treating their labels as aliases."""

    result = _ontology().series_lookup(
        source_id=source_id,
        country_code=country_code,
        local_code=local_code,
        local_label=local_label,
        concept_id=concept_id.upper() if concept_id else None,
        group_id=group_id,
        status=status,
        availability_status=availability_status,
    )
    return result if isinstance(result, list) else [result]


@router.get("/disease-ontology/availability", response_model=list[dict])
async def list_disease_ontology_availability(
    source_id: Optional[str] = Query(None),
    country_code: Optional[str] = Query(
        None, min_length=2, max_length=COUNTRY_REGION_CODE_MAX_LENGTH
    ),
    concept_id: Optional[str] = Query(None),
    group_id: Optional[str] = Query(None),
    series_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
):
    """Explain source coverage, including explicit source-level absence."""

    return _ontology().availability_lookup(
        source_id=source_id,
        country_code=country_code,
        concept_id=concept_id.upper() if concept_id else None,
        group_id=group_id,
        series_id=series_id,
        status=status,
    )


@router.get(
    "/disease-ontology/series/{series_code}/observations",
    response_model=list[dict],
)
async def list_disease_series_observations(
    series_code: str,
    response: Response,
    page: int = Query(1, ge=1),
    page_size: int = Query(500, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
):
    """Read lossless source-series facts without projecting them into flat IDs."""

    try:
        _ontology().series_lookup(series_code)
    except KeyError as exc:
        raise HTTPException(404, "Disease surveillance series not found") from exc

    total = int(
        (
            await db.execute(
                select(func.count()).select_from(DiseaseSeriesObservation).where(
                    DiseaseSeriesObservation.series_code == series_code
                )
            )
        ).scalar_one()
        or 0
    )
    offset = (page - 1) * page_size
    query = (
        select(DiseaseSeriesObservation)
        .where(DiseaseSeriesObservation.series_code == series_code)
        .order_by(DiseaseSeriesObservation.time)
        .offset(offset)
        .limit(page_size)
    )
    observations = (await db.execute(query)).scalars().all()
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Limit"] = str(page_size)
    response.headers["X-Offset"] = str(offset)
    return [
        {
            "time": item.time.isoformat(),
            "series_code": item.series_code,
            "geography_key": item.geography_key,
            "dimension_key": item.dimension_key,
            "dimensions": item.dimensions,
            "value": item.value,
            "unit": item.unit,
            "suppressed": item.suppressed,
            "suppression_reason": item.suppression_reason,
            "quality_status": item.quality_status,
            "metadata": item.metadata_,
        }
        for item in observations
    ]


@router.get("/diseases", response_model=List[DiseaseListItem])
async def list_diseases(
    country_code: str = Query(..., min_length=2, max_length=COUNTRY_REGION_CODE_MAX_LENGTH),
    lang: str = Query("en"),
    db: AsyncSession = Depends(get_db),
):
    """Return diseases that have records for a country (excludes D999 total row)."""

    country = await _resolve_country(country_code, db)
    country_id = country.id
    country_code = country.code

    if lang == "zh":
        display = func.coalesce(
            StandardDisease.standard_name_zh, Disease.name_en, Disease.name
        ).label("display_name")
    else:
        display = func.coalesce(Disease.name_en, Disease.name).label("display_name")

    legacy_query = (
        select(Disease.name.label("code"), display, Disease.name_en.label("display_name_en"))
        .join(DiseaseRecord, DiseaseRecord.disease_id == Disease.id)
        .outerjoin(StandardDisease, Disease.name == StandardDisease.disease_id)
        .where(DiseaseRecord.country_id == country_id, Disease.name != "D999")
        .group_by(Disease.name, display, Disease.name_en)
        .order_by(display)
    )
    legacy_rows = (await db.execute(legacy_query)).all()

    series_rows = []
    if country_code:
        series_rows = (
            await db.execute(
                select(
                    Disease.name.label("code"),
                    display,
                    Disease.name_en.label("display_name_en"),
                )
                .join(
                    DiseaseSurveillanceSeries,
                    DiseaseSurveillanceSeries.disease_id == Disease.name,
                )
                .join(
                    DiseaseSeriesObservation,
                    DiseaseSeriesObservation.series_code
                    == DiseaseSurveillanceSeries.series_code,
                )
                .outerjoin(StandardDisease, Disease.name == StandardDisease.disease_id)
                .where(
                    DiseaseSurveillanceSeries.country_code == country_code,
                    DiseaseSurveillanceSeries.metric_type.in_(
                        SERIES_CASE_COUNT_METRICS
                    ),
                    DiseaseSurveillanceSeries.unit == "count",
                    DiseaseSeriesObservation.geography_key
                    == f"country:{country_code}:national",
                    DiseaseSeriesObservation.dimension_key == "all",
                    DiseaseSeriesObservation.suppressed.is_(False),
                    DiseaseSeriesObservation.value.is_not(None),
                    DiseaseSeriesObservation.unit == "count",
                    DiseaseSeriesObservation.quality_status != "rejected",
                    Disease.name != "D999",
                )
                .group_by(Disease.name, display, Disease.name_en)
                .order_by(display)
            )
        ).all()

    # Registry-only diseases become discoverable without dropping legacy-only
    # diseases while the migration/backfill is still incomplete.
    rows_by_code = {row.code: row for row in legacy_rows}
    rows_by_code.update({row.code: row for row in series_rows})
    rows = sorted(
        rows_by_code.values(),
        key=lambda row: str(row.display_name or row.code),
    )
    return [
        DiseaseListItem(code=r.code, display_name=r.display_name or r.code, display_name_en=r.display_name_en)
        for r in rows
    ]


@router.get("/diseases/{disease_code}", response_model=DiseaseOut)
async def get_disease(disease_code: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Disease).where(Disease.name == disease_code))
    disease = result.scalar_one_or_none()
    if not disease:
        raise HTTPException(404, "Disease not found")
    return disease


@router.get("/diseases/{disease_code}/records", response_model=List[DiseaseRecordOut])
async def get_disease_records(
    disease_code: str,
    response: Response,
    country_code: str = Query(..., min_length=2, max_length=COUNTRY_REGION_CODE_MAX_LENGTH),
    page: int = Query(1, ge=1),
    page_size: int = Query(500, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
):
    """Series-first records with explicit, period-level legacy gap filling."""
    country = await _resolve_country(country_code, db)
    result = await load_series_first_records(
        db,
        disease_code=disease_code,
        country_id=country.id,
        limit=None,
    )
    offset = (page - 1) * page_size
    response.headers["X-Total-Count"] = str(len(result.records))
    response.headers["X-Limit"] = str(page_size)
    response.headers["X-Offset"] = str(offset)
    return result.records[offset : offset + page_size]


@router.get("/analytics/compare", response_model=dict)
async def compare_diseases(
    country_code: str = Query(..., min_length=2, max_length=COUNTRY_REGION_CODE_MAX_LENGTH),
    diseases: str = Query(..., description="Comma-separated disease codes"),
    db: AsyncSession = Depends(get_db),
):
    """Monthly comparison built from safe series-first disease curves."""
    country = await _resolve_country(country_code, db)
    codes = [c.strip() for c in diseases.split(",") if c.strip()]
    if not codes or len(codes) > 10:
        raise HTTPException(400, "Provide 1–10 comma-separated disease codes")

    compared: list[dict] = []
    for code in codes:
        result = await load_series_first_records(
            db,
            disease_code=code,
            country_id=country.id,
        )
        if not result.records:
            continue
        compared.append(
            {
                "disease_code": result.disease_code,
                "disease_name": result.disease_name,
                "data": monthly_comparison_points(result.records),
                "data_layer": result.metadata.get("data_layer"),
                "projection_policy": result.metadata.get("projection_policy"),
                "loss_risk": result.metadata.get("loss_risk"),
                "coverage": result.metadata.get("coverage") or {},
                "provenance": {
                    "selected_series_codes": result.metadata.get(
                        "selected_series_codes"
                    )
                    or [],
                    "source_series": result.metadata.get("source_series") or [],
                    "available_series_count": result.metadata.get(
                        "available_series_count"
                    )
                    or 0,
                    "fallback_reason": result.metadata.get("fallback_reason"),
                },
            }
        )

    return {"diseases": compared}
