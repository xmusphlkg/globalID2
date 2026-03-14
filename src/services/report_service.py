"""
Report Service

Encapsulates all business logic for AI-driven report generation,
decoupling it from the CLI layer in main.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import func, select

from src.core import get_database, get_logger
from src.core.task_manager import task_manager
from src.domain import (
    Country,
    DiseaseRecord,
    Report,
    ReportStatus,
    ReportType,
    Task,
)

logger = get_logger(__name__)


@dataclass
class ReportResult:
    report_id: int
    status: str
    output_files: List[str] = field(default_factory=list)
    sections_count: int = 0
    reused: bool = False


class ReportService:
    """Orchestrates the multi-phase AI report-generation pipeline."""

    async def execute(
        self,
        task: Task,
        country_code: str,
        report_type: str,
        period_start_iso: Optional[str],
        period_end_iso: Optional[str],
        days: int,
        enable_review: bool,
        send_email: bool,
    ) -> ReportResult:
        """
        Run the full report-generation pipeline and return a summary.

        Progress is reported via task_manager (0 → 100 %).
        Raises on unrecoverable errors (caller handles via task_lifecycle).
        """
        from src.ai.model_check import ensure_available_models_checked_async
        from src.generation import ReportGenerator

        # Check model availability once
        await ensure_available_models_checked_async()

        async with get_database() as db:
            # ── Resolve effective time range ──────────────────────────────────
            period_end = (
                datetime.fromisoformat(period_end_iso).replace(tzinfo=timezone.utc)
                if period_end_iso
                else datetime.now(timezone.utc)
            )
            period_start = (
                datetime.fromisoformat(period_start_iso).replace(tzinfo=timezone.utc)
                if period_start_iso
                else period_end - timedelta(days=days)
            )

            # ── Phase 0: Try to reuse an already-approved report ─────────────
            country_obj = await self._get_country(db, country_code)
            report_type_enum = ReportType[report_type.upper()]

            reuse = await self._find_reusable_report(
                db, country_obj, report_type_enum, period_start, period_end
            )
            if reuse:
                output_files = self._collect_output_files(reuse)
                await task_manager.add_workbook_entry(
                    task.task_uuid,
                    entry_type="success",
                    title="Report Reused",
                    content=(
                        f"Reused approved report #{reuse.id} for "
                        f"{country_code} {report_type} "
                        f"({period_start.date()} → {period_end.date()})"
                    ),
                    content_type="text",
                )
                await task_manager.update_task_progress(task.task_uuid, 100)
                return ReportResult(
                    report_id=reuse.id,
                    status=str(reuse.status),
                    output_files=output_files,
                    sections_count=len(reuse.sections) if hasattr(reuse, "sections") else 0,
                    reused=True,
                )

            # ── Phase 1: Data preparation (0 → 15 %) ─────────────────────────
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="Phase 1/5: Data Preparation",
                content="Fetching country and disease records...",
                content_type="text",
            )
            await task_manager.update_task_progress(task.task_uuid, 5)

            period_start, period_end = await self._adjust_period_to_available_data(
                db, task, country_obj, period_start, period_end, days
            )

            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="success",
                title="Data Preparation Complete",
                content=(
                    f"Country: {country_obj.name}\n"
                    f"Period: {period_start.date()} to {period_end.date()}"
                ),
                content_type="text",
            )
            await task_manager.update_task_progress(task.task_uuid, 15)

            # ── Phase 2: AI analysis + content generation (15 → 80 %) ────────
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="Phase 2/5: AI Analysis & Generation",
                content="Starting multi-agent AI workflow (Analyst → Writer → Reviewer)...",
                content_type="text",
            )

            generator = ReportGenerator()

            async def _ai_progress(stage, current, total, message):
                stage_base = {
                    "data_extraction": 15,
                    "analysis": 40,
                    "writing": 60,
                    "review": 75,
                }
                base = stage_base.get(stage, 15)
                pct = base + int((current / total) * 10) if total > 0 else base
                await task_manager.update_task_progress(task.task_uuid, min(pct, 80))
                if current % 5 == 0 or current == total:
                    await task_manager.add_workbook_entry(
                        task.task_uuid,
                        entry_type="info",
                        title=f"{stage.replace('_', ' ').title()} Progress",
                        content=f"{message} ({current}/{total})",
                        content_type="text",
                    )

            report = await generator.generate(
                country_id=country_obj.id,
                report_type=report_type_enum,
                period_start=period_start,
                period_end=period_end,
                send_email=send_email,
                enable_review=enable_review,
                progress_callback=_ai_progress,
                db=db,
            )

            if report.status == ReportStatus.FAILED:
                raise RuntimeError(
                    f"Report generation failed: {report.error_message or 'Unknown error'}"
                )

            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="success",
                title="AI Generation Complete",
                content=f"Report ID: {report.id}",
                content_type="text",
            )
            await task_manager.update_task_progress(task.task_uuid, 80)

            # ── Phase 3: Format & export (80 → 90 %) ─────────────────────────
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="Phase 3/5: Format & Export",
                content="Generating Markdown / HTML / PDF outputs...",
                content_type="text",
            )
            output_files = self._collect_output_files(report)
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="success",
                title="Export Complete",
                content="\n".join(output_files) if output_files else "No files exported",
                content_type="text",
            )
            await task_manager.update_task_progress(task.task_uuid, 90)

            # ── Phase 4: Email (90 → 95 %) ────────────────────────────────────
            if send_email:
                await task_manager.add_workbook_entry(
                    task.task_uuid,
                    entry_type="success",
                    title="Email Sent",
                    content="Report successfully delivered",
                    content_type="text",
                )
            await task_manager.update_task_progress(task.task_uuid, 95)

            # ── Phase 5: Finalise ─────────────────────────────────────────────
            sections_count = len(report.sections) if hasattr(report, "sections") else 0
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="success",
                title="Report Generation Completed",
                content=f"Report ID: {report.id}\nStatus: {report.status}\nFiles: {len(output_files)}",
                content_type="text",
            )
            await task_manager.update_task_progress(task.task_uuid, 100)

            return ReportResult(
                report_id=report.id,
                status=str(report.status),
                output_files=output_files,
                sections_count=sections_count,
            )

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _get_country(self, db, country_code: str) -> Country:
        result = await db.execute(select(Country).where(Country.code == country_code))
        country = result.scalar_one_or_none()
        if not country:
            raise ValueError(f"Country not found: {country_code}")
        return country

    async def _find_reusable_report(
        self,
        db,
        country: Country,
        report_type: ReportType,
        period_start: datetime,
        period_end: datetime,
    ) -> Optional[Report]:
        try:
            q = (
                select(Report)
                .where(
                    Report.country_id == country.id,
                    Report.period_start == period_start,
                    Report.period_end == period_end,
                    Report.report_type == report_type,
                    Report.status == ReportStatus.APPROVED,
                )
                .order_by(Report.created_at.desc())
                .limit(1)
            )
            return (await db.execute(q)).scalar_one_or_none()
        except Exception as e:
            logger.warning(f"Failed to check for reusable report: {e}")
            return None

    async def _adjust_period_to_available_data(
        self,
        db,
        task: Task,
        country: Country,
        period_start: datetime,
        period_end: datetime,
        days: int,
    ) -> tuple[datetime, datetime]:
        """If no data exists in the requested range, shift to the latest available."""
        count_q = select(func.count()).select_from(DiseaseRecord).where(
            DiseaseRecord.country_id == country.id,
            DiseaseRecord.time >= period_start,
            DiseaseRecord.time <= period_end,
        )
        data_count = (await db.execute(count_q)).scalar()

        if data_count and data_count > 0:
            return period_start, period_end

        latest_q = select(func.max(DiseaseRecord.time)).where(
            DiseaseRecord.country_id == country.id
        )
        latest_date = (await db.execute(latest_q)).scalar()

        if not latest_date:
            raise ValueError(
                f"No disease data found for {country.name}. Please run crawl first."
            )

        if latest_date.tzinfo is None:
            latest_date = latest_date.replace(tzinfo=timezone.utc)
        adjusted_end = latest_date
        adjusted_start = adjusted_end - timedelta(days=days)

        msg = (
            "No data in requested period. Using latest available data: "
            f"{adjusted_start.date()} to {adjusted_end.date()}"
        )
        logger.warning(msg)
        await task_manager.add_workbook_entry(
            task.task_uuid,
            entry_type="warning",
            title="Time Range Adjusted",
            content=msg,
            content_type="text",
        )
        return adjusted_start, adjusted_end

    @staticmethod
    def _collect_output_files(report: Report) -> List[str]:
        files = []
        if getattr(report, "markdown_path", None):
            files.append(f"Markdown: {report.markdown_path}")
        if getattr(report, "html_path", None):
            files.append(f"HTML: {report.html_path}")
        if getattr(report, "pdf_path", None):
            files.append(f"PDF: {report.pdf_path}")
        return files
