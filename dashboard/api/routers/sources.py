"""Sources router – data flow pipeline view per country or across all countries."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain import AutomationJob
from src.domain.country import Country
from src.services.automation_service import automation_service

from ..deps import get_db
from ..location_codes import PUBLIC_COUNTRY_REGION_CODE_DB_PATTERN
from ..schemas.sources import (
    AutomationConfigOut,
    AutomationJobCreate,
    AutomationJobOut,
    AutomationJobUpdate,
    AutomationTriggerResult,
    CountrySourceConfigOut,
    DataSourceFlow,
    SourceOptionOut as SourceOptionOut,
    SourcePolicyOut as SourcePolicyOut,
    StageInfo as StageInfo,
)
from ..services.sources_flow_service import (
    DATA_PIPELINE_STAGES as DATA_PIPELINE_STAGES,
    _FLOW_ROLLUP_SQL as _FLOW_ROLLUP_SQL,
    _FLOW_ROLLUP_TTL_SECONDS as _FLOW_ROLLUP_TTL_SECONDS,
    _ICELAND_HISTORY_SCOPES as _ICELAND_HISTORY_SCOPES,
    _OBSERVATION_GEOGRAPHY_SERIES_SOURCES as _OBSERVATION_GEOGRAPHY_SERIES_SOURCES,
    _SERIES_SOURCE_SCOPE_OVERRIDES as _SERIES_SOURCE_SCOPE_OVERRIDES,
    _automation_job_out as _automation_job_out,
    _build_data_stages as _build_data_stages,
    _build_source_policy as _build_source_policy,
    _contains_chinese as _contains_chinese,
    _country_name_zh as _country_name_zh,
    _country_source_config as _country_source_config,
    _enable_parallel_aggregate as _enable_parallel_aggregate,
    _flow_bucket as _flow_bucket,
    _flow_dates as _flow_dates,
    _history_start_year as _history_start_year,
    _increment_count as _increment_count,
    _load_series_flow_facts as _load_series_flow_facts,
    _observation_rollup as _observation_rollup,
    _optional_bool as _optional_bool,
    _pick_latest_crawl_task as _pick_latest_crawl_task,
    _scope_from_task as _scope_from_task,
    _segment_progress as _segment_progress,
    _series_source_scope as _series_source_scope,
    _should_show_task_only_source as _should_show_task_only_source,
    _source_channel_for_panel as _source_channel_for_panel,
    _source_from_task as _source_from_task,
    _source_kind as _source_kind,
    _source_policy_config as _source_policy_config,
    _source_supports_start_year as _source_supports_start_year,
    _task_country_code as _task_country_code,
    _task_status as _task_status,
    _validate_automation_schedule as _validate_automation_schedule,
    _validate_country_automation_options as _validate_country_automation_options,
    apply_automation_job_updates,
    build_automation_job_entity,
    build_sources_flow,
)

router = APIRouter()


@router.get("/sources/config", response_model=List[CountrySourceConfigOut])
async def get_sources_config(
    lang: str = Query("en", description="Display language: en or zh"),
    db: AsyncSession = Depends(get_db),
):
    """Return per-country source selector and task-option metadata."""
    rows = (
        await db.execute(
            select(Country)
            .where(
                Country.is_active.is_(True),
                Country.code.op("~")(PUBLIC_COUNTRY_REGION_CODE_DB_PATTERN),
            )
            .order_by(Country.name_en.asc(), Country.name.asc())
        )
    ).scalars().all()
    return [_country_source_config(country, lang=lang) for country in rows]


@router.get("/sources/flow", response_model=List[DataSourceFlow])
async def get_sources_flow(
    country_code: Optional[str] = Query(
        None, min_length=2, max_length=10, description="Country code"
    ),
    db: AsyncSession = Depends(get_db),
):
    """Return ingestion flow status per data source for the given country or all countries."""
    return await build_sources_flow(
        db,
        country_code=country_code,
        load_series_facts=_load_series_flow_facts,
    )


@router.get("/schedules/ingestion/config", response_model=AutomationConfigOut)
async def get_sources_automation():
    return AutomationConfigOut(**(await automation_service.snapshot_async()))


@router.get("/schedules/ingestion/jobs", response_model=List[AutomationJobOut])
async def list_automation_jobs(db: AsyncSession = Depends(get_db)):
    await automation_service.ensure_storage()
    rows = (
        await db.execute(
            select(AutomationJob).order_by(
                AutomationJob.country_code.asc(), AutomationJob.job_id.asc()
            )
        )
    ).scalars().all()
    snapshot = await automation_service.snapshot_async()
    state_by_job = {item["job_id"]: item for item in snapshot["jobs"]}
    return [_automation_job_out(row, state_by_job.get(row.job_id)) for row in rows]


@router.post(
    "/schedules/ingestion/jobs", response_model=AutomationJobOut, status_code=201
)
async def create_automation_job(
    body: AutomationJobCreate, db: AsyncSession = Depends(get_db)
):
    await automation_service.ensure_storage()
    _validate_automation_schedule(body.interval_minutes, body.daily_time)
    _validate_country_automation_options(
        body.country_code,
        fill_missing=body.fill_missing,
    )
    existing = (
        await db.execute(
            select(AutomationJob).where(AutomationJob.job_id == body.job_id)
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, f"Automation job already exists: {body.job_id}")

    job = build_automation_job_entity(body)
    db.add(job)
    await db.commit()
    await db.refresh(job)
    await automation_service.reschedule_job(job.job_id)
    snapshot = await automation_service.snapshot_async()
    state_by_job = {item["job_id"]: item for item in snapshot["jobs"]}
    return _automation_job_out(job, state_by_job.get(job.job_id))


@router.put("/schedules/ingestion/jobs/{job_id}", response_model=AutomationJobOut)
async def update_automation_job(
    job_id: str, body: AutomationJobUpdate, db: AsyncSession = Depends(get_db)
):
    await automation_service.ensure_storage()
    job = (
        await db.execute(select(AutomationJob).where(AutomationJob.job_id == job_id))
    ).scalar_one_or_none()
    if not job:
        raise HTTPException(404, f"Automation job not found: {job_id}")

    apply_automation_job_updates(job, body)
    await db.commit()
    await db.refresh(job)
    await automation_service.reschedule_job(job.job_id)
    snapshot = await automation_service.snapshot_async()
    state_by_job = {item["job_id"]: item for item in snapshot["jobs"]}
    return _automation_job_out(job, state_by_job.get(job.job_id))


@router.delete("/schedules/ingestion/jobs/{job_id}")
async def delete_automation_job(job_id: str, db: AsyncSession = Depends(get_db)):
    await automation_service.ensure_storage()
    job = (
        await db.execute(select(AutomationJob).where(AutomationJob.job_id == job_id))
    ).scalar_one_or_none()
    if not job:
        raise HTTPException(404, f"Automation job not found: {job_id}")
    await db.delete(job)
    await db.commit()
    await automation_service.remove_job_state(job_id)
    return {"ok": True, "job_id": job_id}


@router.post(
    "/schedules/ingestion/jobs/{job_id}/runs",
    response_model=AutomationTriggerResult,
    status_code=202,
)
async def run_automation_job(job_id: str):
    try:
        result = await automation_service.trigger_job(job_id, manual=True)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return AutomationTriggerResult(**result)
