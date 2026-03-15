"""Sources router – data flow pipeline view per country."""

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_db
from ..schemas.sources import DataSourceFlow, StageInfo
from src.domain.disease_record import DiseaseRecord
from src.domain.task import Task, TaskType, TaskWorkbook

router = APIRouter()

# Real data-ingestion stages implemented in src/data
DATA_PIPELINE_STAGES = [
    "fetch_list",
    "incremental_check",
    "process_store",
    "finalize",
]


def _task_status(task: Optional[Task]) -> Optional[str]:
    if task is None:
        return None
    status = task.status
    return status.value if hasattr(status, "value") else str(status)


def _scope_from_data_source(data_source: Optional[str]) -> str:
    """Map record data_source text to canonical scope key."""
    text = (data_source or "").strip().lower()

    # Exact known labels from current CN dataset.
    exact = {
        "china cdc: notifiable infectious diseases reports": "cdc_weekly",
        "china cdc weekly: notifiable infectious diseases reports": "cdc_weekly",
        "gov data": "nhc",
        "pubmed": "pubmed",
    }
    if text in exact:
        return exact[text]

    # Fallback heuristics for historical / future labels.
    if "pubmed" in text:
        return "pubmed"
    if "gov" in text or "ndcpa" in text or "卫健" in text or "疾控局" in text:
        return "nhc"
    if "cdc" in text or "weekly" in text:
        return "cdc_weekly"
    return "all"


def _canonical_data_source_label(data_source: Optional[str]) -> str:
    """Normalize display label to avoid duplicate cards caused by casing variants."""
    text = (data_source or "").strip().lower()
    if text == "gov data":
        return "GOV Data"
    return data_source or "Unknown"


def _scope_display_label(scope: str) -> str:
    if scope == "pubmed":
        return "PubMed"
    if scope == "nhc":
        return "GOV Data"
    if scope == "cdc_weekly":
        return "China CDC Weekly"
    return "All Sources"


def _scope_from_task(task: Task) -> str:
    """Infer task scope from task input_data/task_name."""
    inp = task.input_data or {}
    source = (inp.get("source") or "").strip().lower() if isinstance(inp, dict) else ""
    if source == "gov":
        source = "nhc"
    if source in {"cdc_weekly", "nhc", "pubmed", "all"}:
        return source

    # Backward compatibility: old tasks only stored data_source text.
    if isinstance(inp, dict) and inp.get("data_source"):
        return _scope_from_data_source(str(inp.get("data_source")))

    name = (task.task_name or "").lower()
    if "pubmed" in name:
        return "pubmed"
    if "gov" in name or "nhc" in name:
        return "nhc"
    if "cdc" in name or "weekly" in name:
        return "cdc_weekly"
    return "all"


def _source_from_task(task: Task) -> str:
    inp = task.input_data or {}
    source = (inp.get("source") or "").strip().lower() if isinstance(inp, dict) else ""
    if not source and isinstance(inp, dict) and inp.get("data_source"):
        source = _scope_from_data_source(str(inp.get("data_source")))
    if source == "gov":
        source = "nhc"
    return source or "all"


def _pick_latest_crawl_task(
    latest_by_scope: Dict[str, Task],
    *,
    scope: str,
) -> Optional[Task]:
    """Get latest crawl task by exact scope first, then country-wide ('all')."""
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


@router.get("/sources/flow", response_model=List[DataSourceFlow])
async def get_sources_flow(
    country_id: int = Query(..., ge=1, description="Country ID"),
    db: AsyncSession = Depends(get_db),
):
    """Return ingestion flow status per data source for the given country."""

    # 1. Distinct data sources + basic stats from disease_records
    src_q = (
        select(
            DiseaseRecord.data_source,
            func.count().label("record_count"),
            func.max(DiseaseRecord.time).label("latest_date"),
        )
        .where(DiseaseRecord.country_id == country_id)
        .group_by(DiseaseRecord.data_source)
        .order_by(func.count().desc())
    )
    src_rows = (await db.execute(src_q)).all()

    if not src_rows:
        return []

    # Normalize/merge rows by canonical source scope (not raw display text).
    merged_sources: Dict[str, Dict[str, object]] = {}
    for row in src_rows:
        label = _canonical_data_source_label(row.data_source)
        scope = _scope_from_data_source(label)
        display_label = _scope_display_label(scope) if scope != "all" else label

        prev = merged_sources.get(scope)
        if prev is None:
            merged_sources[scope] = {
                "scope": scope,
                "data_source": display_label,
                "record_count": int(row.record_count or 0),
                "latest_date": row.latest_date,
            }
            continue

        prev["record_count"] = int(prev["record_count"]) + int(row.record_count or 0)
        prev_latest = prev.get("latest_date")
        if (prev_latest is None) or (row.latest_date and row.latest_date > prev_latest):
            prev["latest_date"] = row.latest_date

    # 2. Gather most-recent crawl tasks and index them by source scope.
    task_q = (
        select(Task)
        .where(
            Task.country_id == country_id,
            Task.task_type == TaskType.CRAWL_DATA,
        )
        .order_by(Task.created_at.desc())
    )
    tasks = (await db.execute(task_q)).scalars().all()

    # Index: scope -> latest crawl task (query already desc by created_at).
    latest_by_scope: Dict[str, Task] = {}
    for t in tasks:
        scope = _scope_from_task(t)
        if scope not in latest_by_scope:
            latest_by_scope[scope] = t

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
    represented_scopes = {str(item["scope"]) for item in merged_sources.values()}
    for scope in latest_by_scope.keys():
        if scope in {"all"} or scope in represented_scopes:
            continue
        label = _scope_display_label(scope)
        if scope not in merged_sources:
            merged_sources[scope] = {
                "scope": scope,
                "data_source": label,
                "record_count": 0,
                "latest_date": None,
            }

    # 3. Build flow objects
    result: List[DataSourceFlow] = []
    sorted_sources = sorted(
        merged_sources.values(),
        key=lambda item: (int(item["record_count"]), str(item["data_source"])),
        reverse=True,
    )

    for src in sorted_sources:
        row_scope = str(src["scope"])
        task = _pick_latest_crawl_task(latest_by_scope, scope=row_scope)
        workbook_entries = workbook_by_task_id.get(task.id, []) if task else []
        stages = _build_data_stages(task, workbook_entries)

        latest_task_time = None
        if task:
            latest_task_time = (
                task.completed_at.isoformat()
                if task.completed_at
                else (task.created_at.isoformat() if task.created_at else None)
            )

        result.append(
            DataSourceFlow(
                data_source=str(src["data_source"]),
                record_count=int(src["record_count"]),
                latest_date=(
                    src["latest_date"].strftime("%Y-%m-%d")
                    if src.get("latest_date")
                    else None
                ),
                latest_task_uuid=task.task_uuid if task else None,
                latest_task_source=_source_from_task(task) if task else None,
                latest_task_status=_task_status(task),
                latest_task_time=latest_task_time,
                stages=stages,
            )
        )

    return result
