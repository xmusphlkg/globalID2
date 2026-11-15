"""Public report projections using the caller's existing read snapshot."""
from __future__ import annotations

from sqlalchemy import text


def compact_report_metadata(
    metadata: dict | None, *, include_figures: bool = False
) -> dict:
    """Return site-facing report metadata without heavyweight agent internals."""
    if not isinstance(metadata, dict):
        return {}

    document = metadata.get("report_document_v4")
    if isinstance(document, dict):
        compact = {
            "report_layout": "report_v4",
            "schema_version": metadata.get("schema_version")
            or document.get("schema_version"),
            "method_version": metadata.get("method_version")
            or document.get("schema_version"),
            "default_locale": document.get("default_locale"),
            "locales": document.get("locales") or ["zh", "en"],
            "quality_gate": metadata.get("quality_gate") or {},
            "data_quality": document.get("data_quality")
            or metadata.get("data_quality")
            or {},
            "summary_metrics": {
                **(document.get("metrics") or {}),
                "death_reporting": document.get("death_reporting") or {},
            },
            "death_reporting": document.get("death_reporting") or {},
            "disease_directory": document.get("disease_directory") or [],
            "risk_ranking": document.get("risk_ranking") or [],
            "references": document.get("references") or [],
        }
        peer_review = document.get("peer_review") or metadata.get("peer_review")
        if peer_review:
            compact["peer_review"] = peer_review
        if include_figures:
            compact["figures"] = document.get("figures") or []
            compact["figure_data"] = metadata.get("figure_data") or {}
            if isinstance(metadata.get("surveillance_snapshot"), dict):
                compact["surveillance_snapshot"] = metadata["surveillance_snapshot"]
            compact["report_document_v4"] = document
        return compact

    return {}


async def fetch_reports(session, *, safe_float, compact_report_metadata) -> list[dict]:
    rows = await session.execute(text("""
            SELECT
                r.id, r.title, r.report_type, r.status,
                r.period_start::date  AS period_start,
                r.period_end::date    AS period_end,
                r.created_at,
                r.updated_at,
                r.summary,
                r.quality_score,
                r.metadata,
                r.generation_config,
                c.code                AS country_code,
                c.name                AS country_name,
                c.name_en             AS country_name_en,
                c.name_local          AS country_name_local
            FROM reports r
            JOIN countries c ON c.id = r.country_id
            WHERE r.status IN ('COMPLETED', 'APPROVED', 'PUBLISHED')
              AND c.code = 'JP'
            ORDER BY r.created_at DESC
            """))
    result = []
    for row in rows:
        report = dict(row._mapping)
        metadata = (
            report.get("metadata") if isinstance(report.get("metadata"), dict) else {}
        )
        document = metadata.get("report_document_v4")
        if not isinstance(document, dict):
            continue
        report["period_start"] = (
            report["period_start"].isoformat() if report["period_start"] else None
        )
        report["period_end"] = (
            report["period_end"].isoformat() if report["period_end"] else None
        )
        report["created_at"] = (
            report["created_at"].isoformat() if report["created_at"] else None
        )
        report["updated_at"] = (
            report["updated_at"].isoformat() if report.get("updated_at") else report["created_at"]
        )
        report["quality_score"] = safe_float(report["quality_score"])
        report_language = "zh"
        report["title"] = (document.get("title") or {}).get("zh") or report.get(
            "title"
        )
        report["summary"] = (document.get("summary") or {}).get("zh") or report.get(
            "summary"
        )
        report["key_findings"] = (document.get("key_findings") or {}).get("zh") or []
        report["metadata"] = compact_report_metadata(metadata, include_figures=False)
        report["metadata"]["language"] = report_language
        report["language"] = report_language
        report.pop("generation_config", None)
        report["analysis_summary"] = None
        report["quality_gate"] = report["metadata"].get("quality_gate")
        report["data_quality"] = report["metadata"].get("data_quality")
        report["method_version"] = report["metadata"].get("method_version")
        result.append(report)
    return result


async def fetch_report_detail(session, report_id: int, *, safe_float, compact_report_metadata) -> dict | None:
    row = await session.execute(
        text("""
            SELECT
                r.id, r.title, r.report_type,
                r.period_start::date AS period_start,
                r.period_end::date   AS period_end,
                r.created_at, r.updated_at, r.ai_model_used, r.quality_score,
                r.summary, r.key_findings,
                r.metadata,
                r.generation_config,
                c.code               AS country_code,
                c.name               AS country_name,
                c.name_en            AS country_name_en,
                c.name_local         AS country_name_local
            FROM reports r
            JOIN countries c ON c.id = r.country_id
            WHERE r.id = :id
            """),
        {"id": report_id},
    )
    result_row = row.fetchone()
    if not result_row:
        return None
    report = dict(result_row._mapping)
    report["period_start"] = (
        report["period_start"].isoformat() if report["period_start"] else None
    )
    report["period_end"] = (
        report["period_end"].isoformat() if report["period_end"] else None
    )
    report["created_at"] = (
        report["created_at"].isoformat() if report["created_at"] else None
    )
    report["updated_at"] = (
        report["updated_at"].isoformat() if report.get("updated_at") else report["created_at"]
    )
    report["quality_score"] = safe_float(report["quality_score"])
    metadata = report.get("metadata") if isinstance(report.get("metadata"), dict) else {}
    document = metadata.get("report_document_v4")
    if not isinstance(document, dict):
        return None
    report_language = "zh"
    report["title"] = (document.get("title") or {}).get("zh") or report.get("title")
    report["summary"] = (document.get("summary") or {}).get("zh") or report.get(
        "summary"
    )
    report["key_findings"] = (document.get("key_findings") or {}).get("zh") or []
    report["report_document_v4"] = document
    report["metadata"] = compact_report_metadata(metadata, include_figures=True)
    report["metadata"]["language"] = report_language
    report["language"] = report_language
    report.pop("generation_config", None)
    report["analysis_summary"] = None
    report["quality_gate"] = report["metadata"].get("quality_gate")
    report["data_quality"] = report["metadata"].get("data_quality")
    report["method_version"] = report["metadata"].get("method_version")
    report["sections"] = [
        {
            "section_type": section.get("type"),
            "section_order": section.get("order"),
            "title": (section.get("title") or {}).get("zh"),
            "content": (section.get("body") or {}).get("zh"),
            "charts": section.get("figures") or [],
            "metadata": {
                "locales": {
                    "zh": {
                        "title": (section.get("title") or {}).get("zh"),
                        "content": (section.get("body") or {}).get("zh"),
                    },
                    "en": {
                        "title": (section.get("title") or {}).get("en"),
                        "content": (section.get("body") or {}).get("en"),
                    },
                },
                "evidence_refs": section.get("evidence_refs") or [],
                "quality_flags": section.get("quality_flags") or [],
            },
        }
        for section in document.get("sections") or []
    ]
    return report
