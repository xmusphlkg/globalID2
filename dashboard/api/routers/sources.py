"""Sources router – data flow pipeline view per country."""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_db
from ..schemas.sources import DataSourceFlow, StageInfo
from src.domain.disease_record import DiseaseRecord
from src.domain.task import Task

router = APIRouter()

# Ordered pipeline stages: (stage label, task_type value)
PIPELINE_STAGES = [
    ("crawl", "crawl_data"),
    ("process", "process_data"),
    ("report", "generate_report"),
    ("export", "export_data"),
]


@router.get("/sources/flow", response_model=List[DataSourceFlow])
async def get_sources_flow(
    country_id: int = Query(..., ge=1, description="Country ID"),
    db: AsyncSession = Depends(get_db),
):
    """Return pipeline status for every data source that has records for the given country."""

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

    # 2. Gather most-recent task per (country, task_type) — one query, filtered by relevant types
    relevant_types = [tt for _, tt in PIPELINE_STAGES]
    task_q = (
        select(Task)
        .where(
            Task.country_id == country_id,
            Task.task_type.in_(relevant_types),
        )
        .order_by(Task.created_at.desc())
    )
    tasks = (await db.execute(task_q)).scalars().all()

    # Index: task_type -> latest Task (sorted desc already)
    latest_by_type: dict[str, Task] = {}
    for t in tasks:
        if t.task_type not in latest_by_type:
            latest_by_type[t.task_type] = t

    # 3. Build flow objects
    result: List[DataSourceFlow] = []
    for row in src_rows:
        stages: List[StageInfo] = []
        for stage_label, task_type in PIPELINE_STAGES:
            task = latest_by_type.get(task_type)
            stages.append(
                StageInfo(
                    stage=stage_label,
                    task_type=task_type,
                    status=task.status if task else None,
                    task_uuid=task.task_uuid if task else None,
                    task_name=task.task_name if task else None,
                    progress=task.progress or 0 if task else 0,
                    last_run=(
                        task.completed_at.isoformat()
                        if task and task.completed_at
                        else (task.created_at.isoformat() if task else None)
                    ),
                )
            )
        result.append(
            DataSourceFlow(
                data_source=row.data_source or "Unknown",
                record_count=row.record_count,
                latest_date=row.latest_date.strftime("%Y-%m-%d") if row.latest_date else None,
                stages=stages,
            )
        )

    return result
