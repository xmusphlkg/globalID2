"""Persistence and file export adapters for report v4."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from src.domain import Report, ReportSection
from src.generation.formatter import ReportFormatter

from .models import METHOD_VERSION, SCHEMA_VERSION


class ReportV4Persistence:
    async def persist(
        self,
        *,
        db,
        report: Report,
        document: dict[str, Any],
        quality_gate: dict[str, Any],
        evidence_packet: dict[str, Any],
    ) -> list[dict[str, Any]]:
        existing = (
            await db.execute(
                select(ReportSection)
                .where(ReportSection.report_id == report.id)
                .order_by(ReportSection.section_order)
            )
        ).scalars().all()
        for section in existing:
            await db.delete(section)
        await db.flush()

        report.title = document["title"]["zh"]
        report.summary = document["summary"]["zh"]
        report.key_findings = document["key_findings"]["zh"]
        report.recommendations = _extract_recommendations(document)
        report.quality_score = quality_gate.get("overall_score")
        report.metadata_ = {
            **(report.metadata_ or {}),
            "report_layout": "report_v4",
            "schema_version": SCHEMA_VERSION,
            "method_version": METHOD_VERSION,
            "language": "zh",
            "default_locale": document.get("default_locale"),
            "locales": document.get("locales"),
            "report_document_v4": document,
            "quality_gate": quality_gate,
            "data_quality": document.get("data_quality") or {},
            "summary_metrics": {
                **(document.get("metrics") or {}),
                "death_reporting": document.get("death_reporting") or {},
            },
            "death_reporting": document.get("death_reporting") or {},
            "disease_directory": document.get("disease_directory") or [],
            "risk_ranking": document.get("risk_ranking") or [],
            "references": document.get("references") or [],
            "figures": document.get("figures") or [],
            "figure_data": {},
            "data_signature": (document.get("metrics") or {}).get("data_signature"),
            "evidence_packet": {
                key: value
                for key, value in evidence_packet.items()
                if key not in {"diseases", "evidence_index"}
            },
        }

        persisted: list[dict[str, Any]] = []
        for order, section_payload in enumerate(document.get("sections") or [], 1):
            section = ReportSection(
                report_id=report.id,
                title=section_payload["title"]["zh"],
                content=section_payload["body"]["zh"],
                section_type=section_payload["type"],
                section_order=order,
                charts=section_payload.get("figures") or [],
                data_sources=evidence_packet.get("sources") or [],
                is_verified=bool(quality_gate.get("passed")),
                metadata_={
                    "schema_version": SCHEMA_VERSION,
                    "locales": {
                        "zh": {
                            "title": section_payload["title"]["zh"],
                            "content": section_payload["body"]["zh"],
                        },
                        "en": {
                            "title": section_payload["title"]["en"],
                            "content": section_payload["body"]["en"],
                        },
                    },
                    "evidence_refs": section_payload.get("evidence_refs") or [],
                    "quality_flags": section_payload.get("quality_flags") or [],
                    "display_type": section_payload["title"],
                },
            )
            db.add(section)
            await db.flush()
            persisted.append(
                {
                    "section_id": section.id,
                    "title": section.title,
                    "content": section.content,
                    "type": section.section_type,
                    "section_type": section.section_type,
                    "metadata": section.metadata_ or {},
                    "figures": section_payload.get("figures") or [],
                    "charts": section_payload.get("figures") or [],
                    "is_verified": bool(quality_gate.get("passed")),
                    "quality_scores": quality_gate,
                }
            )
        await db.commit()
        return persisted


class ReportV4FileExporter:
    def __init__(self, formatter: ReportFormatter | None = None):
        self.formatter = formatter or ReportFormatter()

    def write_files(self, *, report: Report, output_dir: Path, document: dict[str, Any]) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = f"report_{report.id}_{timestamp}"
        sections = [
            {
                "title": section["title"]["zh"],
                "content": section["body"]["zh"],
                "type": section["type"],
                "section_type": section["type"],
                "metadata": {
                    "locales": {
                        "zh": {"title": section["title"]["zh"], "content": section["body"]["zh"]},
                        "en": {"title": section["title"]["en"], "content": section["body"]["en"]},
                    }
                },
                "figures": section.get("figures") or [],
            }
            for section in document.get("sections") or []
        ]
        metadata = {
            "report_layout": "report_v4",
            "report_document_v4": document,
            "title": document["title"]["zh"],
            "summary": document["summary"]["zh"],
            "country": (document.get("metrics") or {}).get("country_name"),
            "period_start": getattr(report.period_start, "strftime", lambda _fmt: "")("%Y-%m-%d"),
            "period_end": getattr(report.period_end, "strftime", lambda _fmt: "")("%Y-%m-%d"),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "language": "zh",
            "disease_directory": document.get("disease_directory") or [],
        }
        markdown = self.formatter.format_markdown(sections, metadata)
        html = self.formatter.format_html(sections, metadata)
        markdown_path = output_dir / f"{prefix}.md"
        html_path = output_dir / f"{prefix}.html"
        self.formatter.save(markdown, str(markdown_path))
        self.formatter.save(html, str(html_path))
        report.markdown_path = str(markdown_path)
        report.html_path = str(html_path)

        json_path = output_dir / f"{prefix}.report-v4.json"
        self.formatter.save(json.dumps(document, ensure_ascii=False, indent=2), str(json_path))


def _extract_recommendations(document: dict[str, Any]) -> list[str]:
    for section in document.get("sections") or []:
        if section.get("type") == "priority_actions":
            text = ((section.get("body") or {}).get("zh") or "")
            return [line[2:].strip() for line in text.splitlines() if line.startswith("- ")]
    return []
