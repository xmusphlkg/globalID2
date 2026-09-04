#!/usr/bin/env python3
"""Reclassify legacy knowledge review rows into the automated evidence workflow.

The old workflow used ``requires_review`` for three different states: title-only
source metadata, missing section evidence, and genuinely ambiguous source
quality.  This migration removes the first two from the human queue, applies
the live profile schema to stored briefs, and can rebuild a narrow source-first
repair queue after the worker has been stopped.

Examples:
    PYTHONPATH=. python scripts/migrate_knowledge_automation.py
    PYTHONPATH=. python scripts/migrate_knowledge_automation.py --apply --rebuild-queue
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from src.core import get_database
from src.domain import DiseaseKnowledgeBrief, DiseaseKnowledgeSource, Task, TaskStatus, TaskType
from src.knowledge import (
    EVIDENCE_POLICY_VERSION,
    KNOWLEDGE_SCHEMA_VERSION,
    apply_knowledge_quality_gate,
    attach_profile_schema,
)
from src.knowledge.citations import (
    KNOWLEDGE_CITATION_FIELDS,
    normalize_knowledge_citations,
    validate_knowledge_citations,
)
from src.knowledge.surveillance_note_overrides import apply_surveillance_note_override
from src.services.disease_knowledge_service import (
    ACTIVE_KNOWLEDGE_TASK_STATUSES,
    KNOWLEDGE_PIPELINE_VERSION,
    DiseaseKnowledgeUpdateService,
    _source_qualification_reason,
    load_standard_diseases,
    source_to_dict,
)

MIGRATION_VERSION = "knowledge-automation.v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _catalogue_by_id() -> dict[str, dict[str, Any]]:
    return {
        str(row["disease_id"]).upper(): attach_profile_schema(row)
        for row in load_standard_diseases()
    }


def _disease_for(
    disease_id: str,
    catalogue: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    existing = catalogue.get(disease_id.upper())
    if existing is not None:
        return existing
    # Retired or newly imported rows still receive the current default schema;
    # they remain discoverable through the normal source-first queue.
    return attach_profile_schema({"disease_id": disease_id, "name_en": disease_id})


def _brief_payload(
    row: DiseaseKnowledgeBrief,
    disease: dict[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Apply the current deterministic publication gates to one stored row."""

    metadata = dict(row.metadata_ or {})
    previous_migration = metadata.get("automation_migration")
    migration_metadata = (
        previous_migration
        if isinstance(previous_migration, dict)
        and previous_migration.get("version") == MIGRATION_VERSION
        else {
            "version": MIGRATION_VERSION,
            "at": _now(),
            "prior_status": row.status,
        }
    )
    payload: dict[str, Any] = {
        "disease_id": row.disease_id,
        "language": row.language,
        # Let the quality gate make the status decision from evidence; legacy
        # requires_review is intentionally not carried forward as a verdict.
        "status": "published",
        "brief": row.brief or "",
        "definition": row.definition,
        "clinical_features": row.clinical_features,
        "clinical_summary": row.clinical_summary,
        "epidemiology": row.epidemiology,
        "transmission": row.transmission,
        "prevention": row.prevention,
        "surveillance_note": row.surveillance_note,
        "risk_groups": row.risk_groups,
        "source_ids": list(row.source_ids or []),
        "source_attribution": list(row.source_attribution or []),
        "disclaimer": row.disclaimer,
        "model": row.model,
        "source_confidence": row.source_confidence or "medium",
        "review_notes": row.review_notes,
        "metadata": {
            **metadata,
            "pipeline_version": KNOWLEDGE_PIPELINE_VERSION,
            "knowledge_schema_version": KNOWLEDGE_SCHEMA_VERSION,
            "evidence_policy_version": EVIDENCE_POLICY_VERSION,
            "profile_schema": disease["profile_schema"],
            "automation_migration": migration_metadata,
        },
    }
    payload = normalize_knowledge_citations(payload, prune_uncited_sources=True)
    payload = apply_surveillance_note_override(payload)
    payload, assessment = apply_knowledge_quality_gate(payload)
    citation_failures = tuple(
        validate_knowledge_citations(
            payload,
            fields=[field for field in KNOWLEDGE_CITATION_FIELDS if field != "clinical_summary"],
        )
    )
    payload["metadata"] = {
        **(payload.get("metadata") or {}),
        "citation_version": 2,
        "citation_validation": {
            "valid": not citation_failures,
            "validated_fields": [
                field for field in KNOWLEDGE_CITATION_FIELDS if field != "clinical_summary"
            ],
            "failures": list(citation_failures),
        },
    }
    if citation_failures:
        payload["status"] = "draft"
        payload["metadata"] = {
            **payload["metadata"],
            "automation_state": "awaiting_evidence",
            "block_reason": "citation_validation_failed",
        }
    elif payload.get("status") != "published":
        payload["metadata"] = {
            **payload["metadata"],
            "automation_state": "awaiting_evidence",
            "block_reason": "missing_required_sections",
        }
    return payload, assessment.missing_required_fields


def _apply_brief_payload(row: DiseaseKnowledgeBrief, payload: dict[str, Any]) -> None:
    for field in (
        "brief",
        "definition",
        "clinical_features",
        "clinical_summary",
        "epidemiology",
        "transmission",
        "prevention",
        "surveillance_note",
        "risk_groups",
        "source_ids",
        "source_attribution",
        "disclaimer",
        "model",
        "status",
        "source_confidence",
        "quality_score",
        "review_notes",
    ):
        setattr(row, field, payload.get(field))
    row.metadata_ = payload.get("metadata") or {}


async def reconcile(*, apply: bool) -> dict[str, Any]:
    catalogue = _catalogue_by_id()
    source_counts: Counter[str] = Counter()
    brief_transitions: Counter[str] = Counter()
    missing_sections: Counter[str] = Counter()
    changed_briefs = 0

    async with get_database() as db:
        sources = list(
            (
                await db.execute(
                    select(DiseaseKnowledgeSource).where(
                        DiseaseKnowledgeSource.status == "active",
                        DiseaseKnowledgeSource.review_status == "requires_review",
                    )
                )
            ).scalars().all()
        )
        for source in sources:
            reason = _source_qualification_reason(source_to_dict(source))
            if reason is None:
                source_counts["retained_for_ai_governance"] += 1
                continue
            source_counts[f"rejected_{reason}"] += 1
            if apply:
                source.review_status = "rejected"
                source.metadata_ = {
                    **(source.metadata_ or {}),
                    "qualification_state": "not_grounding_eligible",
                    "qualification_reason": reason,
                    "automation_migration": {
                        "version": MIGRATION_VERSION,
                        "at": _now(),
                    },
                }

        briefs = list((await db.execute(select(DiseaseKnowledgeBrief))).scalars().all())
        for brief in briefs:
            payload, missing = _brief_payload(
                brief,
                _disease_for(brief.disease_id, catalogue),
            )
            old_status = str(brief.status or "draft")
            new_status = str(payload["status"])
            brief_transitions[f"{old_status}->{new_status}"] += 1
            for field in missing:
                missing_sections[field] += 1
            if any(
                (
                    old_status != new_status,
                    dict(brief.metadata_ or {}) != dict(payload.get("metadata") or {}),
                    brief.quality_score != payload.get("quality_score"),
                )
            ):
                changed_briefs += 1
                if apply:
                    _apply_brief_payload(brief, payload)
        if apply:
            await db.commit()
        else:
            await db.rollback()

    return {
        "mode": "applied" if apply else "rehearsal",
        "migration_version": MIGRATION_VERSION,
        "source_review_rows": len(sources),
        "source_actions": dict(sorted(source_counts.items())),
        "brief_rows": len(briefs),
        "changed_brief_rows": changed_briefs,
        "brief_status_transitions": dict(sorted(brief_transitions.items())),
        "missing_required_sections": dict(sorted(missing_sections.items())),
    }


async def retire_legacy_auto_repair_tasks(*, apply: bool) -> dict[str, Any]:
    """Clear broad pre-v3 tasks before rebuilding exact source-first work."""

    retired: list[str] = []
    async with get_database() as db:
        tasks = list(
            (
                await db.execute(
                    select(Task).where(
                        Task.task_type.in_(
                            (
                                TaskType.UPDATE_DISEASE_KNOWLEDGE,
                                TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES,
                            )
                        ),
                        Task.status.in_(ACTIVE_KNOWLEDGE_TASK_STATUSES),
                    )
                )
            ).scalars().all()
        )
        for task in tasks:
            tags = {str(tag) for tag in (task.tags or [])}
            if "auto_repair" not in tags:
                continue
            retired.append(task.task_uuid)
            if apply:
                task.status = TaskStatus.CANCELLED
                task.last_error = "Superseded by knowledge-automation.v1 section-aware queue rebuild"
                task.metadata_ = {
                    **(task.metadata_ or {}),
                    "superseded_by": MIGRATION_VERSION,
                    "superseded_at": _now(),
                }
        if apply:
            await db.commit()
        else:
            await db.rollback()
    return {"retired_count": len(retired), "retired_task_uuids": retired}


async def rebuild_queue(*, source_groups: list[str], limit: int | None) -> dict[str, Any]:
    service = DiseaseKnowledgeUpdateService()
    async with get_database() as db:
        result = await service.enqueue_repair_tasks(
            db,
            source_groups=source_groups,
            force=True,
            generator_mode="ai",
            limit=limit,
            source_first=True,
            requested_by=MIGRATION_VERSION,
            initiated_via=MIGRATION_VERSION,
        )
    return {
        "created_count": result["created_count"],
        "candidate_count": result["candidate_count"],
        "skipped_count": result["skipped_count"],
        "created_task_uuids": [task.task_uuid for task in result["created_tasks"]],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Persist the deterministic reclassification")
    parser.add_argument(
        "--rebuild-queue",
        action="store_true",
        help="Cancel legacy automatic knowledge tasks and enqueue section-aware source-first work (requires --apply)",
    )
    parser.add_argument(
        "--source-group",
        action="append",
        default=[],
        help="Source group for rebuilt tasks; repeatable, defaults to all configured groups",
    )
    parser.add_argument("--limit", type=int, default=None, help="Maximum rebuilt tasks")
    return parser


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    if args.rebuild_queue and not args.apply:
        raise ValueError("--rebuild-queue requires --apply")
    result = {"reconciliation": await reconcile(apply=args.apply)}
    if args.rebuild_queue:
        result["retired_tasks"] = await retire_legacy_auto_repair_tasks(apply=True)
        result["rebuilt_queue"] = await rebuild_queue(
            source_groups=list(args.source_group),
            limit=args.limit,
        )
    return result


if __name__ == "__main__":
    arguments = _parser().parse_args()
    if arguments.limit is not None and arguments.limit < 1:
        _parser().error("--limit must be at least 1")
    try:
        report = asyncio.run(main_async(arguments))
    except ValueError as exc:
        _parser().exit(2, f"knowledge migration refused: {exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
