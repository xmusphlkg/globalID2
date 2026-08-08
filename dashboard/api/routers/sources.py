"""Sources router – data flow pipeline view per country or across all countries."""

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
    SourcePolicyOut,
    SourceOptionOut,
    StageInfo,
)
from src.domain import AutomationJob
from src.domain.country import Country
from src.domain.disease_ontology import (
    DiseaseSeriesObservation,
    DiseaseSourceAvailability,
    DiseaseSurveillanceSeries,
)
from src.domain.disease_record import DiseaseRecord
from src.domain.task import Task, TaskType, TaskWorkbook
from src.core.source_scopes import (
    canonical_data_source_label,
    canonicalize_task_source,
    default_source_for_country,
    get_expected_scopes_for_country,
    get_known_task_sources,
    scope_display_label,
    scope_from_data_source,
    source_options_for_country,
)
from src.core.country_library import get_country_bootstrap_config, get_country_display_name
from src.services.automation_service import automation_service

router = APIRouter()

# Real data-ingestion stages implemented in src/data
DATA_PIPELINE_STAGES = [
    "fetch_list",
    "incremental_check",
    "process_store",
    "finalize",
]

_ICELAND_HISTORY_SCOPES = frozenset(
    {"is_doh_history", "is_doh_legacy_icd"}
)

_SERIES_SOURCE_SCOPE_OVERRIDES = {
    "SRC_AU_NINDSS": "all",
    "SRC_BR_SINAN": "sinan_datasus",
    "SRC_CA_ON_PHO_IDTO": "pho_idto_monthly",
    "SRC_CH_FOPH_IDD": "foph_idd",
    "SRC_CN_CDC": "cdc_weekly",
    "SRC_FI_THL_TTR": "thl_ttr",
    "SRC_HK_CHP": "chp_notifiable",
    "SRC_IS_DOH_ANNUAL": "is_doh_annual",
    "SRC_IS_DOH_HISTORY": "is_doh_history",
    "SRC_IS_DOH_LEGACY_ICD": "is_doh_legacy_icd",
    "SRC_IS_DOH_RESPIRATORY": "is_doh_respiratory",
    "SRC_IS_DOH_STI": "is_doh_sti",
    "SRC_JP_NIID": "jp_weekly",
    "SRC_KR_KDCA": "kdca_open_api",
    "SRC_NO_FHI_MSIS": "fhi_msis",
    "SRC_SE_FOHM_SMINET": "fohm_sminet",
    "SRC_TW_NIDSS": "nidss_open_data",
    "SRC_US_NHSS": "nhss_hiv",
    "SRC_US_NNDSS": "nndss_api",
}


def _source_kind(country_code: str, scope: str) -> str:
    """Classify selector scopes whose execution contracts differ."""

    if country_code == "IS" and scope in _ICELAND_HISTORY_SCOPES:
        return "history"
    return "current"


def _source_supports_start_year(
    country_code: str,
    scope: str,
    *,
    configured_start_year: Optional[int],
) -> bool:
    """Return whether a crawl scope actually consumes ``start_year``.

    Iceland's two historical workbook branches always process the reviewed
    catalogue and deliberately ignore this filter.  Its live Power BI branch
    accepts the filter, including the country-level ``all`` alias (which means
    all *current* dashboards for Iceland).
    """

    if configured_start_year is None:
        return False
    return not (
        country_code == "IS" and scope in _ICELAND_HISTORY_SCOPES
    )


def _series_source_scope(source_system: str, *, country_code: str) -> str:
    """Resolve a series registry source ID to a dashboard crawl scope."""

    raw = str(source_system or "").strip()
    if raw.upper() in _SERIES_SOURCE_SCOPE_OVERRIDES:
        return _SERIES_SOURCE_SCOPE_OVERRIDES[raw.upper()]
    candidate = raw.casefold()
    if candidate.startswith("src_"):
        candidate = candidate[4:]
    expected = set(get_expected_scopes_for_country(country_code))
    if candidate in expected:
        return candidate
    return scope_from_data_source(raw)


def _increment_count(
    target: Dict[str, Dict[str, int]],
    key: str,
    value: str,
    count: int,
) -> None:
    bucket = target.setdefault(key, {})
    bucket[value] = bucket.get(value, 0) + int(count or 0)


def _validate_country_automation_options(
    country_code: str,
    *,
    fill_missing: bool,
) -> None:
    if str(country_code or "").strip().upper() == "IS" and fill_missing:
        raise HTTPException(
            400,
            "Iceland mixed-grain sources preserve missing periods as unknown; fill_missing is not supported",
        )


def _contains_chinese(text: str | None) -> bool:
    if not text:
        return False
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _country_name_zh(country: Country) -> str:
    resolved = get_country_display_name(country.code, "zh")
    if not _contains_chinese(resolved) and _contains_chinese(country.name_local):
        return country.name_local
    return resolved


def _history_start_year(country_code: Optional[str]) -> Optional[int]:
    cfg = get_country_bootstrap_config(country_code or "")
    crawler_cfg = cfg.get("crawler_config", {}) if isinstance(cfg, dict) else {}
    raw_value = crawler_cfg.get("full_history_start_year")
    if raw_value is None:
        return None
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _country_source_config(country: Country, *, lang: str = "en") -> CountrySourceConfigOut:
    from src.services.crawl_service import CrawlService

    code = (country.code or "").strip().upper()
    cfg = get_country_bootstrap_config(code)
    crawler_cfg = cfg.get("crawler_config", {}) if isinstance(cfg, dict) else {}
    start_year = _history_start_year(code)
    options = [
        SourceOptionOut(
            value=option["value"],
            label_en=(
                "Iceland Directorate of Health — All Current Dashboards"
                if code == "IS" and option["value"] == "all"
                else option["label_en"]
            ),
            label_zh=(
                "冰岛卫生署全部当前看板"
                if code == "IS" and option["value"] == "all"
                else option["label_zh"]
            ),
            label=(
                (
                    "冰岛卫生署全部当前看板"
                    if code == "IS" and option["value"] == "all"
                    else option["label_zh"]
                )
                if (lang or "").lower().startswith("zh")
                else (
                    "Iceland Directorate of Health — All Current Dashboards"
                    if code == "IS" and option["value"] == "all"
                    else option["label_en"]
                )
            ),
            source_kind=_source_kind(code, option["value"]),
            supports_start_year=_source_supports_start_year(
                code,
                option["value"],
                configured_start_year=start_year,
            ),
        )
        for option in source_options_for_country(code)
    ]
    supports_fill_missing = _optional_bool(
        crawler_cfg.get("supports_fill_missing"),
        code not in {"IS", "US"},
    )
    if code == "IS":
        # All Iceland branches preserve missing source periods as unknown.
        # Synthetic monthly filling would be incorrect for its mixed grain.
        supports_fill_missing = False
    default_fill_missing = supports_fill_missing and _optional_bool(
        crawler_cfg.get("default_fill_missing"),
        code not in {"BR"},
    )
    supports_current_month = _optional_bool(
        crawler_cfg.get("supports_current_month"), False
    )
    default_include_current_month = supports_current_month and _optional_bool(
        crawler_cfg.get("default_include_current_month"), False
    )
    try:
        revision_window_months = max(
            1, min(24, int(crawler_cfg.get("refresh_recent_months") or 3))
        )
    except (TypeError, ValueError):
        revision_window_months = 3
    publication_day = crawler_cfg.get("publication_day")
    try:
        publication_day = int(publication_day) if publication_day is not None else None
    except (TypeError, ValueError):
        publication_day = None

    return CountrySourceConfigOut(
        country_code=code,
        country_name=country.name_en or country.name or code,
        country_name_en=country.name_en or country.name or code,
        country_name_zh=_country_name_zh(country),
        language=country.language or cfg.get("report_config", {}).get("lang") or "en",
        timezone=country.timezone or "UTC",
        supports_crawl=(
            bool(get_expected_scopes_for_country(code))
            and code in CrawlService.supported_country_codes()
        ),
        supports_fill_missing=supports_fill_missing,
        default_fill_missing=default_fill_missing,
        default_source=default_source_for_country(code),
        default_start_year=start_year,
        supports_start_year=any(option.supports_start_year for option in options),
        supports_source_file=bool(
            crawler_cfg.get("dportal_file_env")
            or crawler_cfg.get("kosis_file_env")
        ),
        supports_source_dir=bool(crawler_cfg.get("dportal_dir_env")),
        source_policy=SourcePolicyOut(
            supports_current_month=supports_current_month,
            default_include_current_month=default_include_current_month,
            dynamic_revision_enabled=_optional_bool(
                crawler_cfg.get("dynamic_revision_enabled"), False
            ),
            default_revision_window_months=revision_window_months,
            current_month_status=str(
                crawler_cfg.get("current_month_status") or "not_supported"
            ),
            public_release_enabled=_optional_bool(
                cfg.get("public_release_enabled"), True
            ),
            public_release_editable=False,
            publication_day=publication_day,
            source_update_cadence=(
                str(crawler_cfg.get("source_update_cadence"))
                if crawler_cfg.get("source_update_cadence")
                else None
            ),
        ),
        source_options=options,
    )


def _task_status(task: Optional[Task]) -> Optional[str]:
    if task is None:
        return None
    status = task.status
    return status.value if hasattr(status, "value") else str(status)


def _should_show_task_only_source(task: Optional[Task]) -> bool:
    """Only surface task-only rows when the source still needs attention."""
    status = _task_status(task)
    return status in {"pending", "queued", "running", "retrying", "failed"}


def _task_country_code(task: Task) -> str:
    inp = task.input_data or {}
    if isinstance(inp, dict):
        return str(inp.get("country_code") or inp.get("country") or "").strip().upper()
    return ""


def _scope_from_task(task: Task) -> str:
    """Infer task scope from task input_data/task_name."""
    inp = task.input_data or {}
    country_code = _task_country_code(task)
    source = canonicalize_task_source(
        (inp.get("source") or "") if isinstance(inp, dict) else "",
        country_code=country_code,
    )
    if source in get_known_task_sources(country_code):
        return source

    # Backward compatibility: old tasks only stored data_source text.
    if isinstance(inp, dict) and inp.get("data_source"):
        return scope_from_data_source(str(inp.get("data_source")))

    name = (task.task_name or "").lower()
    if "pubmed" in name:
        return "pubmed"
    if "idwr" in name or "niid" in name or "japan" in name:
        return "jp_weekly"
    if "nndss" in name:
        return "nndss_api"
    if "nhss" in name or ("hiv" in name and country_code == "US"):
        return "nhss_hiv"
    if "sinan" in name or "datasus" in name or "brazil" in name:
        return "sinan_datasus"
    if "kdca" in name or "korea" in name:
        return "kdca_open_api"
    if "gov" in name or "nhc" in name:
        return "nhc"
    if "cdc" in name or "weekly" in name:
        return "cdc_weekly"
    return "all"


def _source_from_task(task: Task) -> str:
    inp = task.input_data or {}
    country_code = _task_country_code(task)
    source = canonicalize_task_source(
        (inp.get("source") or "") if isinstance(inp, dict) else "",
        country_code=country_code,
    )
    if not source and isinstance(inp, dict) and inp.get("data_source"):
        source = scope_from_data_source(str(inp.get("data_source")))
    return source or "all"


def _pick_latest_crawl_task(
    latest_by_scope: Dict[str, Task],
    *,
    scope: str,
) -> Optional[Task]:
    """Get latest crawl task by exact scope first, then country-wide ('all')."""
    if ":" in scope:
        country_key, scope_name = scope.split(":", 1)
        if scope_name in _ICELAND_HISTORY_SCOPES:
            # Iceland's ``all`` task is explicitly current-only.  Reusing it
            # for the two immutable workbook branches would fabricate a
            # history-source freshness signal.
            return latest_by_scope.get(scope)
        return latest_by_scope.get(scope) or latest_by_scope.get(f"{country_key}:all")
    return latest_by_scope.get(scope) or latest_by_scope.get("all")


def _segment_progress(progress: int, start: int, end: int) -> int:
    if progress <= start:
        return 0
    if progress >= end:
        return 100
    span = max(1, end - start)
    return int(((progress - start) / span) * 100)


def _build_data_stages(task: Optional[Task], workbook_entries: List[TaskWorkbook]) -> List[StageInfo]:
    """Map one crawl task to detailed src/data pipeline stage statuses."""
    if task is None:
        return [
            StageInfo(
                stage=stage,
                task_type="crawl_data",
                status=None,
                task_uuid=None,
                task_name=None,
                progress=0,
                last_run=None,
            )
            for stage in DATA_PIPELINE_STAGES
        ]

    status = _task_status(task) or "pending"
    progress = int(task.progress or 0)
    last_run = (
        task.completed_at.isoformat()
        if task.completed_at
        else (task.created_at.isoformat() if task.created_at else None)
    )

    completed_no_new = any(
        (w.title == "Crawl Completed") and ("No new data found" in (w.content or ""))
        for w in workbook_entries
    )
    phase2_done = any(w.title in {"Phase 2/3 Complete", "Raw Data Saved"} for w in workbook_entries)

    stage_status = {k: None for k in DATA_PIPELINE_STAGES}

    if status in {"pending", "queued"}:
        stage_status["fetch_list"] = status
    elif status == "running":
        if progress < 30:
            stage_status["fetch_list"] = "running"
        elif progress < 50:
            stage_status["fetch_list"] = "completed"
            stage_status["incremental_check"] = "running"
        elif progress < 90:
            stage_status["fetch_list"] = "completed"
            stage_status["incremental_check"] = "completed"
            stage_status["process_store"] = "running"
        else:
            stage_status["fetch_list"] = "completed"
            stage_status["incremental_check"] = "completed"
            stage_status["process_store"] = "completed" if (phase2_done or completed_no_new) else "running"
            stage_status["finalize"] = "running"
    elif status == "completed":
        stage_status["fetch_list"] = "completed"
        stage_status["incremental_check"] = "completed"
        stage_status["process_store"] = "skipped" if completed_no_new else "completed"
        stage_status["finalize"] = "completed"
    else:
        # failed / cancelled / retrying
        if progress < 30:
            stage_status["fetch_list"] = status
        elif progress < 50:
            stage_status["fetch_list"] = "completed"
            stage_status["incremental_check"] = status
        elif progress < 90:
            stage_status["fetch_list"] = "completed"
            stage_status["incremental_check"] = "completed"
            stage_status["process_store"] = status
        else:
            stage_status["fetch_list"] = "completed"
            stage_status["incremental_check"] = "completed"
            stage_status["process_store"] = "completed" if (phase2_done or completed_no_new) else status
            stage_status["finalize"] = status

    stage_progress = {
        "fetch_list": _segment_progress(progress, 0, 30),
        "incremental_check": _segment_progress(progress, 30, 50),
        "process_store": _segment_progress(progress, 50, 90),
        "finalize": _segment_progress(progress, 90, 100),
    }

    out: List[StageInfo] = []
    for stage in DATA_PIPELINE_STAGES:
        this_status = stage_status[stage]
        out.append(
            StageInfo(
                stage=stage,
                task_type="crawl_data",
                status=this_status,
                task_uuid=task.task_uuid if this_status else None,
                task_name=(
                    "Skipped (No New Data)"
                    if (stage == "process_store" and this_status == "skipped")
                    else (task.task_name if this_status else None)
                ),
                progress=(
                    100
                    if this_status in {"completed", "skipped"}
                    else (stage_progress[stage] if this_status else 0)
                ),
                last_run=last_run if this_status else None,
            )
        )

    return out


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
    country_id: Optional[int] = Query(None, ge=1, description="Country ID"),
    db: AsyncSession = Depends(get_db),
):
    """Return ingestion flow status per data source for the given country or all countries."""

    # 1. Distinct data sources + basic stats from disease_records
    src_q = (
        select(
            DiseaseRecord.country_id,
            Country.code.label("country_code"),
            Country.name_en.label("country_name"),
            DiseaseRecord.data_source,
            func.count().label("record_count"),
            func.min(DiseaseRecord.time).label("earliest_date"),
            func.max(DiseaseRecord.time).label("latest_date"),
        )
        .outerjoin(Country, Country.id == DiseaseRecord.country_id)
        .group_by(
            DiseaseRecord.country_id,
            Country.code,
            Country.name_en,
            DiseaseRecord.data_source,
        )
        .order_by(func.count().desc(), Country.name_en.asc(), DiseaseRecord.data_source.asc())
    )
    if country_id is not None:
        src_q = src_q.where(DiseaseRecord.country_id == country_id)
    src_rows = (await db.execute(src_q)).all()

    # Source-series facts are the lossless surveillance layer.  They must be
    # counted independently from ``disease_records`` compatibility projections
    # so the same observation is never double-counted and non-case metrics such
    # as Iceland's registered diagnoses retain their actual semantics.
    series_q = (
        select(
            Country.id.label("country_id"),
            Country.code.label("country_code"),
            Country.name_en.label("country_name"),
            DiseaseSurveillanceSeries.source_system,
            func.count(
                func.distinct(DiseaseSurveillanceSeries.series_code)
            ).label("series_count"),
            func.count(DiseaseSeriesObservation.id).label("observation_count"),
            func.min(DiseaseSeriesObservation.time).label("earliest_date"),
            func.max(DiseaseSeriesObservation.time).label("latest_date"),
        )
        .select_from(DiseaseSurveillanceSeries)
        .join(Country, Country.code == DiseaseSurveillanceSeries.country_code)
        .outerjoin(
            DiseaseSeriesObservation,
            DiseaseSeriesObservation.series_code
            == DiseaseSurveillanceSeries.series_code,
        )
        .group_by(
            Country.id,
            Country.code,
            Country.name_en,
            DiseaseSurveillanceSeries.source_system,
        )
    )
    if country_id is not None:
        series_q = series_q.where(Country.id == country_id)
    series_rows = (await db.execute(series_q)).all()

    definition_q = (
        select(
            Country.id.label("country_id"),
            Country.code.label("country_code"),
            DiseaseSurveillanceSeries.source_system,
            DiseaseSurveillanceSeries.availability_status,
            DiseaseSurveillanceSeries.metric_type,
            DiseaseSurveillanceSeries.mapping_relation,
            DiseaseSurveillanceSeries.comparability,
            func.count().label("count"),
        )
        .select_from(DiseaseSurveillanceSeries)
        .join(Country, Country.code == DiseaseSurveillanceSeries.country_code)
        .group_by(
            Country.id,
            Country.code,
            DiseaseSurveillanceSeries.source_system,
            DiseaseSurveillanceSeries.availability_status,
            DiseaseSurveillanceSeries.metric_type,
            DiseaseSurveillanceSeries.mapping_relation,
            DiseaseSurveillanceSeries.comparability,
        )
    )
    if country_id is not None:
        definition_q = definition_q.where(Country.id == country_id)
    definition_rows = (await db.execute(definition_q)).all()

    quality_q = (
        select(
            Country.id.label("country_id"),
            Country.code.label("country_code"),
            DiseaseSurveillanceSeries.source_system,
            DiseaseSeriesObservation.quality_status,
            func.count().label("count"),
        )
        .select_from(DiseaseSurveillanceSeries)
        .join(Country, Country.code == DiseaseSurveillanceSeries.country_code)
        .join(
            DiseaseSeriesObservation,
            DiseaseSeriesObservation.series_code
            == DiseaseSurveillanceSeries.series_code,
        )
        .group_by(
            Country.id,
            Country.code,
            DiseaseSurveillanceSeries.source_system,
            DiseaseSeriesObservation.quality_status,
        )
    )
    if country_id is not None:
        quality_q = quality_q.where(Country.id == country_id)
    quality_rows = (await db.execute(quality_q)).all()

    availability_q = (
        select(
            Country.id.label("country_id"),
            Country.code.label("country_code"),
            DiseaseSourceAvailability.source_system,
            DiseaseSourceAvailability.status,
            func.count().label("count"),
        )
        .select_from(DiseaseSourceAvailability)
        .join(Country, Country.code == DiseaseSourceAvailability.country_code)
        .group_by(
            Country.id,
            Country.code,
            DiseaseSourceAvailability.source_system,
            DiseaseSourceAvailability.status,
        )
    )
    if country_id is not None:
        availability_q = availability_q.where(Country.id == country_id)
    availability_rows = (await db.execute(availability_q)).all()

    series_availability_by_key: Dict[str, Dict[str, int]] = {}
    metric_types_by_key: Dict[str, Dict[str, int]] = {}
    mapping_relations_by_key: Dict[str, Dict[str, int]] = {}
    comparability_by_key: Dict[str, Dict[str, int]] = {}
    observation_quality_by_key: Dict[str, Dict[str, int]] = {}
    source_availability_by_key: Dict[str, Dict[str, int]] = {}

    for row in definition_rows:
        scope = _series_source_scope(
            row.source_system,
            country_code=row.country_code,
        )
        key = f"{row.country_id}:{scope}"
        _increment_count(
            series_availability_by_key,
            key,
            str(row.availability_status),
            row.count,
        )
        _increment_count(
            metric_types_by_key, key, str(row.metric_type), row.count
        )
        _increment_count(
            mapping_relations_by_key,
            key,
            str(row.mapping_relation),
            row.count,
        )
        _increment_count(
            comparability_by_key, key, str(row.comparability), row.count
        )

    for row in quality_rows:
        scope = _series_source_scope(
            row.source_system,
            country_code=row.country_code,
        )
        key = f"{row.country_id}:{scope}"
        _increment_count(
            observation_quality_by_key,
            key,
            str(row.quality_status),
            row.count,
        )

    for row in availability_rows:
        scope = _series_source_scope(
            row.source_system,
            country_code=row.country_code,
        )
        key = f"{row.country_id}:{scope}"
        _increment_count(
            source_availability_by_key, key, str(row.status), row.count
        )

    # Normalize/merge rows by canonical source scope (not raw display text).
    merged_sources: Dict[str, Dict[str, object]] = {}
    for row in src_rows:
        scope = scope_from_data_source(row.data_source)
        display_label = canonical_data_source_label(
            row.data_source,
            country_code=row.country_code,
        )
        country_key = row.country_id if row.country_id is not None else "none"
        merged_key = f"{country_key}:{scope}"

        prev = merged_sources.get(merged_key)
        if prev is None:
            merged_sources[merged_key] = {
                "key": merged_key,
                "country_id": row.country_id,
                "country_code": row.country_code,
                "country_name": row.country_name,
                "scope": scope,
                "data_source": display_label,
                "record_count": int(row.record_count or 0),
                "source_series_count": 0,
                "source_observation_count": 0,
                "series_availability": {},
                "source_availability": {},
                "observation_quality": {},
                "metric_types": {},
                "mapping_relations": {},
                "comparability": {},
                "earliest_date": row.earliest_date,
                "latest_date": row.latest_date,
                "history_start_year": _history_start_year(row.country_code),
                "expected_source": False,
            }
            continue

        prev["record_count"] = int(prev["record_count"]) + int(row.record_count or 0)
        prev_earliest = prev.get("earliest_date")
        if (prev_earliest is None) or (row.earliest_date and row.earliest_date < prev_earliest):
            prev["earliest_date"] = row.earliest_date
        prev_latest = prev.get("latest_date")
        if (prev_latest is None) or (row.latest_date and row.latest_date > prev_latest):
            prev["latest_date"] = row.latest_date

    for row in series_rows:
        scope = _series_source_scope(
            row.source_system,
            country_code=row.country_code,
        )
        merged_key = f"{row.country_id}:{scope}"
        prev = merged_sources.get(merged_key)
        if prev is None:
            prev = {
                "key": merged_key,
                "country_id": row.country_id,
                "country_code": row.country_code,
                "country_name": row.country_name,
                "scope": scope,
                "data_source": scope_display_label(
                    scope, country_code=row.country_code
                ),
                "record_count": 0,
                "source_series_count": 0,
                "source_observation_count": 0,
                "series_availability": {},
                "source_availability": {},
                "observation_quality": {},
                "metric_types": {},
                "mapping_relations": {},
                "comparability": {},
                "earliest_date": row.earliest_date,
                "latest_date": row.latest_date,
                "history_start_year": _history_start_year(row.country_code),
                "expected_source": scope
                in get_expected_scopes_for_country(row.country_code),
            }
            merged_sources[merged_key] = prev

        prev["source_series_count"] = int(
            prev.get("source_series_count") or 0
        ) + int(row.series_count or 0)
        prev["source_observation_count"] = int(
            prev.get("source_observation_count") or 0
        ) + int(row.observation_count or 0)
        prev["series_availability"] = series_availability_by_key.get(
            merged_key, {}
        )
        prev["source_availability"] = source_availability_by_key.get(
            merged_key, {}
        )
        prev["observation_quality"] = observation_quality_by_key.get(
            merged_key, {}
        )
        prev["metric_types"] = metric_types_by_key.get(merged_key, {})
        prev["mapping_relations"] = mapping_relations_by_key.get(
            merged_key, {}
        )
        prev["comparability"] = comparability_by_key.get(merged_key, {})
        prev_earliest = prev.get("earliest_date")
        if (prev_earliest is None) or (
            row.earliest_date and row.earliest_date < prev_earliest
        ):
            prev["earliest_date"] = row.earliest_date
        prev_latest = prev.get("latest_date")
        if (prev_latest is None) or (
            row.latest_date and row.latest_date > prev_latest
        ):
            prev["latest_date"] = row.latest_date

    # 2. Gather most-recent crawl tasks and index them by source scope.
    task_q = select(Task).where(Task.task_type == TaskType.CRAWL_DATA).order_by(Task.created_at.desc())
    if country_id is not None:
        task_q = task_q.where(Task.country_id == country_id)
    tasks = (await db.execute(task_q)).scalars().all()

    if not src_rows and not series_rows and not tasks and country_id is None:
        return []

    # Index: country_id:scope -> latest crawl task (query already desc by created_at).
    latest_by_scope: Dict[str, Task] = {}
    for t in tasks:
        scope = _scope_from_task(t)
        key = f"{t.country_id if t.country_id is not None else 'none'}:{scope}"
        if key not in latest_by_scope:
            latest_by_scope[key] = t

    # Preload workbook entries for selected latest tasks.
    selected_task_ids = {task.id for task in latest_by_scope.values()}
    workbook_by_task_id: Dict[int, List[TaskWorkbook]] = {}
    if selected_task_ids:
        wb_rows = (await db.execute(
            select(TaskWorkbook)
            .where(TaskWorkbook.task_id.in_(selected_task_ids))
            .order_by(TaskWorkbook.created_at.asc())
        )).scalars().all()
        for wb in wb_rows:
            workbook_by_task_id.setdefault(wb.task_id, []).append(wb)

    # 2.1 Add virtual source rows for scopes that have tasks but no records yet.
    represented_keys = set(merged_sources.keys())
    country_ids_for_meta = {
        int(row.country_id)
        for row in src_rows
        if row.country_id is not None
    } | {
        int(row.country_id)
        for row in series_rows
        if row.country_id is not None
    } | {
        int(t.country_id)
        for t in tasks
        if t.country_id is not None
    }
    if country_id is not None:
        country_ids_for_meta.add(country_id)

    country_meta = {
        row.id: row
        for row in (
            await db.execute(
                select(Country).where(Country.id.in_(country_ids_for_meta))
            )
        ).scalars().all()
    }
    for task_key, task in latest_by_scope.items():
        _, scope = task_key.split(":", 1)
        if scope in {"all"} or task_key in represented_keys:
            continue
        if task.country_id is None:
            continue
        country = country_meta.get(task.country_id) if task.country_id is not None else None
        label = scope_display_label(scope, country_code=country.code if country else None)
        is_expected_source = bool(
            country
            and scope in get_expected_scopes_for_country(country.code)
        )
        merged_sources[task_key] = {
                "key": task_key,
                "country_id": task.country_id,
                "country_code": country.code if country else None,
                "country_name": country.name_en if country else None,
                "scope": scope,
                "data_source": label,
                "record_count": 0,
                "source_series_count": 0,
                "source_observation_count": 0,
                "series_availability": {},
                "source_availability": {},
                "observation_quality": {},
                "metric_types": {},
                "mapping_relations": {},
                "comparability": {},
                "earliest_date": None,
                "latest_date": None,
                "history_start_year": _history_start_year(country.code if country else None),
                "expected_source": is_expected_source,
        }

    # 2.2 Ensure configured per-country sources appear even before data exists.
    country_scope_candidates: Dict[int, Country] = {}
    for src in merged_sources.values():
        country_id_value = src.get("country_id")
        if country_id_value is None:
            continue
        country_code_value = src.get("country_code")
        country_name_value = src.get("country_name")
        if country_id_value not in country_scope_candidates:
            country_scope_candidates[int(country_id_value)] = Country(
                id=int(country_id_value),
                code=str(country_code_value or ""),
                name_en=str(country_name_value or ""),
            )

    if country_id is not None and country_id in country_meta:
        country_scope_candidates[country_id] = country_meta[country_id]

    for task in tasks:
        if task.country_id is None:
            continue
        if task.country_id in country_scope_candidates:
            continue
        country = country_meta.get(task.country_id)
        if country is not None:
            country_scope_candidates[task.country_id] = country

    for country in country_scope_candidates.values():
        expected_scopes = get_expected_scopes_for_country(country.code)
        for scope in expected_scopes:
            merged_key = f"{country.id}:{scope}"
            if merged_key in merged_sources:
                continue
            merged_sources[merged_key] = {
                "key": merged_key,
                "country_id": country.id,
                "country_code": country.code,
                "country_name": country.name_en,
                "scope": scope,
                "data_source": scope_display_label(scope, country_code=country.code),
                "record_count": 0,
                "source_series_count": 0,
                "source_observation_count": 0,
                "series_availability": {},
                "source_availability": {},
                "observation_quality": {},
                "metric_types": {},
                "mapping_relations": {},
                "comparability": {},
                "earliest_date": None,
                "latest_date": None,
                "history_start_year": _history_start_year(country.code),
                "expected_source": True,
            }

    # 3. Build flow objects
    result: List[DataSourceFlow] = []
    sorted_sources = sorted(
        merged_sources.values(),
        key=lambda item: (
            str(item.get("country_name") or "").lower(),
            -int(item.get("source_observation_count") or item["record_count"]),
            str(item["data_source"]).lower(),
        ),
    )

    for src in sorted_sources:
        row_scope = str(src["scope"])
        scope_key = f"{src.get('country_id') if src.get('country_id') is not None else 'none'}:{row_scope}"
        task = _pick_latest_crawl_task(latest_by_scope, scope=scope_key)
        workbook_entries = workbook_by_task_id.get(task.id, []) if task else []
        stages = _build_data_stages(task, workbook_entries)

        latest_task_time = None
        if task:
            latest_task_time = (
                task.completed_at.isoformat()
                if task.completed_at
                else (task.created_at.isoformat() if task.created_at else None)
            )

        has_records = (
            int(src["record_count"]) > 0
            or int(src.get("source_series_count") or 0) > 0
            or int(src.get("source_observation_count") or 0) > 0
        )
        has_real_country = src.get("country_id") is not None
        is_expected_source = bool(src.get("expected_source"))
        if not has_real_country:
            continue
        if not has_records and not _should_show_task_only_source(task) and not is_expected_source:
            continue

        result.append(
            DataSourceFlow(
                data_source=str(src["data_source"]),
                country_id=src.get("country_id"),
                country_code=src.get("country_code"),
                country_name=src.get("country_name"),
                record_count=int(src["record_count"]),
                source_series_count=int(src.get("source_series_count") or 0),
                source_observation_count=int(
                    src.get("source_observation_count") or 0
                ),
                series_availability=dict(src.get("series_availability") or {}),
                source_availability=dict(src.get("source_availability") or {}),
                observation_quality=dict(src.get("observation_quality") or {}),
                metric_types=dict(src.get("metric_types") or {}),
                mapping_relations=dict(src.get("mapping_relations") or {}),
                comparability=dict(src.get("comparability") or {}),
                earliest_date=(
                    src["earliest_date"].strftime("%Y-%m-%d")
                    if src.get("earliest_date")
                    else None
                ),
                latest_date=(
                    src["latest_date"].strftime("%Y-%m-%d")
                    if src.get("latest_date")
                    else None
                ),
                history_start_year=src.get("history_start_year"),
                source_scope=row_scope,
                latest_task_uuid=task.task_uuid if task else None,
                latest_task_source=_source_from_task(task) if task else None,
                latest_task_status=_task_status(task),
                latest_task_time=latest_task_time,
                stages=stages,
            )
        )

    return result


@router.get("/sources/automation", response_model=AutomationConfigOut)
async def get_sources_automation():
    return AutomationConfigOut(**(await automation_service.snapshot_async()))


@router.get("/sources/automation/jobs", response_model=List[AutomationJobOut])
async def list_automation_jobs(db: AsyncSession = Depends(get_db)):
    await automation_service.ensure_storage()
    rows = (
        await db.execute(
            select(AutomationJob).order_by(AutomationJob.country_code.asc(), AutomationJob.job_id.asc())
        )
    ).scalars().all()
    snapshot = await automation_service.snapshot_async()
    state_by_job = {item["job_id"]: item for item in snapshot["jobs"]}
    return [
        _automation_job_out(row, state_by_job.get(row.job_id))
        for row in rows
    ]


@router.post("/sources/automation/jobs", response_model=AutomationJobOut, status_code=201)
async def create_automation_job(body: AutomationJobCreate, db: AsyncSession = Depends(get_db)):
    await automation_service.ensure_storage()
    _validate_automation_schedule(body.interval_minutes, body.daily_time)
    _validate_country_automation_options(
        body.country_code,
        fill_missing=body.fill_missing,
    )
    existing = (
        await db.execute(select(AutomationJob).where(AutomationJob.job_id == body.job_id))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, f"Automation job already exists: {body.job_id}")

    job = AutomationJob(
        job_id=body.job_id.strip(),
        name=body.name.strip(),
        country_code=body.country_code.strip().upper(),
        source=canonicalize_task_source(
            body.source,
            country_code=body.country_code,
        ),
        enabled=body.enabled,
        priority=(body.priority or "normal").strip().lower(),
        process=body.process,
        save_raw=body.save_raw,
        fill_missing=body.fill_missing,
        force=body.force,
        include_current_month=(
            body.include_current_month
            if body.include_current_month is not None
            else _optional_bool(
                (
                    get_country_bootstrap_config(body.country_code)
                    .get("crawler_config", {})
                    .get("default_include_current_month")
                ),
                False,
            )
        ),
        revision_window_months=body.revision_window_months,
        retry_threshold=body.retry_threshold,
        interval_minutes=body.interval_minutes,
        daily_time=body.daily_time,
        timezone=body.timezone,
        notes=body.notes,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    await automation_service.reschedule_job(job.job_id)
    snapshot = await automation_service.snapshot_async()
    state_by_job = {item["job_id"]: item for item in snapshot["jobs"]}
    return _automation_job_out(job, state_by_job.get(job.job_id))


@router.put("/sources/automation/jobs/{job_id}", response_model=AutomationJobOut)
async def update_automation_job(job_id: str, body: AutomationJobUpdate, db: AsyncSession = Depends(get_db)):
    await automation_service.ensure_storage()
    job = (
        await db.execute(select(AutomationJob).where(AutomationJob.job_id == job_id))
    ).scalar_one_or_none()
    if not job:
        raise HTTPException(404, f"Automation job not found: {job_id}")

    updates = body.model_dump(exclude_unset=True)
    next_interval = updates["interval_minutes"] if "interval_minutes" in updates else job.interval_minutes
    next_daily = updates["daily_time"] if "daily_time" in updates else job.daily_time
    _validate_automation_schedule(next_interval, next_daily)

    next_country_code = (
        str(updates.get("country_code", job.country_code) or "").strip().upper()
        if ("country_code" in updates or job.country_code)
        else ""
    )
    _validate_country_automation_options(
        next_country_code,
        fill_missing=bool(updates.get("fill_missing", job.fill_missing)),
    )

    for field, value in updates.items():
        if field == "country_code":
            value = value.strip().upper()
        elif field in {"source", "priority"} and isinstance(value, str):
            value = value.strip().lower()
            if field == "source":
                value = canonicalize_task_source(value, country_code=next_country_code)
        elif field in {"name", "timezone", "notes"} and isinstance(value, str):
            value = value.strip()
        setattr(job, field, value)

    await db.commit()
    await db.refresh(job)
    await automation_service.reschedule_job(job.job_id)
    snapshot = await automation_service.snapshot_async()
    state_by_job = {item["job_id"]: item for item in snapshot["jobs"]}
    return _automation_job_out(job, state_by_job.get(job.job_id))


@router.delete("/sources/automation/jobs/{job_id}")
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


@router.post("/sources/automation/jobs/{job_id}/run", response_model=AutomationTriggerResult, status_code=202)
async def run_automation_job(job_id: str):
    try:
        result = await automation_service.trigger_job(job_id, manual=True)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return AutomationTriggerResult(**result)


def _validate_automation_schedule(interval_minutes: int | None, daily_time: str | None) -> None:
    if interval_minutes is None and not daily_time:
        raise HTTPException(400, "Automation job requires either interval_minutes or daily_time")
    if interval_minutes is not None and interval_minutes <= 0:
        raise HTTPException(400, "interval_minutes must be greater than 0")
    if daily_time:
        parts = daily_time.split(":")
        if len(parts) != 2:
            raise HTTPException(400, "daily_time must be in HH:MM format")
        try:
            hour = int(parts[0])
            minute = int(parts[1])
        except ValueError as exc:
            raise HTTPException(400, "daily_time must be numeric HH:MM") from exc
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise HTTPException(400, "daily_time must be a valid HH:MM value")


def _automation_job_out(job: AutomationJob, state: dict | None) -> AutomationJobOut:
    state = state or {}
    return AutomationJobOut(
        job_id=job.job_id,
        name=job.name,
        country_code=job.country_code,
        source=canonicalize_task_source(job.source, country_code=job.country_code),
        enabled=job.enabled,
        priority=job.priority,
        process=job.process,
        save_raw=job.save_raw,
        fill_missing=job.fill_missing,
        force=job.force,
        include_current_month=job.include_current_month,
        revision_window_months=job.revision_window_months,
        retry_threshold=job.retry_threshold,
        interval_minutes=job.interval_minutes,
        daily_time=job.daily_time,
        timezone=job.timezone,
        notes=job.notes,
        next_run_at=state.get("next_run_at"),
        last_started_at=state.get("last_started_at"),
        last_finished_at=state.get("last_finished_at"),
        last_status=state.get("last_status", "idle"),
        last_error=state.get("last_error"),
        last_task_uuid=state.get("last_task_uuid"),
        run_count=state.get("run_count", 0),
        skipped_count=state.get("skipped_count", 0),
    )
