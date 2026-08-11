"""Reports router – list, detail, sections, conversations, AI insights."""

from collections import defaultdict
from datetime import datetime
from typing import Any, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..deps import get_db
from ..enum_utils import parse_enum_member
from ..schemas.report import (
    AIConversationOut,
    AIInteractionOut,
    AIInteractionSummaryOut,
    ReportDetailOut,
    ReportOut,
    ReportSectionOut,
    ReportSectionRunOut,
)
from src.domain.country import Country
from src.domain.report import AIConversation, Report, ReportSection, ReportSectionRun, ReportStatus, ReportType
from src.domain.task import Task

router = APIRouter()


def _to_float(value) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_total_tokens(tokens: Optional[dict]) -> int:
    if not isinstance(tokens, dict):
        return 0

    for key in ("total", "total_tokens", "sum"):
        v = _to_float(tokens.get(key))
        if v is not None:
            return int(v)

    total = 0
    for v in tokens.values():
        num = _to_float(v)
        if num is not None:
            total += int(num)
    return total


def _extract_quality_overall(quality_scores: Optional[dict]) -> Optional[float]:
    if not isinstance(quality_scores, dict):
        return None

    for key in ("overall", "total", "quality", "score"):
        v = _to_float(quality_scores.get(key))
        if v is not None:
            return v
    return None


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _maybe_report_type(raw: Any) -> ReportType | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return parse_enum_member(ReportType, raw, "report_type")
    except HTTPException:
        return None


def _parse_task_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


async def _resolve_task_report_id(task: Task, db: AsyncSession) -> Optional[int]:
    if task.report_id is not None:
        return task.report_id
    if task.country_id is None:
        return None

    output_data = task.output_data if isinstance(task.output_data, dict) else {}
    output_report_id = output_data.get("report_id")
    if isinstance(output_report_id, int):
        task.report_id = output_report_id
        await db.commit()
        return output_report_id

    output_report_uuid = output_data.get("report_uuid")
    if isinstance(output_report_uuid, str) and output_report_uuid.strip():
        report_id = (
            await db.execute(select(Report.id).where(Report.report_uuid == output_report_uuid.strip()))
        ).scalar_one_or_none()
        if report_id is not None:
            task.report_id = report_id
            await db.commit()
            return report_id

    input_data = task.input_data if isinstance(task.input_data, dict) else {}
    report_type = _maybe_report_type(input_data.get("report_type"))
    period_start = _parse_task_datetime(input_data.get("period_start"))
    period_end = _parse_task_datetime(input_data.get("period_end"))

    candidate_queries = []

    exact_q = select(Report).where(Report.country_id == task.country_id)
    has_exact_filters = False
    if report_type is not None:
        exact_q = exact_q.where(Report.report_type == report_type)
        has_exact_filters = True
    if period_start is not None:
        exact_q = exact_q.where(Report.period_start == period_start)
        has_exact_filters = True
    if period_end is not None:
        exact_q = exact_q.where(Report.period_end == period_end)
        has_exact_filters = True
    if has_exact_filters:
        candidate_queries.append(exact_q)

    if report_type is not None:
        candidate_queries.append(
            select(Report).where(
                Report.country_id == task.country_id,
                Report.report_type == report_type,
            )
        )

    candidate_queries.append(select(Report).where(Report.country_id == task.country_id))

    report = None
    for query in candidate_queries:
        report = (
            await db.execute(
                query.order_by(Report.updated_at.desc(), Report.created_at.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if report is not None:
            break

    if report is None:
        return None

    task.report_id = report.id
    await db.commit()
    return report.id


@router.get("/reports", response_model=List[ReportOut])
async def list_reports(
    response: Response,
    country_code: Optional[str] = Query(None, min_length=2, max_length=10),
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    q = (
        select(
            Report,
            Country.name.label("country_name"),
            func.count(ReportSection.id).label("section_count"),
        )
        .join(Country, Report.country_id == Country.id)
        .outerjoin(ReportSection, ReportSection.report_id == Report.id)
        .group_by(Report.id, Country.name)
        .order_by(Report.created_at.desc())
    )

    count_q = select(func.count()).select_from(Report).join(Country, Report.country_id == Country.id)

    if country_code:
        q = q.where(func.upper(Country.code) == country_code.strip().upper())
        count_q = count_q.where(func.upper(Country.code) == country_code.strip().upper())
    if status:
        status_value = parse_enum_member(ReportStatus, status, "status")
        q = q.where(Report.status == status_value)
        count_q = count_q.where(Report.status == status_value)

    total = int((await db.execute(count_q)).scalar_one() or 0)
    offset = (page - 1) * page_size
    rows = (await db.execute(q.offset(offset).limit(page_size))).all()
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Limit"] = str(page_size)
    response.headers["X-Offset"] = str(offset)

    disease_map: dict[int, list[str]] = {}
    report_ids = [r.Report.id for r in rows]
    if report_ids:
        run_rows = (
            await db.execute(
                select(ReportSectionRun.report_id, ReportSectionRun.disease_name)
                .where(ReportSectionRun.report_id.in_(report_ids))
            )
        ).all()
        grouped_diseases: dict[int, set[str]] = defaultdict(set)
        for report_id, disease_name in run_rows:
            normalized = (disease_name or "").strip()
            if normalized:
                grouped_diseases[int(report_id)].add(normalized)
        disease_map = {
            report_id: sorted(list(names))
            for report_id, names in grouped_diseases.items()
        }

    for row in rows:
        report_id = row.Report.id
        if disease_map.get(report_id):
            continue
        metadata = row.Report.metadata_ if isinstance(row.Report.metadata_, dict) else {}
        cards = metadata.get("disease_cards") if isinstance(metadata, dict) else None
        if isinstance(cards, list):
            names = [
                str(card.get("name_en") or card.get("name_zh") or card.get("disease_id")).strip()
                for card in cards[:20]
                if isinstance(card, dict) and str(card.get("name_en") or card.get("name_zh") or card.get("disease_id") or "").strip()
            ]
            if names:
                disease_map[report_id] = sorted(set(names))

    return [
        ReportOut(
            id=r.Report.id,
            report_uuid=str(r.Report.report_uuid),
            title=r.Report.title,
            report_type=r.Report.report_type,
            status=r.Report.status,
            country_id=r.Report.country_id,
            country_name=r.country_name,
            period_start=r.Report.period_start,
            period_end=r.Report.period_end,
            quality_score=r.Report.quality_score,
            generation_time=r.Report.generation_time,
            section_count=r.section_count,
            primary_disease=(disease_map.get(r.Report.id) or [None])[0],
            disease_names=disease_map.get(r.Report.id, []),
            created_at=r.Report.created_at,
        )
        for r in rows
    ]


@router.get("/reports/{report_uuid}", response_model=ReportDetailOut)
async def get_report(report_uuid: UUID, db: AsyncSession = Depends(get_db)):
    q = (
        select(Report, Country.name.label("country_name"))
        .join(Country, Report.country_id == Country.id)
        .where(Report.report_uuid == report_uuid)
    )
    row = (await db.execute(q)).one_or_none()
    if not row:
        raise HTTPException(404, "Report not found")

    report = row.Report
    metadata = report.metadata_ if isinstance(report.metadata_, dict) else {}
    # Eager-load sections
    sec_q = (
        select(ReportSection)
        .where(ReportSection.report_id == report.id)
        .order_by(ReportSection.section_order)
    )
    sections = (await db.execute(sec_q)).scalars().all()

    return ReportDetailOut(
        id=report.id,
        report_uuid=str(report.report_uuid),
        title=report.title,
        report_type=report.report_type,
        status=report.status,
        country_id=report.country_id,
        country_name=row.country_name,
        period_start=report.period_start,
        period_end=report.period_end,
        summary=report.summary,
        key_findings=report.key_findings or [],
        recommendations=report.recommendations or [],
        quality_score=report.quality_score,
        generation_time=report.generation_time,
        token_usage=report.token_usage,
        ai_model_used=report.ai_model_used,
        html_path=report.html_path,
        pdf_path=report.pdf_path,
        markdown_path=report.markdown_path,
        error_message=report.error_message,
        metadata=metadata,
        analysis_summary=metadata.get("analysis_summary") if isinstance(metadata.get("analysis_summary"), dict) else None,
        quality_gate=metadata.get("quality_gate") if isinstance(metadata.get("quality_gate"), dict) else None,
        data_quality=metadata.get("data_quality") if isinstance(metadata.get("data_quality"), dict) else None,
        method_version=metadata.get("method_version"),
        created_at=report.created_at,
        sections=[
            ReportSectionOut(
                id=s.id,
                section_type=s.section_type,
                section_order=s.section_order,
                title=s.title,
                content=s.content,
                ai_model=s.ai_model,
                generation_time=s.generation_time,
                data_sources=s.data_sources,
                charts=s.charts,
                metadata=s.metadata_ if isinstance(s.metadata_, dict) else {},
                created_at=s.created_at,
            )
            for s in sections
        ],
    )


@router.get("/reports/{report_uuid}/runs", response_model=List[ReportSectionRunOut])
async def get_report_runs(report_uuid: UUID, db: AsyncSession = Depends(get_db)):
    report_q = select(Report.id).where(Report.report_uuid == report_uuid)
    report_id = (await db.execute(report_q)).scalar_one_or_none()
    if report_id is None:
        raise HTTPException(404, "Report not found")

    q = (
        select(ReportSectionRun)
        .where(ReportSectionRun.report_id == report_id)
        .order_by(ReportSectionRun.created_at)
    )
    runs = (await db.execute(q)).scalars().all()
    return [
        ReportSectionRunOut(
            id=run.id,
            run_uuid=run.run_uuid,
            section_id=run.section_id,
            disease_name=run.disease_name,
            section_type=run.section_type,
            status=run.status,
            model=run.model,
            provider=run.provider,
            temperature=run.temperature,
            max_tokens=run.max_tokens,
            token_usage=run.token_usage,
            quality_scores=run.quality_scores,
            revision_count=run.revision_count,
            error_message=run.error_message,
            started_at=run.started_at,
            ended_at=run.ended_at,
            metadata=run.metadata_,
            created_at=run.created_at,
        )
        for run in runs
    ]


@router.get(
    "/reports/{report_uuid}/sections/{section_key}/conversations",
    response_model=List[AIConversationOut],
)
async def get_section_conversations(
    report_uuid: str,
    section_key: str,
    db: AsyncSession = Depends(get_db),
):
    # Validate report exists
    report_q = select(Report.id).where(Report.report_uuid == report_uuid)
    report_id = (await db.execute(report_q)).scalar_one_or_none()
    if report_id is None:
        raise HTTPException(404, "Report not found")

    section_id = (
        await db.execute(
            select(ReportSection.id).where(
                ReportSection.report_id == report_id,
                ReportSection.section_type == section_key,
            )
        )
    ).scalar_one_or_none()
    if section_id is None:
        raise HTTPException(404, "Report section not found")

    # Find matching run(s) for this stable section type.
    run_q = select(ReportSectionRun.id).where(
        ReportSectionRun.report_id == report_id,
        ReportSectionRun.section_id == section_id,
    )
    run_ids = (await db.execute(run_q)).scalars().all()
    if not run_ids:
        return []

    q = (
        select(AIConversation)
        .where(AIConversation.run_id.in_(run_ids))
        .order_by(AIConversation.timestamp)
    )
    return (await db.execute(q)).scalars().all()


@router.get("/ai/interactions", response_model=List[AIInteractionOut])
async def list_ai_interactions(
    response: Response,
    country_code: Optional[str] = Query(None, min_length=2, max_length=10),
    task_uuid: Optional[str] = Query(None),
    report_uuid: Optional[str] = Query(None),
    agent: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    disease: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    task = None
    report_id = None
    if task_uuid:
        task = (
            await db.execute(select(Task).where(Task.task_uuid == task_uuid))
        ).scalar_one_or_none()
        if task is None:
            raise HTTPException(404, "Task not found")
        report_id = await _resolve_task_report_id(task, db)

    if report_uuid:
        report_id = (
            await db.execute(select(Report.id).where(Report.report_uuid == report_uuid))
        ).scalar_one_or_none()
        if report_id is None:
            raise HTTPException(404, "Report not found")

    if task_uuid and report_id is None:
        response.headers["X-Total-Count"] = "0"
        response.headers["X-Limit"] = str(page_size)
        response.headers["X-Offset"] = str((page - 1) * page_size)
        return []

    q = (
        select(AIConversation, ReportSectionRun, Report, ReportSection)
        .join(ReportSectionRun, AIConversation.run_id == ReportSectionRun.id)
        .join(Report, AIConversation.report_id == Report.id)
        .outerjoin(ReportSection, AIConversation.section_id == ReportSection.id)
        .order_by(AIConversation.timestamp.desc())
    )

    if country_code:
        q = q.where(
            Report.country_id.in_(
                select(Country.id).where(func.upper(Country.code) == country_code.strip().upper())
            )
        )
    if report_id is not None:
        q = q.where(AIConversation.report_id == report_id)
    if agent:
        q = q.where(AIConversation.agent == agent)
    if model:
        q = q.where(AIConversation.model == model)
    if disease:
        q = q.where(ReportSectionRun.disease_name.ilike(f"%{disease}%"))

    count_q = select(func.count()).select_from(q.order_by(None).subquery())
    total = int((await db.execute(count_q)).scalar_one() or 0)
    offset = (page - 1) * page_size
    rows = (await db.execute(q.offset(offset).limit(page_size))).all()
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Limit"] = str(page_size)
    response.headers["X-Offset"] = str(offset)
    return [
        AIInteractionOut(
            id=row.AIConversation.id,
            task_uuid=task.task_uuid if task else None,
            task_name=task.task_name if task else None,
            task_status=_enum_value(task.status) if task and task.status is not None else None,
            report_id=row.Report.id,
            report_uuid=str(row.Report.report_uuid),
            report_status=row.Report.status,
            report_title=row.Report.title,
            country_id=row.Report.country_id,
            section_id=row.AIConversation.section_id,
            section_type=row.ReportSectionRun.section_type,
            section_title=row.ReportSection.title if row.ReportSection else None,
            disease_name=row.ReportSectionRun.disease_name,
            run_id=row.ReportSectionRun.id,
            run_uuid=row.ReportSectionRun.run_uuid,
            run_status=row.ReportSectionRun.status,
            run_model=row.ReportSectionRun.model,
            run_provider=row.ReportSectionRun.provider,
            run_temperature=row.ReportSectionRun.temperature,
            agent=row.AIConversation.agent,
            role=row.AIConversation.role,
            timestamp=row.AIConversation.timestamp,
            model=row.AIConversation.model,
            provider=row.AIConversation.provider,
            tokens=row.AIConversation.tokens,
            total_tokens=_extract_total_tokens(row.AIConversation.tokens),
            duration=row.AIConversation.duration,
            quality_scores=row.ReportSectionRun.quality_scores,
            quality_overall=_extract_quality_overall(row.ReportSectionRun.quality_scores),
            system_prompt=row.AIConversation.system_prompt,
            prompt=row.AIConversation.prompt,
            response=row.AIConversation.response,
            temperature=row.AIConversation.temperature,
        )
        for row in rows
    ]


@router.get("/ai/interactions/summary", response_model=AIInteractionSummaryOut)
async def get_ai_interactions_summary(
    country_code: Optional[str] = Query(None, min_length=2, max_length=10),
    task_uuid: Optional[str] = Query(None),
    report_uuid: Optional[str] = Query(None),
    agent: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    disease: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    task = None
    report_id = None
    if task_uuid:
        task = (
            await db.execute(select(Task).where(Task.task_uuid == task_uuid))
        ).scalar_one_or_none()
        if task is None:
            raise HTTPException(404, "Task not found")
        report_id = await _resolve_task_report_id(task, db)

    if report_uuid:
        report_id = (
            await db.execute(select(Report.id).where(Report.report_uuid == report_uuid))
        ).scalar_one_or_none()
        if report_id is None:
            raise HTTPException(404, "Report not found")

    if task_uuid and report_id is None:
        return AIInteractionSummaryOut(
            total_interactions=0,
            total_tokens=0,
            avg_tokens=0,
            avg_duration=0,
            avg_quality=None,
            by_agent={},
            by_model={},
            task_uuid=task.task_uuid if task else None,
        )

    q = (
        select(AIConversation, ReportSectionRun, Report)
        .join(ReportSectionRun, AIConversation.run_id == ReportSectionRun.id)
        .join(Report, AIConversation.report_id == Report.id)
    )

    if country_code:
        q = q.where(
            Report.country_id.in_(
                select(Country.id).where(func.upper(Country.code) == country_code.strip().upper())
            )
        )
    if report_id is not None:
        q = q.where(AIConversation.report_id == report_id)
    if agent:
        q = q.where(AIConversation.agent == agent)
    if model:
        q = q.where(AIConversation.model == model)
    if disease:
        q = q.where(ReportSectionRun.disease_name.ilike(f"%{disease}%"))

    rows = (await db.execute(q)).all()

    if not rows:
        return AIInteractionSummaryOut(
            total_interactions=0,
            total_tokens=0,
            avg_tokens=0,
            avg_duration=0,
            avg_quality=None,
            by_agent={},
            by_model={},
            task_uuid=task.task_uuid if task else None,
        )

    total_tokens = 0
    durations: list[float] = []
    qualities: list[float] = []
    by_agent: dict[str, int] = {}
    by_model: dict[str, int] = {}

    for row in rows:
        total_tokens += _extract_total_tokens(row.AIConversation.tokens)

        duration = _to_float(row.AIConversation.duration)
        if duration is not None:
            durations.append(duration)

        quality = _extract_quality_overall(row.ReportSectionRun.quality_scores)
        if quality is not None:
            qualities.append(quality)

        agent_name = row.AIConversation.agent or "unknown"
        model_name = row.AIConversation.model or "unknown"
        by_agent[agent_name] = by_agent.get(agent_name, 0) + 1
        by_model[model_name] = by_model.get(model_name, 0) + 1

    total = len(rows)
    return AIInteractionSummaryOut(
        total_interactions=total,
        total_tokens=total_tokens,
        avg_tokens=total_tokens / total if total else 0,
        avg_duration=(sum(durations) / len(durations)) if durations else 0,
        avg_quality=(sum(qualities) / len(qualities)) if qualities else None,
        by_agent=by_agent,
        by_model=by_model,
        task_uuid=task.task_uuid if task else None,
    )
