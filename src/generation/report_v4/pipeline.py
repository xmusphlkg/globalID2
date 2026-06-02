"""Orchestration for report v4 generation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select

from src.core import get_logger
from src.domain import Country, Disease, Report

from .composer import compose_report_document
from .dataset import DatasetBuilder
from .evidence import build_evidence_packet
from .exporters import ReportV4FileExporter, ReportV4Persistence
from .quality import ReportV4QualityGate

logger = get_logger(__name__)


@dataclass
class ReportV4Context:
    db: Any
    report: Report
    data: pd.DataFrame
    historical_data: pd.DataFrame | None
    period_start: datetime
    period_end: datetime
    output_dir: Path
    raw_sources: list[dict[str, Any]] | None = None


class ReportV4Pipeline:
    def __init__(
        self,
        *,
        dataset_builder: DatasetBuilder | None = None,
        quality_gate: ReportV4QualityGate | None = None,
        persistence: ReportV4Persistence | None = None,
        file_exporter: ReportV4FileExporter | None = None,
    ):
        self.dataset_builder = dataset_builder or DatasetBuilder()
        self.quality_gate = quality_gate or ReportV4QualityGate()
        self.persistence = persistence or ReportV4Persistence()
        self.file_exporter = file_exporter or ReportV4FileExporter()

    async def generate(self, context: ReportV4Context) -> list[dict[str, Any]]:
        country = await self._load_country(context.db, context.report.country_id)
        disease_payloads = await self._load_diseases(context.db, context.data)
        usable_data = context.data[context.data["disease_id"].isin(disease_payloads.keys())].copy()
        normalized, normalized_history, source_policy = self.dataset_builder.normalize(
            usable_data,
            country_code=country.code,
            historical_data=context.historical_data,
        )
        country_payload = {
            "id": country.id,
            "code": country.code,
            "name": country.name_local or country.name or country.name_en,
            "name_zh": country.name_local or country.name or country.name_en,
            "name_en": country.name_en or country.name or country.name_local,
            "name_local": country.name_local,
        }
        evidence_packet = build_evidence_packet(
            data=normalized,
            historical_data=normalized_history,
            country=country_payload,
            diseases=disease_payloads,
            period_start=context.period_start,
            period_end=context.period_end,
            raw_sources=context.raw_sources or [],
            knowledge_status={},
            source_policy=source_policy,
        )
        document = compose_report_document(
            evidence_packet=evidence_packet,
            country=country_payload,
            period_start=context.period_start,
            period_end=context.period_end,
        ).to_dict()
        document["metrics"]["country_code"] = country.code
        document["metrics"]["country_name"] = country_payload["name_zh"]
        document["metrics"]["country_name_en"] = country_payload["name_en"]

        quality_gate = self.quality_gate.ensure_passed(document)
        persisted_sections = await self.persistence.persist(
            db=context.db,
            report=context.report,
            document=document,
            quality_gate=quality_gate,
            evidence_packet=evidence_packet,
        )
        self.file_exporter.write_files(
            report=context.report,
            output_dir=context.output_dir,
            document=document,
        )
        await context.db.commit()
        logger.info("Report v4 generated with %s sections", len(persisted_sections))
        return persisted_sections

    @staticmethod
    async def _load_country(db, country_id: int) -> Country:
        country = (await db.execute(select(Country).where(Country.id == country_id))).scalar_one_or_none()
        if country is None:
            raise ValueError(f"Country not found: {country_id}")
        return country

    @staticmethod
    async def _load_diseases(db, data: pd.DataFrame) -> dict[int, dict[str, Any]]:
        disease_ids = [int(value) for value in data["disease_id"].dropna().unique()] if "disease_id" in data.columns else []
        if not disease_ids:
            return {}
        rows = (await db.execute(select(Disease).where(Disease.id.in_(disease_ids)))).scalars().all()
        return {
            row.id: {
                "code": row.name,
                "name": row.name,
                "name_en": row.name_en or row.name,
                "name_zh": _disease_name_zh(row),
                "category": row.category,
                "icd_10": row.icd_10,
                "icd_11": row.icd_11,
            }
            for row in rows
            if row.name != "D999"
        }


def _disease_name_zh(disease: Disease) -> str:
    metadata = disease.metadata_ if isinstance(disease.metadata_, dict) else {}
    for key in ("name_zh", "zh_name", "standard_name_zh"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    aliases = disease.aliases if isinstance(disease.aliases, list) else []
    for alias in aliases:
        text = str(alias)
        if any("\u3400" <= char <= "\u9fff" for char in text):
            return text
    return disease.name_en or disease.name
