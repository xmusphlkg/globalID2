"""Report generator facade for the v4 report pipeline."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from sqlalchemy import select

from src.core import get_config, get_database, get_logger, normalize_rate_columns, normalize_rate_value
from src.core.task_manager import task_manager
from src.domain import Country, DiseaseRecord, PopulationRecord, Report, ReportStatus, ReportType
from src.services.exceptions import TaskCancelledError

from .data_exporter import DataExporter
from .email_service import EmailService
from .formatter import ReportFormatter
from .report_v4 import ReportV4Context, ReportV4Pipeline

logger = get_logger(__name__)


class ReportGenerator:
    """Thin orchestration facade for report_v4.

    The legacy, structured, and analytical v3 layouts are intentionally no
    longer active. Callers must regenerate reports with the canonical v4
    document contract.
    """

    SUPPORTED_LAYOUT = "report_v4"

    def __init__(self):
        self.config = get_config()
        self.data_exporter = DataExporter()
        self.formatter = ReportFormatter()
        self.email_service = EmailService()
        self.output_dir = Path(self.config.app.base_dir) / self.config.report.output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        logger.info("ReportGenerator initialized for report_v4")

    async def generate(
        self,
        country_id: int,
        report_type: ReportType,
        period_start: datetime,
        period_end: datetime,
        diseases: Optional[list[int]] = None,
        db=None,
        **kwargs,
    ) -> Report:
        """Generate a v4 report for the requested country/window."""
        report_layout = str(kwargs.get("report_layout") or self.SUPPORTED_LAYOUT).strip().lower()
        if report_layout != self.SUPPORTED_LAYOUT:
            raise ValueError(
                f"Unsupported report_layout={report_layout!r}. "
                "Legacy report layouts were removed; regenerate as report_v4."
            )
        kwargs["report_layout"] = self.SUPPORTED_LAYOUT

        if db is None:
            async with get_database() as managed_db:
                return await self._generate_with_db(
                    managed_db, country_id, report_type, period_start, period_end, diseases, **kwargs
                )
        return await self._generate_with_db(
            db, country_id, report_type, period_start, period_end, diseases, **kwargs
        )

    async def _generate_with_db(
        self,
        db,
        country_id: int,
        report_type: ReportType,
        period_start: datetime,
        period_end: datetime,
        diseases: Optional[list[int]] = None,
        **kwargs,
    ) -> Report:
        progress_callback = kwargs.get("progress_callback")
        task_uuid = kwargs.get("task_uuid")
        existing_report = kwargs.get("existing_report")

        async def notify(stage: str, current: int, total: int, message: str) -> None:
            if progress_callback:
                await progress_callback(stage, current, total, message)

        report = existing_report or await self._create_report_record(
            db,
            country_id=country_id,
            report_type=report_type,
            period_start=period_start,
            period_end=period_end,
            **kwargs,
        )
        if existing_report is not None:
            report.status = ReportStatus.GENERATING
            report.error_message = None
            report.completed_at = None
            report.generation_config = self._generation_config(report, kwargs)
            await db.commit()
            await db.refresh(report)

        if task_uuid:
            await task_manager.link_task_report(task_uuid, report.id)

        try:
            await self._ensure_task_not_cancelled(task_uuid, report.id)
            await notify("data_extraction", 0, 2, "Extracting report-window disease data...")
            data = await self._extract_data(
                db,
                country_id=country_id,
                period_start=period_start,
                period_end=period_end,
                diseases=diseases,
            )
            if data.empty:
                report.status = ReportStatus.FAILED
                report.error_message = "No data available"
                await db.commit()
                return report

            history_days = int(kwargs.get("historical_context_days", 15 * 366))
            history_start = min(period_start, period_end - timedelta(days=history_days))
            historical_data = data
            if history_start < period_start:
                historical_data = await self._extract_data(
                    db,
                    country_id=country_id,
                    period_start=history_start,
                    period_end=period_end,
                    diseases=diseases,
                )
            await notify("data_extraction", 2, 2, f"Extracted {len(data)} records")

            await self._ensure_task_not_cancelled(task_uuid, report.id)
            await notify("analysis", 0, 3, "Building report_v4 evidence packet...")
            pipeline = ReportV4Pipeline(
                file_exporter=None,
            )
            sections = await pipeline.generate(
                ReportV4Context(
                    db=db,
                    report=report,
                    data=data,
                    historical_data=historical_data,
                    period_start=period_start,
                    period_end=period_end,
                    output_dir=self.output_dir,
                    raw_sources=[],
                )
            )
            await notify("analysis", 3, 3, "report_v4 quality gate passed")
            await notify("writing", len(sections), len(sections), f"Generated {len(sections)} v4 sections")

            if kwargs.get("export_data", True):
                await self._export_data(db, report, country_id, period_start, period_end)
            if kwargs.get("send_email", False):
                await self._send_email(report)

            report.status = ReportStatus.APPROVED
            report.completed_at = datetime.now(timezone.utc)
            await db.commit()
            logger.info("Report v4 generation completed: %s", report.id)
            return report

        except TaskCancelledError:
            await self._mark_failed(db, report, "Report generation cancelled")
            raise
        except Exception as exc:
            logger.error("Report v4 generation failed: %s", exc)
            await self._mark_failed(db, report, str(exc))
            raise

    async def _create_report_record(
        self,
        db,
        *,
        country_id: int,
        report_type: ReportType,
        period_start: datetime,
        period_end: datetime,
        **kwargs,
    ) -> Report:
        report = Report(
            country_id=country_id,
            report_type=report_type,
            title=kwargs.get("title") or f"{report_type.value} report_v4",
            status=ReportStatus.GENERATING,
            period_start=period_start,
            period_end=period_end,
            generation_config=self._generation_config(None, kwargs),
        )
        db.add(report)
        await db.commit()
        await db.refresh(report)
        return report

    @classmethod
    def _generation_config(cls, report: Report | None, kwargs: dict[str, Any]) -> dict[str, Any]:
        base = dict((report.generation_config if report is not None else None) or {})
        base.update(dict(kwargs.get("config") or {}))
        base["report_layout"] = cls.SUPPORTED_LAYOUT
        base["language"] = "zh"
        if kwargs.get("analysis_depth"):
            base["analysis_depth"] = kwargs.get("analysis_depth")
        if kwargs.get("quality_threshold") is not None:
            base["quality_threshold"] = kwargs.get("quality_threshold")
        base.pop("email_delivery", None)
        return base

    async def _extract_data(
        self,
        db,
        *,
        country_id: int,
        period_start: datetime,
        period_end: datetime,
        diseases: Optional[list[int]] = None,
    ) -> pd.DataFrame:
        query = select(DiseaseRecord).where(
            DiseaseRecord.country_id == country_id,
            DiseaseRecord.time >= period_start,
            DiseaseRecord.time <= period_end,
        )
        if diseases:
            query = query.where(DiseaseRecord.disease_id.in_(diseases))
        records = (await db.execute(query)).scalars().all()
        if not records:
            return pd.DataFrame()

        population_by_year: dict[int, PopulationRecord] = {}
        try:
            pop_rows = (
                await db.execute(
                    select(PopulationRecord).where(
                        PopulationRecord.country_id == country_id,
                        PopulationRecord.year >= int(period_start.year),
                        PopulationRecord.year <= int(period_end.year),
                    )
                )
            ).scalars().all()
            population_by_year = {int(row.year): row for row in pop_rows}
        except Exception as exc:
            logger.warning("Could not load population denominators: %s", exc)

        rows = []
        for record in records:
            incidence = self._incidence_fields(record, population_by_year)
            rows.append(
                {
                    "time": record.time,
                    "disease_id": record.disease_id,
                    "cases": record.cases,
                    "deaths": record.deaths,
                    "new_cases": record.new_cases,
                    "new_deaths": record.new_deaths,
                    "recoveries": record.recoveries,
                    **incidence,
                    "mortality_rate": record.mortality_rate,
                    "recovery_rate": record.recovery_rate,
                    "data_source": record.data_source,
                    "data_quality": record.data_quality,
                    "confidence_score": record.confidence_score,
                    "metadata": record.metadata_ or {},
                }
            )
        return normalize_rate_columns(pd.DataFrame(rows), copy=False)

    @staticmethod
    def _incidence_fields(record: DiseaseRecord, population_by_year: dict[int, PopulationRecord]) -> dict[str, Any]:
        raw_incidence = normalize_rate_value(record.incidence_rate)
        year = int(record.time.year) if record.time is not None else None
        population_record = population_by_year.get(year) if year is not None else None
        population = float(population_record.population) if population_record and population_record.population else None
        if raw_incidence is not None:
            return {
                "incidence_rate": raw_incidence,
                "incidence_rate_source": "original_db",
                "population_denominator": population,
                "population_year": year if population is not None else None,
                "population_source": population_record.source if population_record else None,
            }
        if population and population > 0 and record.cases is not None:
            return {
                "incidence_rate": round((float(record.cases) / population) * 100000.0, 6),
                "incidence_rate_source": "wpp_computed_crude",
                "population_denominator": population,
                "population_year": year,
                "population_source": population_record.source if population_record else None,
            }
        return {
            "incidence_rate": None,
            "incidence_rate_source": "missing_population",
            "population_denominator": population,
            "population_year": year if population is not None else None,
            "population_source": population_record.source if population_record else None,
        }

    async def _export_data(
        self,
        db,
        report: Report,
        country_id: int,
        period_start: datetime,
        period_end: datetime,
    ) -> None:
        country = (await db.execute(select(Country).where(Country.id == country_id))).scalar_one_or_none()
        if country is None:
            return
        exported = await self.data_exporter.export_all(
            country_code=country.code,
            period_start=period_start,
            period_end=period_end,
            formats=["csv", "json"],
        )
        report.metadata_ = {**(report.metadata_ or {}), "exported_data_files": exported}
        await db.commit()

    async def _send_email(self, report: Report) -> None:
        if not report.html_path:
            return
        html = Path(report.html_path).read_text(encoding="utf-8")
        delivery = self.email_service.send_report_to_settings_recipients(
            report_title=report.title,
            report_html=html,
            pdf_path=report.pdf_path,
        )
        report.generation_config = {
            **(report.generation_config or {}),
            "email_delivery": delivery,
        }

    async def _ensure_task_not_cancelled(self, task_uuid: str | None, report_id: int) -> None:
        if task_uuid and await task_manager.is_cancel_requested(task_uuid):
            raise TaskCancelledError(f"Report generation cancelled for report #{report_id}")

    @staticmethod
    async def _mark_failed(db, report: Report, message: str) -> None:
        try:
            await db.rollback()
        except Exception:
            pass
        report.status = ReportStatus.FAILED
        report.error_message = message
        report.completed_at = datetime.now(timezone.utc)
        db.add(report)
        await db.commit()

    @staticmethod
    def _normalize_report_language(language: Any) -> str:
        text = str(language or "zh").strip().lower()
        if text in {"en", "english"}:
            return "en"
        return "zh"

    @staticmethod
    def _is_bilingual_language(_language: Any) -> bool:
        return True

    @staticmethod
    def _is_zh_like_language(_language: Any) -> bool:
        return True
