"""Orchestration for report v4 generation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select

from src.core import get_logger
from src.core.report_scope import require_report_country
from src.domain import Country, Disease, Report

from .composer import compose_report_document
from .dataset import DatasetBuilder
from .evidence import build_evidence_packet
from .exporters import ReportV4FileExporter, ReportV4Persistence
from .figures import attach_figures
from .quality import ReportV4QualityGate
from .surveillance_snapshot import (
    build_surveillance_snapshot,
    ensure_snapshot_matches_evidence,
)

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
    progress_callback: Any | None = None


def attach_historical_series(evidence_packet: dict, document: dict, normalized: pd.DataFrame,
                             normalized_history: pd.DataFrame | None, disease_payloads: dict,
                             period_end: datetime) -> None:
    # Historical context is offered only for diseases discussed in the
    # focused disease section. Reuse the data already loaded for analysis.
    focus_ids = {str(ref).split(":", 1)[1].split(".", 1)[0]
                 for section in document["sections"] if section["type"] == "disease_context"
                 for ref in section.get("evidence_refs") or [] if str(ref).startswith("disease:")}
    if normalized_history is None or normalized_history.empty:
        return
    from src.generation.data_cleaner import resolve_frequency
    scope_fields = ("reporting_area", "surveillance_type", "case_scope", "unit", "metric_semantics")
    for internal_id, meta in disease_payloads.items():
        code = str(meta.get("code") or internal_id)
        if code not in focus_ids:
            continue
        current = normalized[normalized["disease_id"] == internal_id]
        try:
            grain = resolve_frequency(current)
        except ValueError:
            continue
        if not grain:
            continue
        history = normalized_history[(normalized_history["disease_id"] == internal_id)
                                     & (normalized_history["time"] <= period_end)].copy()
        if history.empty:
            continue
        def cohort(row):
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            return json.dumps([str(row.get("data_source") or "unknown"),
                               {key: metadata.get(key) for key in scope_fields}], sort_keys=True, ensure_ascii=False)
        history["_chart_cohort"] = history.apply(cohort, axis=1)
        combined = []
        for cohort_key, group in history.groupby("_chart_cohort"):
            try:
                if group["time"].duplicated().any() or resolve_frequency(group) != grain:
                    continue
                group = group.sort_values("time").tail(52)
                frozen = build_surveillance_snapshot(group, diseases={internal_id: meta},
                    period_start=group["time"].min() - timedelta(days=366), period_end=period_end)
            except ValueError:
                continue
            archived = frozen["series"].get(code, {})
            source, scope = json.loads(cohort_key)
            segment = hashlib.sha256(cohort_key.encode()).hexdigest()[:12]
            combined.extend({"period": period, "period_end": end, "cases": cases,
                             "source": source, "segment": segment, "source_scope": scope}
                            for period, end, cases in zip(archived.get("dates") or [],
                                archived.get("period_ends_exclusive") or [], archived.get("cases") or []))
        combined.sort(key=lambda row: row["period"])
        combined = combined[-52:]
        # Overlapping cohorts cannot be silently summed or chosen arbitrarily.
        if len({row["period"] for row in combined}) != len(combined) or len({row["segment"] for row in combined}) > 3:
            continue
        for disease in evidence_packet.get("diseases") or []:
            if str(disease["disease_id"]) == code:
                disease["historical_figure_series"] = combined



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
        require_report_country(country.code)
        disease_payloads = await self._load_diseases(context.db, context.data)
        usable_data = context.data[context.data["disease_id"].isin(disease_payloads.keys())].copy()
        if usable_data.empty:
            raise ValueError("Report window has no mapped, publishable disease records")
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
            "name_fr": (country.metadata_ or {}).get("name_fr") if isinstance(country.metadata_, dict) else None,
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

        surveillance_snapshot = build_surveillance_snapshot(
            normalized, diseases=disease_payloads,
            period_start=context.period_start, period_end=context.period_end,
        )
        ensure_snapshot_matches_evidence(surveillance_snapshot, evidence_packet)
        # Charts use the same frozen observations, including missing values,
        # as the report detail. They never query the evolving country dataset.
        for disease in evidence_packet.get("diseases") or []:
            archived = surveillance_snapshot["series"].get(str(disease["disease_id"]), {})
            disease["figure_series"] = [
                {"period": period, "period_end": end, "cases": cases, "quality_status": status}
                for period, end, cases, status in zip(
                    archived.get("dates") or [], archived.get("period_ends_exclusive") or [],
                    archived.get("cases") or [], archived.get("point_quality_statuses") or [],
                )
            ]

        attach_historical_series(evidence_packet, document, normalized, normalized_history,
                                 disease_payloads, context.period_end)

        if context.progress_callback:
            await context.progress_callback("review", 0, 1, "Writing evidence-grounded interpretation and starting peer review")
        from .peer_review import review_report_narrative

        interpretation, peer_review_record = await review_report_narrative(
            evidence_packet=evidence_packet,
            deterministic_document=document,
        )
        _insert_peer_reviewed_interpretation(document, interpretation)
        document["peer_review"] = peer_review_record
        attach_figures(document, evidence_packet, peer_review_record["figure_plan"])
        if context.progress_callback:
            await context.progress_callback("review", 1, 1, "Report interpretation passed peer review")

        quality_gate = self.quality_gate.ensure_passed(document)
        persisted_sections = await self.persistence.persist(
            db=context.db,
            report=context.report,
            document=document,
            quality_gate=quality_gate,
            evidence_packet=evidence_packet,
            surveillance_snapshot=surveillance_snapshot,
        )
        self.file_exporter.write_files(
            report=context.report,
            output_dir=context.output_dir,
            document=document,
            surveillance_snapshot=surveillance_snapshot,
        )
        await context.db.commit()
        logger.info(f"Report v4 generated with {len(persisted_sections)} sections")
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
                "name_fr": _disease_name_fr(row),
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


def _disease_name_fr(disease: Disease) -> str | None:
    metadata = disease.metadata_ if isinstance(disease.metadata_, dict) else {}
    for key in ("name_fr", "fr_name", "standard_name_fr"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _insert_peer_reviewed_interpretation(
    document: dict[str, Any], interpretation: dict[str, str]
) -> None:
    """Add only the reviewed prose; keep all deterministic facts untouched."""

    sections = document.setdefault("sections", [])
    for section in sections:
        if int(section.get("order") or 0) >= 2:
            section["order"] = int(section["order"]) + 1
    sections.insert(
        1,
        {
            "id": "peer_reviewed_interpretation",
            "type": "peer_reviewed_interpretation",
            "order": 2,
            "title": {
                "zh": "AI 同行评审解读",
                "en": "Peer-reviewed AI interpretation",
                "fr": "Interprétation IA évaluée par les pairs",
            },
            "body": {locale: interpretation[locale] for locale in ("zh", "en", "fr")},
            "evidence_refs": [
                f"disease:{item.get('disease_id')}"
                for item in (document.get("attention_ranking") or [])[:10]
                if item.get("disease_id")
            ],
            "figures": [],
            "quality_flags": ["peer_reviewed"],
        },
    )
