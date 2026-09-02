"""Disease knowledge update service used by scripts, worker tasks, and dashboard APIs."""

from __future__ import annotations

import asyncio
import csv
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func, select, text

from src.core import get_config, get_database, get_logger
from src.core.task_manager import task_manager
from src.domain import (
    DiseaseKnowledgeBrief,
    DiseaseKnowledgeSource,
    Task,
    TaskPriority,
    TaskStatus,
    TaskType,
)
from src.knowledge import (
    AIDiseaseBriefGenerator,
    ReviewedDiseaseBriefGenerator,
    apply_knowledge_quality_gate,
    assess_knowledge_evidence,
    assess_knowledge_brief,
    attach_profile_schema,
    DiseaseKnowledgeFetcher,
    EVIDENCE_POLICY_VERSION,
    has_grounding_content,
    KNOWLEDGE_SCHEMA_VERSION,
    knowledge_brief_block_reason,
    knowledge_brief_publication_tier,
    prepare_evidence_packet,
    resolve_disease_knowledge_status,
    SourceFetchReport,
)
from src.knowledge.citations import (
    KNOWLEDGE_CITATION_FIELDS,
    normalize_knowledge_citations,
    validate_knowledge_citations,
)
from src.knowledge.profile_schema import resolve_knowledge_profile_schema
from src.ontology import load_disease_ontology
from src.services.exceptions import TaskCancelledError

logger = get_logger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DISEASE_CSV = ROOT / "configs" / "standard_diseases.csv"
KNOWLEDGE_PIPELINE_VERSION = 2
SOURCE_REFRESH_TTL_DAYS = 30

SOURCE_GROUPS = {
    "who": ["who", "who_don"],
    "who_don": ["who_don"],
    "search": ["web_search"],
    "web": ["web_search"],
    "web_search": ["web_search"],
    "wikidata": ["wikidata"],
    "wikipedia": ["wikipedia"],
    "pubmed": ["pubmed"],
    "msd": ["msd"],
}

ACTIVE_KNOWLEDGE_TASK_STATUSES = (
    TaskStatus.PENDING,
    TaskStatus.QUEUED,
    TaskStatus.RUNNING,
    TaskStatus.RETRYING,
)


def _knowledge_evidence_limits() -> dict[str, int]:
    config = get_config().ai
    return {
        "max_sources": int(config.knowledge_evidence_max_sources),
        "max_manifest_characters": int(config.knowledge_evidence_manifest_max_characters),
    }


class KnowledgeEvidenceInsufficientError(RuntimeError):
    """Raised when automatic source enrichment cannot produce publishable knowledge."""


def load_standard_diseases(path: Path = DEFAULT_DISEASE_CSV) -> list[dict[str, Any]]:
    """Load the standard disease catalogue from CSV."""
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as csv_file:
        for raw in csv.DictReader(csv_file):
            rows.append(
                {
                    "disease_id": raw["disease_id"],
                    "name_en": raw.get("standard_name_en") or raw.get("name_en"),
                    "name_zh": raw.get("standard_name_zh") or raw.get("name_zh"),
                    "standard_name_en": raw.get("standard_name_en"),
                    "standard_name_zh": raw.get("standard_name_zh"),
                    "category": raw.get("category") or "Unknown",
                    "icd_10": raw.get("icd_10"),
                    "icd_11": raw.get("icd_11"),
                    "description": raw.get("description") or "",
                    "source": raw.get("source") or "",
                }
            )
    return rows


def expand_sources(values: list[str] | None) -> list[str]:
    """Expand source groups into concrete adapters."""
    if not values:
        values = ["who", "search", "wikidata", "wikipedia", "pubmed", "msd"]
    expanded: list[str] = []
    for value in values:
        for adapter in SOURCE_GROUPS.get(value, []):
            if adapter not in expanded:
                expanded.append(adapter)
    return expanded


async def _existing_sources(db, disease_id: str) -> list[DiseaseKnowledgeSource]:
    result = await db.execute(
        select(DiseaseKnowledgeSource)
        .where(DiseaseKnowledgeSource.disease_id == disease_id)
        .order_by(DiseaseKnowledgeSource.source_type, DiseaseKnowledgeSource.id)
    )
    return list(result.scalars().all())


async def _related_parent_sources(db, disease: dict[str, Any]) -> list[dict[str, Any]]:
    """Reuse only explicitly scoped parent evidence from the ontology graph."""
    context = disease.get("ontology_context") if isinstance(disease.get("ontology_context"), dict) else {}
    related = context.get("related_entities") if isinstance(context.get("related_entities"), list) else []
    inherited: list[dict[str, Any]] = []
    for relation in related:
        if not isinstance(relation, dict) or not relation.get("disease_id"):
            continue
        rows = await _existing_sources(db, str(relation["disease_id"]))
        for row in rows:
            source = source_to_dict(row)
            if str(source.get("status") or "") != "active":
                continue
            if str(source.get("review_status") or "") != "approved":
                continue
            if not has_grounding_content(source):
                continue
            source["metadata"] = {
                **(source.get("metadata") or {}),
                "inherited_evidence": True,
                "inherited_from_disease_id": relation["disease_id"],
                "ontology_relation_type": relation.get("relation_type"),
                "allowed_sections": relation.get("allowed_shared_sections") or [],
            }
            inherited.append(source)
    return inherited[:6]


async def _upsert_source(db, candidate) -> DiseaseKnowledgeSource:
    result = await db.execute(
        select(DiseaseKnowledgeSource).where(
            DiseaseKnowledgeSource.disease_id == candidate.disease_id,
            DiseaseKnowledgeSource.source_type == candidate.source_type,
            DiseaseKnowledgeSource.url == candidate.url,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = DiseaseKnowledgeSource(
            disease_id=candidate.disease_id,
            source_type=candidate.source_type,
            source_name=candidate.source_name,
            url=candidate.url,
        )
        db.add(row)

    row.title = candidate.title
    row.license = candidate.license
    row.status = candidate.status
    row.language = candidate.language
    row.resolved_url = getattr(candidate, "resolved_url", None) or candidate.url
    row.raw_excerpt = candidate.raw_excerpt
    row.content_text = getattr(candidate, "content_text", None)
    row.content_sections = getattr(candidate, "content_sections", None) or []
    row.raw_excerpt_hash = candidate.raw_excerpt_hash
    row.fetched_at = candidate.fetched_at
    row.review_status = candidate.review_status
    row.metadata_ = {
        **(candidate.metadata or {}),
        "resolved_url": getattr(candidate, "resolved_url", None) or candidate.url,
    }
    await db.flush()
    return row


async def _mark_stale_sources(
    db,
    disease_id: str,
    candidates: list,
    enabled_sources: list[str],
    adapter_outcomes: dict[str, str] | None = None,
) -> None:
    """Reject old rows from refreshed source adapters that no longer match."""
    # An empty result can also mean that every remote adapter failed. Preserve
    # the last known sources instead of turning a transient outage into a
    # destructive refresh; semantic grounding checks still prevent weak rows
    # from being published.
    if not candidates:
        return
    fresh_keys = {(c.disease_id, c.source_type, c.url) for c in candidates}
    source_rows = await _existing_sources(db, disease_id)
    for row in source_rows:
        if row.source_type not in enabled_sources:
            continue
        if adapter_outcomes is not None and adapter_outcomes.get(row.source_type) not in {
            "success",
            "success_empty",
        }:
            continue
        if (row.disease_id, row.source_type, row.url) in fresh_keys:
            continue
        row.status = "stale"
        row.review_status = "rejected"
        row.metadata_ = {**(row.metadata_ or {}), "stale_reason": "Not returned by latest forced refresh"}


async def _upsert_brief(db, payload: dict[str, Any]) -> DiseaseKnowledgeBrief:
    from src.knowledge.surveillance_note_overrides import apply_surveillance_note_override

    payload = normalize_knowledge_citations(
        payload,
        prune_uncited_sources=True,
    )
    payload = apply_surveillance_note_override(payload)
    payload, _ = apply_knowledge_quality_gate(payload)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    previous_validation = (
        metadata.get("citation_validation")
        if isinstance(metadata.get("citation_validation"), dict)
        else {}
    )
    validated_fields = previous_validation.get("validated_fields")
    validation_fields = (
        validated_fields
        if isinstance(validated_fields, list)
        else [
            field
            for field in KNOWLEDGE_CITATION_FIELDS
            if field != "clinical_summary"
        ]
    )
    citation_failures = validate_knowledge_citations(
        payload,
        fields=validation_fields,
    )
    payload["metadata"] = {
        **metadata,
        "citation_validation": {
            "valid": not citation_failures,
            "validated_fields": validation_fields,
            "failures": citation_failures,
        },
    }
    if citation_failures:
        payload["status"] = "requires_review"
        citation_note = "Citation validation failed: " + "; ".join(citation_failures)
        existing_notes = str(payload.get("review_notes") or "").strip()
        payload["review_notes"] = (
            f"{existing_notes}; {citation_note}"
            if existing_notes and citation_note not in existing_notes
            else citation_note
        )
    result = await db.execute(
        select(DiseaseKnowledgeBrief).where(
            DiseaseKnowledgeBrief.disease_id == payload["disease_id"],
            DiseaseKnowledgeBrief.language == payload["language"],
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = DiseaseKnowledgeBrief(
            disease_id=payload["disease_id"],
            language=payload["language"],
            brief=payload["brief"],
        )
        db.add(row)

    row.brief = payload["brief"]
    row.definition = payload.get("definition")
    row.clinical_features = payload.get("clinical_features")
    row.epidemiology = payload.get("epidemiology")
    row.clinical_summary = payload.get("clinical_summary")
    row.transmission = payload.get("transmission")
    row.prevention = payload.get("prevention")
    row.surveillance_note = payload.get("surveillance_note")
    row.risk_groups = payload.get("risk_groups")
    row.source_ids = payload.get("source_ids") or []
    row.source_attribution = payload.get("source_attribution") or []
    row.disclaimer = payload.get("disclaimer")
    row.model = payload.get("model")
    row.status = payload.get("status") or "draft"
    row.source_confidence = payload.get("source_confidence") or "medium"
    row.quality_score = payload.get("quality_score")
    row.review_notes = payload.get("review_notes")
    row.metadata_ = payload.get("metadata") or {}
    await db.flush()
    return row


def source_to_dict(row: DiseaseKnowledgeSource) -> dict[str, Any]:
    metadata = row.metadata_ or {}
    return {
        "id": row.id,
        "disease_id": row.disease_id,
        "source_type": row.source_type,
        "source_name": row.source_name,
        "url": row.url,
        "resolved_url": row.resolved_url or metadata.get("resolved_url") or row.url,
        "title": row.title,
        "license": row.license,
        "status": row.status,
        "language": row.language,
        "raw_excerpt": row.raw_excerpt,
        "content_text": row.content_text or metadata.get("content_text"),
        "content_sections": row.content_sections or metadata.get("content_sections") or [],
        "raw_excerpt_hash": row.raw_excerpt_hash,
        "review_status": row.review_status,
        "metadata": metadata,
        "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
    }


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _brief_metadata(brief: Any) -> dict[str, Any]:
    if isinstance(brief, dict):
        metadata = brief.get("metadata") or brief.get("metadata_")
    else:
        metadata = getattr(brief, "metadata_", None) or getattr(brief, "metadata", None)
    return metadata if isinstance(metadata, dict) else {}


def _brief_uses_current_pipeline(brief: Any) -> bool:
    metadata = _brief_metadata(brief)
    return (
        _safe_int(metadata.get("pipeline_version")) == KNOWLEDGE_PIPELINE_VERSION
        and _safe_int(metadata.get("knowledge_schema_version")) == KNOWLEDGE_SCHEMA_VERSION
        and _safe_int(metadata.get("evidence_policy_version")) == EVIDENCE_POLICY_VERSION
        and _safe_int(metadata.get("citation_version")) == 2
    )


def _sources_need_refresh(
    sources: list[Any],
    *,
    now: datetime | None = None,
    ttl_days: int = SOURCE_REFRESH_TTL_DAYS,
) -> bool:
    active_fetched_at = [
        getattr(source, "fetched_at", None)
        if not isinstance(source, dict)
        else source.get("fetched_at")
        for source in sources
        if str(
            getattr(source, "status", None)
            if not isinstance(source, dict)
            else source.get("status")
        )
        == "active"
        and str(
            getattr(source, "review_status", None)
            if not isinstance(source, dict)
            else source.get("review_status")
        )
        == "approved"
    ]
    parsed: list[datetime] = []
    for value in active_fetched_at:
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                value = None
        if isinstance(value, datetime):
            parsed.append(
                value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
            )
    if not parsed:
        return True
    current = now or datetime.now(timezone.utc)
    return current - max(parsed) >= timedelta(days=max(1, ttl_days))


async def _acquire_disease_knowledge_lock(db, disease_id: str) -> None:
    """Serialize updates for one disease across API, CLI and worker processes."""
    bind = db.get_bind()
    if getattr(getattr(bind, "dialect", None), "name", None) != "postgresql":
        return
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:scope))"),
        {"scope": f"disease-knowledge:{disease_id.upper()}"},
    )


def _metadata_field(source: dict[str, Any], key: str) -> Any:
    metadata = source.get("metadata")
    if isinstance(metadata, dict):
        return metadata.get(key)
    return None


def _enrich_source_attribution(
    attribution: list[Any],
    sources_by_id: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    metadata_keys = (
        "pmid",
        "doi",
        "first_author",
        "journal",
        "pub_date",
        "container_title",
        "publisher",
        "year",
        "provider",
        "content_kind",
    )
    direct_keys = (
        "source_name",
        "source_type",
        "title",
        "url",
        "resolved_url",
        "license",
        "fetched_at",
    )

    for item in attribution:
        if not isinstance(item, dict):
            continue
        source_id = _safe_int(item.get("source_id") or item.get("id"))
        source = sources_by_id.get(source_id) if source_id is not None else None
        if not source:
            enriched.append(dict(item))
            continue

        source_metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
        item_metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        merged = {
            **item,
            "id": item.get("id") or source.get("id"),
            "source_id": item.get("source_id") or source.get("id"),
            "metadata": {**source_metadata, **item_metadata},
        }
        for key in direct_keys:
            if not merged.get(key):
                merged[key] = source.get(key)
        for key in metadata_keys:
            if not merged.get(key):
                merged[key] = _metadata_field(source, key)
        enriched.append(merged)
    return enriched


def _has_approved_public_sources(sources: list[dict[str, Any]]) -> bool:
    public_sources = [
        source
        for source in sources
        if str(source.get("source_type") or "") in AIDiseaseBriefGenerator.PUBLIC_SOURCE_TYPES
    ]
    return assess_knowledge_evidence(public_sources).sufficient


def _evidence_block_reason(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return "no_sources_fetched"
    assessment = assess_knowledge_evidence(sources)
    if assessment.issues:
        return ",".join(assessment.issues)
    if not any(has_grounding_content(src) for src in sources):
        return "metadata_only_sources"
    return "insufficient_source_evidence"


def _generated_profile_failures(results: list[dict[str, Any]]) -> list[str]:
    """Return reasons that generated profiles must not be persisted as complete work."""
    failures: list[str] = []
    languages_seen: set[str] = set()
    for result in results:
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else result
        trace = result.get("trace") if isinstance(result.get("trace"), dict) else {}
        language = str(payload.get("language") or "unknown").strip().lower()
        languages_seen.add(language)
        if trace.get("error"):
            failures.append(f"{language}: generator error: {trace['error']}")
        assessment = assess_knowledge_brief(payload, language)
        if not assessment.fields["brief"].available:
            failures.append(f"{language}: substantive brief is required")
        if str(payload.get("status") or "").strip().lower() != "published":
            failures.append(f"{language}: status is not published")
        if not assessment.profile_available:
            failures.append(
                f"{language}: no substantive profile ({'; '.join(assessment.issues) or 'quality gate failed'})"
            )
        if assessment.missing_required_fields:
            failures.append(
                f"{language}: missing required sections ("
                + ", ".join(assessment.missing_required_fields)
                + ")"
            )
        citation_validation = (
            payload.get("metadata", {}).get("citation_validation")
            if isinstance(payload.get("metadata"), dict)
            else None
        )
        if isinstance(citation_validation, dict) and citation_validation.get("failures"):
            failures.append(
                f"{language}: citation validation failed ("
                + "; ".join(str(item) for item in citation_validation["failures"])
                + ")"
            )
    for language in ("en", "zh"):
        if language not in languages_seen:
            failures.append(f"{language}: profile was not generated")
    return failures


def _bilingual_alignment_failures(results: list[dict[str, Any]]) -> list[str]:
    """Ensure both localized profiles were rendered from one evidence boundary."""
    payloads = {
        str((result.get("payload") or {}).get("language") or ""): result.get("payload") or {}
        for result in results
    }
    if not all(language in payloads for language in ("en", "zh")):
        return []
    failures: list[str] = []
    metadata = {
        language: payloads[language].get("metadata")
        if isinstance(payloads[language].get("metadata"), dict)
        else {}
        for language in ("en", "zh")
    }
    newly_generated_languages = {
        str((result.get("payload") or {}).get("language") or "")
        for result in results
        if (result.get("trace") or {}).get("generator") == "ai"
    }
    manifest_ids = {
        language: (metadata[language].get("evidence_manifest") or {}).get("manifest_id")
        for language in newly_generated_languages
        if isinstance(metadata[language].get("evidence_manifest"), dict)
    }
    if len(manifest_ids) > 1 and len(set(manifest_ids.values())) != 1:
        failures.append("bilingual profiles used different evidence manifests")
    profile_types = {
        language: (metadata[language].get("profile_schema") or {}).get("profile_type")
        for language in ("en", "zh")
    }
    if profile_types["en"] != profile_types["zh"]:
        failures.append("bilingual profiles used different profile schemas")
    return failures


def _profile_repair_sections(
    briefs: list[Any],
    disease: dict[str, Any],
) -> list[str]:
    """Return only fields that need evidence repair in either language."""
    schema = disease["profile_schema"]
    ordered_fields = ["brief", *schema["required_fields"], *schema["optional_fields"]]
    by_language = _profile_repair_sections_by_language(briefs, disease)
    targets = {field for fields in by_language.values() for field in fields}
    return [field for field in ordered_fields if field in targets]


def _active_knowledge_tasks_by_disease(tasks: list[Task]) -> dict[str, Task]:
    """Map active knowledge tasks to their disease IDs for idempotent enqueueing."""
    result: dict[str, Task] = {}
    for task in tasks:
        input_data = dict(getattr(task, "input_data", None) or {})
        candidates: list[str] = []
        disease_ids = input_data.get("disease_ids")
        if isinstance(disease_ids, list):
            candidates.extend(str(value).strip().upper() for value in disease_ids if str(value).strip())
        disease_id = input_data.get("disease_id") or input_data.get("disease")
        if disease_id:
            candidates.append(str(disease_id).strip().upper())
        for candidate in candidates:
            if candidate and candidate not in result:
                result[candidate] = task
    return result


def _select_knowledge_repair_candidates(
    catalogue: list[dict[str, Any]],
    *,
    active_by_disease: dict[str, Task] | None = None,
    limit: int | None = None,
    priorities: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return catalogue rows that should continue through automatic repair."""
    active_by_disease = active_by_disease or {}
    priorities = priorities or {"urgent", "high"}
    selected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    priority_rank = {"urgent": 0, "high": 1, "normal": 2, "none": 3}
    ordered = sorted(
        catalogue,
        key=lambda item: (
            priority_rank.get(str(item.get("repair_priority") or "none"), 9),
            str(item.get("disease_id") or ""),
        ),
    )
    for item in ordered:
        disease_id = str(item.get("disease_id") or "").strip().upper()
        repair_priority = str(item.get("repair_priority") or "none").strip().lower()
        repair_sections = [
            str(section).strip()
            for section in (item.get("repair_sections") or [])
            if str(section).strip()
        ]
        if not disease_id:
            continue
        if repair_priority not in priorities or not repair_sections:
            continue
        active_task = active_by_disease.get(disease_id)
        if active_task is not None:
            skipped.append(
                {
                    "disease_id": disease_id,
                    "reason": "already_running",
                    "existing_task_uuid": getattr(active_task, "task_uuid", None),
                    "existing_status": str(getattr(active_task, "status", "")),
                }
            )
            continue
        selected.append({**item, "disease_id": disease_id, "repair_sections": repair_sections})
        if limit is not None and len(selected) >= limit:
            break
    return selected, skipped


def _knowledge_repair_task_priority(
    requested: str | TaskPriority | None,
    repair_priority: str,
) -> TaskPriority:
    if isinstance(requested, TaskPriority):
        return requested
    requested_value = str(requested or "").strip().lower()
    mapping = {
        "urgent": TaskPriority.URGENT,
        "high": TaskPriority.HIGH,
        "normal": TaskPriority.NORMAL,
        "low": TaskPriority.LOW,
    }
    if requested_value in mapping:
        return mapping[requested_value]
    return TaskPriority.URGENT if repair_priority == "urgent" else TaskPriority.HIGH


def _profile_repair_sections_by_language(
    briefs: list[Any],
    disease: dict[str, Any],
) -> dict[str, list[str]]:
    """Plan localized repairs so a complete translation is never regenerated."""
    schema = disease["profile_schema"]
    ordered_fields = ["brief", *schema["required_fields"], *schema["optional_fields"]]
    by_language = {
        str(getattr(brief, "language", None) or (brief.get("language") if isinstance(brief, dict) else "")): brief
        for brief in briefs
    }
    result: dict[str, list[str]] = {}
    for language in ("en", "zh"):
        brief = by_language.get(language)
        if brief is None:
            result[language] = list(ordered_fields)
            continue
        if not _brief_uses_current_pipeline(brief):
            result[language] = list(ordered_fields)
            continue
        assessment = assess_knowledge_brief(brief, language, disease=disease)
        targets = set(assessment.missing_required_fields)
        if not assessment.fields["brief"].available:
            targets.add("brief")
        result[language] = [field for field in ordered_fields if field in targets]
    return result


def _locked_existing_brief_result(brief: Any, disease: dict[str, Any]) -> dict[str, Any]:
    """Wrap a complete localized brief without invoking the model."""
    metadata = getattr(brief, "metadata_", None) or getattr(brief, "metadata", None) or {}
    payload = {
        "disease_id": getattr(brief, "disease_id", None) or disease.get("disease_id"),
        "language": getattr(brief, "language", None),
        "brief": getattr(brief, "brief", None),
        "definition": getattr(brief, "definition", None),
        "clinical_features": getattr(brief, "clinical_features", None),
        "clinical_summary": getattr(brief, "clinical_summary", None),
        "epidemiology": getattr(brief, "epidemiology", None),
        "transmission": getattr(brief, "transmission", None),
        "prevention": getattr(brief, "prevention", None),
        "surveillance_note": getattr(brief, "surveillance_note", None),
        "risk_groups": getattr(brief, "risk_groups", None),
        "source_ids": getattr(brief, "source_ids", None) or [],
        "source_attribution": getattr(brief, "source_attribution", None) or [],
        "disclaimer": getattr(brief, "disclaimer", None),
        "model": getattr(brief, "model", None),
        "status": getattr(brief, "status", None),
        "source_confidence": getattr(brief, "source_confidence", None),
        "quality_score": getattr(brief, "quality_score", None),
        "review_notes": getattr(brief, "review_notes", None),
        "metadata": dict(metadata) if isinstance(metadata, dict) else {},
    }
    return {
        "payload": payload,
        "trace": {
            "generator": "locked_existing",
            "language": payload["language"],
            "model": None,
            "provider": None,
            "token_usage": {},
            "duration": 0.0,
            "error": None,
            "cache_hit": False,
        },
    }


def _merge_repair_payload(
    payload: dict[str, Any],
    existing: Any | None,
    target_sections: list[str],
    disease: dict[str, Any],
) -> dict[str, Any]:
    """Lock already-published fields while replacing only requested gaps."""
    merged = _use_source_id_citation_markers(dict(payload))
    if existing is not None:
        existing_payload = _use_source_id_citation_markers(
            {
                "brief": getattr(existing, "brief", None),
                "definition": getattr(existing, "definition", None),
                "clinical_features": getattr(existing, "clinical_features", None),
                "epidemiology": getattr(existing, "epidemiology", None),
                "transmission": getattr(existing, "transmission", None),
                "prevention": getattr(existing, "prevention", None),
                "surveillance_note": getattr(existing, "surveillance_note", None),
                "risk_groups": getattr(existing, "risk_groups", None),
                "source_attribution": getattr(existing, "source_attribution", None) or [],
            }
        )
        for field in ("brief", "definition", "clinical_features", "epidemiology", "transmission", "prevention", "surveillance_note", "risk_groups"):
            if field not in target_sections:
                merged[field] = existing_payload.get(field)
        source_attribution: list[dict[str, Any]] = []
        seen_sources: set[str] = set()
        for source in [
            *(merged.get("source_attribution") or []),
            *(existing_payload.get("source_attribution") or []),
        ]:
            if not isinstance(source, dict):
                continue
            key = str(source.get("source_id") or source.get("id") or source.get("url") or "")
            if not key or key in seen_sources:
                continue
            seen_sources.add(key)
            source_attribution.append(source)
        merged["source_attribution"] = source_attribution
        merged["clinical_summary"] = merged.get("clinical_features")
    merged["status"] = "published"
    merged["metadata"] = {
        **(merged.get("metadata") or {}),
        "profile_schema": disease["profile_schema"],
        "target_sections": target_sections,
        "repair_mode": existing is not None,
    }
    merged = normalize_knowledge_citations(
        merged,
        marker_mode="source_id",
        prune_uncited_sources=True,
    )
    merged, _ = apply_knowledge_quality_gate(merged)
    citation_failures = validate_knowledge_citations(
        merged,
        fields=target_sections,
    )
    merged["metadata"] = {
        **(merged.get("metadata") or {}),
        "pipeline_version": KNOWLEDGE_PIPELINE_VERSION,
        "knowledge_schema_version": KNOWLEDGE_SCHEMA_VERSION,
        "evidence_policy_version": EVIDENCE_POLICY_VERSION,
        "citation_validation": {
            "valid": not citation_failures,
            "validated_fields": list(target_sections),
            "failures": citation_failures,
        },
    }
    if citation_failures:
        merged["status"] = "requires_review"
    return merged


def _use_source_id_citation_markers(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert sequential citations to stable source IDs before section merging."""
    result = dict(payload)
    sources = result.get("source_attribution") if isinstance(result.get("source_attribution"), list) else []
    marker_to_source_id: dict[int, int] = {}
    for position, source in enumerate(sources, start=1):
        if not isinstance(source, dict):
            continue
        source_id = source.get("source_id") or source.get("id")
        try:
            source_id = int(source_id)
        except (TypeError, ValueError):
            continue
        marker = source.get("citation_index") or position
        try:
            marker_to_source_id[int(marker)] = source_id
        except (TypeError, ValueError):
            continue

    def replace(match: re.Match[str]) -> str:
        marker = int(match.group(1))
        return f"[{marker_to_source_id.get(marker, marker)}]"

    for field in ("brief", "definition", "clinical_features", "epidemiology", "transmission", "prevention", "surveillance_note", "risk_groups"):
        value = result.get(field)
        if isinstance(value, str):
            result[field] = re.sub(r"\[(\d+)\]", replace, value)
    return result


async def _generate_brief_result(
    generator: AIDiseaseBriefGenerator | ReviewedDiseaseBriefGenerator,
    *,
    disease: dict[str, Any],
    sources: list[dict[str, Any]],
    language: str,
) -> dict[str, Any]:
    return await generator.generate_with_trace(
        disease=disease,
        sources=sources,
        language=language,
    )


async def _translate_brief_result(
    generator: Any,
    *,
    disease: dict[str, Any],
    english_payload: dict[str, Any],
    sources: list[dict[str, Any]],
    target_sections: list[str],
) -> dict[str, Any]:
    translate = getattr(generator, "translate_from_payload_with_trace", None)
    if not callable(translate):
        raise AttributeError("Generator does not support grounded translation")
    return await translate(
        disease=disease,
        english_payload=english_payload,
        sources=sources,
        target_sections=target_sections,
        language="zh",
    )


def _translation_source_usable(result: dict[str, Any] | None) -> bool:
    if not isinstance(result, dict):
        return False
    trace = result.get("trace") if isinstance(result.get("trace"), dict) else {}
    if trace.get("error"):
        return False
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    citation_repair = metadata.get("citation_repair") if isinstance(metadata.get("citation_repair"), dict) else {}
    final_failures = citation_repair.get("final_failures")
    if isinstance(final_failures, list) and final_failures:
        return False
    trace_failures = trace.get("citation_failures")
    if isinstance(trace_failures, list) and trace_failures:
        return False
    return bool(payload)


def _model_center_route_failure(result: dict[str, Any] | None) -> bool:
    if not isinstance(result, dict):
        return False
    trace = result.get("trace") if isinstance(result.get("trace"), dict) else {}
    error = str(trace.get("error") or "").lower()
    return any(
        marker in error
        for marker in (
            "agent completion failed",
            "no candidate model available",
            "all models failed",
            "rate limit",
            "timeout",
            "temporarily unavailable",
        )
    )


def _skipped_language_after_route_failure(
    *,
    disease: dict[str, Any],
    language: str,
    target_sections: list[str],
    upstream_language: str,
) -> dict[str, Any]:
    payload = {
        "disease_id": disease.get("disease_id"),
        "language": language,
        "brief": None,
        "definition": None,
        "clinical_features": None,
        "clinical_summary": None,
        "epidemiology": None,
        "transmission": None,
        "prevention": None,
        "surveillance_note": None,
        "risk_groups": None,
        "source_ids": [],
        "source_attribution": [],
        "model": "ai-model-center",
        "status": "requires_review",
        "source_confidence": "low",
        "quality_score": 0.0,
        "review_notes": (
            f"Skipped {language.upper()} generation because "
            f"{upstream_language.upper()} already failed at the model route."
        ),
        "metadata": {
            "generator": "skipped_after_model_route_failure",
            "target_sections": target_sections,
            "profile_schema": disease.get("profile_schema"),
        },
    }
    return {
        "payload": payload,
        "trace": {
            "generator": "skipped_after_model_route_failure",
            "language": language,
            "model": None,
            "provider": None,
            "token_usage": {},
            "duration": 0.0,
            "error": payload["review_notes"],
            "cache_hit": False,
        },
    }


def _fetch_sources_with_report(
    fetcher: DiseaseKnowledgeFetcher,
    disease: dict[str, Any],
    *,
    enabled_sources: list[str],
    target_sections: list[str],
) -> SourceFetchReport:
    if hasattr(fetcher, "fetch_with_report"):
        return fetcher.fetch_with_report(
            disease,
            enabled_sources=enabled_sources,
            target_sections=target_sections,
        )
    candidates = fetcher.fetch(
        disease,
        enabled_sources=enabled_sources,
        target_sections=target_sections,
    )
    return SourceFetchReport(
        candidates=list(candidates),
        adapter_outcomes={source: "success" for source in enabled_sources},
        adapter_durations={},
    )


def _normalize_generator_mode(mode: str | None) -> str:
    value = (mode or "ai").strip().lower()
    if value not in {"ai", "auto"}:
        return "ai"
    return value


def _generator_for_mode(
    mode: str, disease_id: str
) -> AIDiseaseBriefGenerator | ReviewedDiseaseBriefGenerator:
    if mode == "auto":
        reviewed = ReviewedDiseaseBriefGenerator()
        if reviewed.has_profile(disease_id):
            return reviewed
    return AIDiseaseBriefGenerator()


async def _log_task(
    task_uuid: Optional[str],
    *,
    entry_type: str,
    title: str,
    content: str,
    metadata: Optional[dict[str, Any]] = None,
    success: bool = True,
    prompt: Optional[str] = None,
    response: Optional[str] = None,
    model_used: Optional[str] = None,
    tokens_used: Optional[int] = None,
    duration: Optional[float] = None,
) -> None:
    if not task_uuid:
        return
    await task_manager.add_workbook_entry(
        task_uuid,
        entry_type=entry_type,
        title=title,
        content=content,
        content_type="text",
        prompt=prompt,
        response=response,
        model_used=model_used,
        tokens_used=tokens_used,
        duration=duration,
        success=success,
        metadata=metadata or {},
    )


def _total_tokens_from_usage(token_usage: Any) -> Optional[int]:
    if not isinstance(token_usage, dict):
        return None
    total = token_usage.get("total")
    if isinstance(total, int):
        return total
    if isinstance(total, float):
        return int(total)
    return None


async def _raise_if_cancelled(task_uuid: Optional[str]) -> None:
    if task_uuid and await task_manager.is_cancel_requested(task_uuid):
        raise TaskCancelledError("Cancellation requested by user")


class DiseaseKnowledgeUpdateService:
    """Coordinates source fetch, brief generation, and persistence for one disease."""

    def __init__(
        self,
        *,
        disease_csv_path: Path = DEFAULT_DISEASE_CSV,
        fetcher: DiseaseKnowledgeFetcher | None = None,
    ) -> None:
        self.disease_csv_path = disease_csv_path
        self.fetcher = fetcher or DiseaseKnowledgeFetcher()
        self._disease_cache: list[dict[str, Any]] | None = None
        self._ontology = load_disease_ontology()

    def load_standard_diseases(self) -> list[dict[str, Any]]:
        if self._disease_cache is not None:
            return list(self._disease_cache)
        self._disease_cache = load_standard_diseases(self.disease_csv_path)
        return list(self._disease_cache)

    async def enqueue_repair_tasks(
        self,
        db,
        *,
        source_groups: list[str] | None = None,
        force: bool = False,
        generator_mode: str = "ai",
        priority: str | TaskPriority | None = None,
        limit: int | None = None,
        requested_by: str = "knowledge-auto-repair",
        initiated_via: str = "knowledge-auto-repair",
    ) -> dict[str, Any]:
        """Queue automatic model-center repairs for incomplete knowledge profiles."""
        source_groups = expand_sources(source_groups)
        result = await db.execute(
            select(Task)
            .where(
                Task.task_type.in_(
                    [
                        TaskType.UPDATE_DISEASE_KNOWLEDGE,
                        TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES,
                    ]
                ),
                Task.status.in_(ACTIVE_KNOWLEDGE_TASK_STATUSES),
            )
            .order_by(Task.created_at.asc())
        )
        active_by_disease = _active_knowledge_tasks_by_disease(
            list(result.scalars().all())
        )
        catalogue = await self.list_catalogue(db)
        candidates, skipped = _select_knowledge_repair_candidates(
            catalogue,
            active_by_disease=active_by_disease,
            limit=limit,
        )

        created: list[Task] = []
        for item in candidates:
            disease_id = str(item["disease_id"])
            repair_sections = list(item.get("repair_sections") or [])
            repair_priority = str(item.get("repair_priority") or "high")
            task_priority = _knowledge_repair_task_priority(priority, repair_priority)
            name_en = str(item.get("name_en") or disease_id)
            task_name = f"Repair {name_en} knowledge profile"
            description = (
                f"Automatic knowledge repair for {disease_id}: "
                f"{', '.join(repair_sections)}"
            )
            task = await task_manager.create_task(
                task_type=TaskType.UPDATE_DISEASE_KNOWLEDGE,
                task_name=task_name,
                priority=task_priority,
                description=description,
                input_data={
                    "disease_id": disease_id,
                    "disease_ids": [disease_id],
                    "source_groups": source_groups,
                    "source": source_groups,
                    "force": force,
                    "generator": _normalize_generator_mode(generator_mode),
                    "targeted_repair": True,
                    "repair_sections": repair_sections,
                    "repair_priority": repair_priority,
                    "knowledge_completeness": item.get("knowledge_completeness"),
                    "knowledge_display_mode": item.get("knowledge_display_mode"),
                    "initiated_via": initiated_via,
                    "requested_by": requested_by,
                },
                tags=["knowledge", "auto_repair"],
            )
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="Automatic Knowledge Repair Queued",
                content=(
                    f"Queued model-center repair for {disease_id}. "
                    f"Target sections: {', '.join(repair_sections)}."
                ),
                content_type="text",
                metadata={
                    "disease_id": disease_id,
                    "repair_sections": repair_sections,
                    "repair_priority": repair_priority,
                    "source_groups": source_groups,
                    "generator": _normalize_generator_mode(generator_mode),
                    "force": force,
                    "workflow_stage": "knowledge_repair_queued",
                },
            )
            task = await task_manager.update_task_status(task.task_uuid, TaskStatus.QUEUED) or task
            created.append(task)

        return {
            "created_count": len(created),
            "skipped_count": len(skipped),
            "created_tasks": created,
            "skipped": skipped,
            "candidate_count": len(candidates),
            "source_groups": source_groups,
            "generator": _normalize_generator_mode(generator_mode),
            "force": force,
        }

    async def enqueue_source_refresh_tasks(
        self,
        db,
        *,
        disease_ids: list[str] | None = None,
        source_groups: list[str] | None = None,
        force: bool = True,
        priority: str | TaskPriority | None = None,
        limit: int | None = None,
        requested_by: str = "knowledge-source-refresh",
        initiated_via: str = "knowledge-source-refresh",
    ) -> dict[str, Any]:
        """Queue source-only refresh work before model-center generation."""
        source_groups = expand_sources(source_groups)
        result = await db.execute(
            select(Task)
            .where(
                Task.task_type.in_(
                    [
                        TaskType.UPDATE_DISEASE_KNOWLEDGE,
                        TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES,
                    ]
                ),
                Task.status.in_(ACTIVE_KNOWLEDGE_TASK_STATUSES),
            )
            .order_by(Task.created_at.asc())
        )
        active_by_disease = _active_knowledge_tasks_by_disease(
            list(result.scalars().all())
        )
        requested_ids = {
            str(disease_id).strip().upper()
            for disease_id in (disease_ids or [])
            if str(disease_id).strip()
        }
        catalogue = await self.list_catalogue(db)
        candidates = [
            item
            for item in catalogue
            if not requested_ids or str(item.get("disease_id") or "").upper() in requested_ids
        ]
        if limit is not None:
            candidates = candidates[: max(0, int(limit))]

        created: list[Task] = []
        skipped: list[dict[str, Any]] = []
        for item in candidates:
            disease_id = str(item.get("disease_id") or "").strip().upper()
            if not disease_id:
                continue
            active_task = active_by_disease.get(disease_id)
            if active_task is not None:
                skipped.append(
                    {
                        "disease_id": disease_id,
                        "reason": "active_task_exists",
                        "task_uuid": active_task.task_uuid,
                    }
                )
                continue
            name_en = str(item.get("name_en") or disease_id)
            task = await task_manager.create_task(
                task_type=TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES,
                task_name=f"Refresh {name_en} knowledge sources",
                priority=priority or TaskPriority.NORMAL,
                description=f"Fetch and organize knowledge sources for {disease_id}.",
                input_data={
                    "disease_id": disease_id,
                    "disease_ids": [disease_id],
                    "source_groups": source_groups,
                    "source": source_groups,
                    "force": force,
                    "source_only": True,
                    "initiated_via": initiated_via,
                    "requested_by": requested_by,
                },
                tags=["knowledge", "source_refresh"],
            )
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title="Knowledge Source Refresh Queued",
                content=(
                    f"Queued source-only refresh for {disease_id}. "
                    f"Source groups: {', '.join(source_groups) or 'default'}."
                ),
                content_type="text",
                metadata={
                    "disease_id": disease_id,
                    "source_groups": source_groups,
                    "force": force,
                    "workflow_stage": "knowledge_source_refresh_queued",
                },
            )
            task = await task_manager.update_task_status(task.task_uuid, TaskStatus.QUEUED) or task
            created.append(task)

        return {
            "created_count": len(created),
            "skipped_count": len(skipped),
            "created_tasks": created,
            "skipped": skipped,
            "candidate_count": len(candidates),
            "source_groups": source_groups,
            "force": force,
        }

    async def stage_queued_repairs_as_source_refresh_tasks(
        self,
        db,
        *,
        limit: int | None = None,
        requested_by: str = "knowledge-source-first-staging",
    ) -> dict[str, Any]:
        """Convert queued AI repairs into source-first refresh tasks."""
        query = (
            select(Task)
            .where(
                Task.task_type == TaskType.UPDATE_DISEASE_KNOWLEDGE,
                Task.status.in_([TaskStatus.PENDING, TaskStatus.QUEUED]),
            )
            .order_by(Task.created_at.asc())
            .with_for_update(skip_locked=True)
        )
        if limit is not None:
            query = query.limit(max(0, int(limit)))
        tasks = list((await db.execute(query)).scalars().all())
        staged: list[str] = []
        skipped: list[dict[str, Any]] = []
        for task in tasks:
            input_data = dict(task.input_data or {})
            tags = set(task.tags or [])
            disease_id = str(
                input_data.get("disease_id")
                or input_data.get("disease")
                or ""
            ).strip().upper()
            if not disease_id:
                skipped.append({"task_uuid": task.task_uuid, "reason": "missing_disease_id"})
                continue
            if not input_data.get("targeted_repair") and "auto_repair" not in tags:
                skipped.append({"task_uuid": task.task_uuid, "reason": "not_auto_repair"})
                continue
            if input_data.get("source_only") or "source_refresh" in tags:
                skipped.append({"task_uuid": task.task_uuid, "reason": "already_source_first"})
                continue

            input_data["source_only"] = True
            input_data["enqueue_ai_after_source_refresh"] = True
            input_data["source_first_staged_from"] = TaskType.UPDATE_DISEASE_KNOWLEDGE.value
            input_data["source_first_staged_by"] = requested_by
            input_data["force"] = bool(input_data.get("force", False))
            metadata = dict(task.metadata_ or {})
            metadata["source_first_staging"] = {
                "at": datetime.now(timezone.utc).isoformat(),
                "requested_by": requested_by,
                "original_task_type": TaskType.UPDATE_DISEASE_KNOWLEDGE.value,
            }
            task.task_type = TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES
            task.task_name = f"Refresh sources before {task.task_name}"
            task.description = (
                f"Source-first stage for {disease_id}; model repair will be queued "
                "after source quality is known."
            )
            task.input_data = input_data
            task.tags = sorted({*tags, "knowledge", "source_refresh", "source_first"})
            task.metadata_ = metadata
            task.retry_count = 0
            task.max_retries = max(int(task.max_retries or 0), 5)
            task.last_error = None
            staged.append(task.task_uuid)
        await db.commit()
        for task_uuid in staged:
            await task_manager.add_workbook_entry(
                task_uuid,
                entry_type="info",
                title="Knowledge Repair Staged As Source Refresh",
                content=(
                    "Queued AI repair was converted to a source-first refresh task. "
                    "A follow-up model repair will be queued only if source evidence is sufficient."
                ),
                content_type="text",
                metadata={
                    "workflow_stage": "knowledge_source_first_staged",
                    "requested_by": requested_by,
                },
            )
        return {
            "staged_count": len(staged),
            "staged_task_uuids": staged,
            "skipped": skipped,
        }

    def _find_disease(self, disease_id: str) -> dict[str, Any]:
        wanted = (disease_id or "").strip().upper()
        for row in self.load_standard_diseases():
            if str(row.get("disease_id") or "").upper() == wanted:
                disease = attach_profile_schema(row)
                try:
                    detail = self._ontology.concept_detail(wanted)
                except KeyError:
                    return disease
                catalogue = {
                    str(item.get("disease_id") or "").upper(): item
                    for item in self.load_standard_diseases()
                }
                related_entities: list[dict[str, Any]] = []
                query_aliases: list[str] = []
                seen_aliases: set[str] = set()

                def add_alias(value: Any) -> None:
                    alias = " ".join(str(value or "").split()).strip()
                    key = alias.casefold()
                    if alias and key not in seen_aliases:
                        seen_aliases.add(key)
                        query_aliases.append(alias)

                labels = detail.get("labels") if isinstance(detail.get("labels"), dict) else {}
                for label in labels.values():
                    add_alias(label)
                for series in detail.get("source_series") or []:
                    if not isinstance(series, dict):
                        continue
                    local_codes = {
                        str(code).strip().casefold()
                        for code in series.get("local_codes") or []
                        if str(code).strip()
                    }
                    for label in series.get("local_labels") or []:
                        # Registry codes are valuable for series mapping but
                        # are often ambiguous in public web/biomedical search
                        # (for example SINAN HIVE). A label that merely repeats
                        # a local code must not become a knowledge-query alias.
                        if str(label).strip().casefold() not in local_codes:
                            add_alias(label)
                relations = detail.get("relations") if isinstance(detail.get("relations"), dict) else {}
                for relation in relations.get("outgoing") or []:
                    target = relation.get("to_ref") if isinstance(relation.get("to_ref"), dict) else {}
                    if target.get("kind") != "concept" or not relation.get("hierarchical"):
                        continue
                    parent_id = str(target.get("id") or "").upper()
                    parent = catalogue.get(parent_id) or {}
                    related_entities.append(
                        {
                            "disease_id": parent_id,
                            "relation_type": relation.get("type"),
                            "name_en": parent.get("name_en"),
                            "name_zh": parent.get("name_zh"),
                            "allowed_shared_sections": ["definition", "transmission", "prevention"],
                        }
                    )
                return {
                    **disease,
                    "query_aliases": query_aliases[:20],
                    "ontology_context": {
                        "definition": detail.get("definition"),
                        "facet_tags": detail.get("facet_tags") or {},
                        "related_entities": related_entities,
                    },
                }
        raise ValueError(f"Disease not found in standard catalogue: {disease_id}")

    async def list_catalogue(self, db, *, search: str | None = None) -> list[dict[str, Any]]:
        diseases = self.load_standard_diseases()
        if search:
            needle = search.strip().lower()
            if needle:
                diseases = [
                    disease
                    for disease in diseases
                    if needle in str(disease.get("disease_id") or "").lower()
                    or needle in str(disease.get("name_en") or "").lower()
                    or needle in str(disease.get("name_zh") or "").lower()
                    or needle in str(disease.get("description") or "").lower()
                    or needle in str(disease.get("category") or "").lower()
                ]

        brief_rows = (
            await db.execute(
                select(DiseaseKnowledgeBrief)
                .where(DiseaseKnowledgeBrief.status.in_(["published", "draft", "requires_review"]))
                .order_by(DiseaseKnowledgeBrief.disease_id.asc(), DiseaseKnowledgeBrief.language.asc())
            )
        ).scalars().all()
        source_counts_rows = (
            await db.execute(
                select(
                    DiseaseKnowledgeSource.disease_id,
                    func.count(DiseaseKnowledgeSource.id).label("source_count"),
                )
                .where(
                    DiseaseKnowledgeSource.review_status != "rejected",
                    DiseaseKnowledgeSource.status == "active",
                )
                .group_by(DiseaseKnowledgeSource.disease_id)
            )
        ).all()

        briefs_by_disease: dict[str, dict[str, DiseaseKnowledgeBrief]] = {}
        for brief in brief_rows:
            briefs_by_disease.setdefault(brief.disease_id, {})[brief.language] = brief

        source_counts = {row.disease_id: int(row.source_count or 0) for row in source_counts_rows}

        items: list[dict[str, Any]] = []
        for raw_disease in diseases:
            disease = attach_profile_schema(raw_disease)
            disease_id = str(disease.get("disease_id") or "")
            language_briefs = briefs_by_disease.get(disease_id, {})
            language_quality = {
                lang: assess_knowledge_brief(brief, lang, disease=disease)
                for lang, brief in language_briefs.items()
            }
            repair_sections = _profile_repair_sections(
                list(language_briefs.values()), disease
            )
            published_languages = sorted(
                [
                    lang
                    for lang, brief in language_briefs.items()
                    if knowledge_brief_publication_tier(brief) == "published"
                    and language_quality[lang].profile_available
                ]
            )
            blocked_languages = sorted(
                [
                    lang
                    for lang, brief in language_briefs.items()
                    if knowledge_brief_publication_tier(brief) == "blocked"
                ]
            )
            stored_knowledge_status = resolve_disease_knowledge_status(language_briefs.values())
            knowledge_status = (
                "published"
                if published_languages
                else "requires_review"
                if language_briefs and stored_knowledge_status in {"published", "requires_review"}
                else stored_knowledge_status
            )
            completeness_values = [
                language_quality[lang].completeness for lang in published_languages
            ]
            knowledge_completeness = (
                round(sum(completeness_values) / len(completeness_values), 3)
                if completeness_values
                else 0.0
            )
            knowledge_display_mode = (
                "full"
                if len(published_languages) == 2
                and all(language_quality[lang].display_mode == "full" for lang in published_languages)
                else "partial"
                if published_languages
                else "blocked"
            )

            latest_updated_at = None
            for brief in language_briefs.values():
                if brief.updated_at and (latest_updated_at is None or brief.updated_at > latest_updated_at):
                    latest_updated_at = brief.updated_at

            items.append(
                {
                    "disease_id": disease_id,
                    "name_en": disease.get("name_en"),
                    "name_zh": disease.get("name_zh"),
                    "category": disease.get("category"),
                    "icd_10": disease.get("icd_10"),
                    "icd_11": disease.get("icd_11"),
                    "description": disease.get("description"),
                    "slug": disease.get("slug"),
                    "knowledge_status": knowledge_status,
                    "knowledge_updated_at": latest_updated_at.isoformat() if latest_updated_at else None,
                    "published_languages": published_languages,
                    "knowledge_display_mode": knowledge_display_mode,
                    "knowledge_completeness": knowledge_completeness,
                    "knowledge_profile_type": disease["knowledge_profile_type"],
                    "knowledge_profile_schema": disease["profile_schema"],
                    "repair_sections": repair_sections,
                    "repair_priority": (
                        "urgent" if not language_briefs else "high" if repair_sections else "none"
                    ),
                    "language_quality": {
                        lang: quality.to_dict() for lang, quality in language_quality.items()
                    },
                    "blocked_languages": blocked_languages,
                    "source_count": source_counts.get(disease_id, 0),
                    "brief_statuses": {
                        lang: brief.status for lang, brief in sorted(language_briefs.items())
                    },
                    "brief_tiers": {
                        lang: knowledge_brief_publication_tier(brief)
                        for lang, brief in sorted(language_briefs.items())
                    },
                }
            )
        return items

    async def get_detail(self, db, disease_id: str) -> dict[str, Any]:
        """Return a knowledge-base detail payload for one disease."""
        disease = self._find_disease(disease_id)
        disease_code = str(disease.get("disease_id") or "").upper()

        brief_rows = (
            await db.execute(
                select(DiseaseKnowledgeBrief)
                .where(DiseaseKnowledgeBrief.disease_id == disease_code)
                .order_by(DiseaseKnowledgeBrief.language.asc())
            )
        ).scalars().all()
        source_rows = (
            await db.execute(
                select(DiseaseKnowledgeSource)
                .where(DiseaseKnowledgeSource.disease_id == disease_code)
                .order_by(DiseaseKnowledgeSource.review_status.asc(), DiseaseKnowledgeSource.source_type.asc(), DiseaseKnowledgeSource.id.asc())
            )
        ).scalars().all()
        source_dicts = [source_to_dict(source) for source in source_rows]
        evidence_quality = assess_knowledge_evidence(source_dicts)
        sources_by_id = {
            int(source["id"]): source
            for source in source_dicts
            if source.get("id") is not None
        }

        quality_by_language = {
            brief.language: assess_knowledge_brief(brief, brief.language, disease=disease)
            for brief in brief_rows
        }
        repair_sections = _profile_repair_sections(list(brief_rows), disease)
        published_languages = sorted([
            brief.language
            for brief in brief_rows
            if knowledge_brief_publication_tier(brief) == "published"
            and quality_by_language[brief.language].profile_available
        ])
        blocked_languages = sorted(
            [brief.language for brief in brief_rows if knowledge_brief_publication_tier(brief) == "blocked"]
        )
        stored_knowledge_status = resolve_disease_knowledge_status(brief_rows)
        knowledge_status = (
            "published"
            if published_languages
            else "requires_review"
            if brief_rows and stored_knowledge_status in {"published", "requires_review"}
            else stored_knowledge_status
        )

        latest_updated_at = None
        for brief in brief_rows:
            if brief.updated_at and (latest_updated_at is None or brief.updated_at > latest_updated_at):
                latest_updated_at = brief.updated_at

        brief_statuses = {brief.language: brief.status for brief in brief_rows}
        brief_tiers = {brief.language: knowledge_brief_publication_tier(brief) for brief in brief_rows}
        review_status_counts: dict[str, int] = {}
        source_type_counts: dict[str, int] = {}
        for source in source_rows:
            review_status_counts[source.review_status] = review_status_counts.get(source.review_status, 0) + 1
            source_type_counts[source.source_type] = source_type_counts.get(source.source_type, 0) + 1

        brief_payloads: list[dict[str, Any]] = []
        for brief in brief_rows:
            payload = normalize_knowledge_citations(
                {
                    "language": brief.language,
                    "status": brief.status,
                    "brief_tier": knowledge_brief_publication_tier(brief),
                    "block_reason": knowledge_brief_block_reason(brief),
                    "source_confidence": brief.source_confidence,
                    "updated_at": brief.updated_at.isoformat() if brief.updated_at else None,
                    "brief": brief.brief,
                    "definition": brief.definition,
                    "clinical_features": brief.clinical_features,
                    "clinical_summary": brief.clinical_summary,
                    "epidemiology": brief.epidemiology,
                    "transmission": brief.transmission,
                    "prevention": brief.prevention,
                    "surveillance_note": brief.surveillance_note,
                    "risk_groups": brief.risk_groups,
                    "disclaimer": brief.disclaimer,
                    "model": brief.model,
                    "quality_score": brief.quality_score,
                    "review_notes": brief.review_notes,
                    "source_ids": brief.source_ids or [],
                    "source_attribution": _enrich_source_attribution(
                        brief.source_attribution or [],
                        sources_by_id,
                    ),
                    "metadata": brief.metadata_ or {},
                }
            )
            payload["quality"] = quality_by_language[brief.language].to_dict()
            brief_payloads.append(payload)

        knowledge_display_mode = (
            "full"
            if len(published_languages) == 2
            and all(quality_by_language[lang].display_mode == "full" for lang in published_languages)
            else "partial"
            if published_languages
            else "blocked"
        )
        completeness_values = [
            quality_by_language[lang].completeness for lang in published_languages
        ]

        return {
            "disease_id": disease_code,
            "name_en": disease.get("name_en"),
            "name_zh": disease.get("name_zh"),
            "category": disease.get("category"),
            "icd_10": disease.get("icd_10"),
            "icd_11": disease.get("icd_11"),
            "description": disease.get("description"),
            "slug": disease.get("slug"),
            "knowledge_status": knowledge_status,
            "knowledge_updated_at": latest_updated_at.isoformat() if latest_updated_at else None,
            "published_languages": published_languages,
            "knowledge_display_mode": knowledge_display_mode,
            "knowledge_completeness": (
                round(sum(completeness_values) / len(completeness_values), 3)
                if completeness_values
                else 0.0
            ),
            "knowledge_profile_type": disease["knowledge_profile_type"],
            "knowledge_profile_schema": disease["profile_schema"],
            "repair_sections": repair_sections,
            "repair_priority": (
                "urgent" if not brief_rows else "high" if repair_sections else "none"
            ),
            "language_quality": {
                lang: quality.to_dict() for lang, quality in quality_by_language.items()
            },
            "evidence_quality": evidence_quality.to_dict(),
            "blocked_languages": blocked_languages,
            "source_count": len(source_rows),
            "brief_statuses": brief_statuses,
            "brief_tiers": brief_tiers,
            "summary": {
                "brief_count": len(brief_rows),
                "source_count": len(source_rows),
                "published_briefs": sum(1 for brief in brief_rows if knowledge_brief_publication_tier(brief) == "published"),
                "public_profile_briefs": len(published_languages),
                "blocked_briefs": sum(1 for brief in brief_rows if knowledge_brief_publication_tier(brief) == "blocked"),
                "draft_briefs": sum(1 for brief in brief_rows if brief.status == "draft"),
                "review_briefs": sum(1 for brief in brief_rows if brief.status == "requires_review"),
                "source_review_counts": review_status_counts,
                "source_type_counts": source_type_counts,
            },
            "briefs": brief_payloads,
            "sources": source_dicts,
        }

    async def update_disease(
        self,
        disease_id: str,
        *,
        enabled_sources: list[str] | None = None,
        force: bool = False,
        generator_mode: str = "auto",
        target_languages: list[str] | None = None,
        dry_run: bool = False,
        task_uuid: str | None = None,
        source_only: bool = False,
        refresh_existing_on_source_change: bool | None = None,
        source_refreshed_task_uuid: str | None = None,
    ) -> dict[str, Any]:
        disease = self._find_disease(disease_id)
        generator_mode = _normalize_generator_mode(generator_mode)
        enabled_sources = expand_sources(enabled_sources)
        generator = _generator_for_mode(generator_mode, disease["disease_id"])
        ai_config = get_config().ai
        refresh_existing_on_source_change = bool(
            force
            or (
                ai_config.knowledge_refresh_existing_on_source_change
                if refresh_existing_on_source_change is None
                else refresh_existing_on_source_change
            )
        )

        await _log_task(
            task_uuid,
            entry_type="info",
            title="Knowledge Update Started",
            content=(
                f"Disease: {disease['disease_id']}\n"
                f"Source groups: {', '.join(enabled_sources) or 'none'}\n"
                f"Force refresh: {'yes' if force else 'no'}\n"
                f"Generator: {generator_mode}\n"
                f"Dry run: {'yes' if dry_run else 'no'}\n"
                f"Source-only: {'yes' if source_only else 'no'}"
            ),
            metadata={
                "disease_id": disease["disease_id"],
                "source_groups": enabled_sources,
                "force": force,
                "generator": generator_mode,
                "dry_run": dry_run,
                "source_only": source_only,
                "refresh_existing_on_source_change": refresh_existing_on_source_change,
                "source_refreshed_task_uuid": source_refreshed_task_uuid,
                "evidence_limits": _knowledge_evidence_limits(),
                "workflow_stage": "knowledge_start",
            },
        )

        await _raise_if_cancelled(task_uuid)
        if task_uuid:
            await task_manager.update_task_progress(task_uuid, 5)

        if dry_run:
            target_sections = [
                "brief",
                *disease["profile_schema"]["required_fields"],
                *disease["profile_schema"]["optional_fields"],
            ]
            disease_payload = {
                **disease,
                "target_sections": target_sections,
                "evidence_target_sections": target_sections,
            }
            fetch_report = await asyncio.to_thread(
                _fetch_sources_with_report,
                self.fetcher,
                disease_payload,
                enabled_sources=enabled_sources,
                target_sections=target_sections,
            )
            candidates = fetch_report.candidates
            source_dicts = [
                {
                    "id": idx + 1,
                    "disease_id": c.disease_id,
                    "source_type": c.source_type,
                    "source_name": c.source_name,
                    "url": c.url,
                    "resolved_url": getattr(c, "resolved_url", None) or c.url,
                    "title": c.title,
                    "license": c.license,
                    "status": c.status,
                    "review_status": c.review_status,
                    "raw_excerpt": c.raw_excerpt,
                    "content_text": getattr(c, "content_text", None),
                    "content_sections": getattr(c, "content_sections", None) or [],
                    "metadata": c.metadata or {},
                }
                for idx, c in enumerate(candidates)
            ]
            profile_schema = resolve_knowledge_profile_schema(disease_payload)
            evidence_packet = prepare_evidence_packet(
                source_dicts,
                profile_schema,
                target_sections=target_sections,
                **_knowledge_evidence_limits(),
                allowed_source_types=AIDiseaseBriefGenerator.PUBLIC_SOURCE_TYPES,
            )
            source_dicts = list(evidence_packet.sources)
            evidence_quality = evidence_packet.assessment
            if source_only:
                return {
                    "disease_id": disease["disease_id"],
                    "knowledge_profile_type": disease["knowledge_profile_type"],
                    "target_sections": target_sections,
                    "fetched_sources": len(candidates),
                    "selected_sources": len(source_dicts),
                    "evidence_quality": evidence_quality.to_dict(),
                    "adapter_outcomes": fetch_report.adapter_outcomes,
                    "adapter_durations": fetch_report.adapter_durations,
                    "source_only": True,
                    "sources": source_dicts,
                }
            if not evidence_quality.sufficient:
                reason = _evidence_block_reason(source_dicts)
                raise KnowledgeEvidenceInsufficientError(
                    f"{disease['disease_id']} source enrichment exhausted: {reason}. "
                    "No disease brief was generated."
                )
            # Generate languages sequentially so process-wide model cooldowns
            # learned from EN prevent ZH from repeating the same quota or
            # timeout failure immediately.
            generated_results = []
            for language in ("en", "zh"):
                generated_results.append(
                    await _generate_brief_result(
                        generator,
                        disease={**disease_payload, "_evidence_packet_prepared": True},
                        sources=source_dicts,
                        language=language,
                    )
                )
            for result in generated_results:
                result["payload"] = _merge_repair_payload(
                    result["payload"], None, target_sections, disease_payload
                )
            generation_failures = [
                *_generated_profile_failures(generated_results),
                *_bilingual_alignment_failures(generated_results),
            ]
            if generation_failures:
                raise KnowledgeEvidenceInsufficientError(
                    f"{disease['disease_id']} generation did not pass the publication gate: "
                    + " | ".join(generation_failures)
                )
            briefs = [result["payload"] for result in generated_results]
            for brief in briefs:
                brief["metadata"] = {
                    **(brief.get("metadata") or {}),
                    "evidence_quality": evidence_quality.to_dict(),
                }
            return {
                "disease_id": disease["disease_id"],
                "knowledge_profile_type": disease["knowledge_profile_type"],
                "target_sections": target_sections,
                "fetched_sources": len(candidates),
                "evidence_quality": evidence_quality.to_dict(),
                "adapter_outcomes": fetch_report.adapter_outcomes,
                "adapter_durations": fetch_report.adapter_durations,
                "brief_statuses": {brief["language"]: brief["status"] for brief in briefs},
                "brief_previews": {
                    brief["language"]: {
                        "brief": brief.get("brief"),
                        "definition": brief.get("definition"),
                        "clinical_features": brief.get("clinical_features"),
                        "epidemiology": brief.get("epidemiology"),
                        "transmission": brief.get("transmission"),
                        "prevention": brief.get("prevention"),
                        "surveillance_note": brief.get("surveillance_note"),
                        "risk_groups": brief.get("risk_groups"),
                        "model": brief.get("model"),
                    }
                    for brief in briefs
                },
                "sources": source_dicts,
            }

        async with get_database() as db:
            await _acquire_disease_knowledge_lock(db, disease["disease_id"])
            existing = await _existing_sources(db, disease["disease_id"])
            existing_source_dicts = [source_to_dict(row) for row in existing]
            existing_has_public_sources = _has_approved_public_sources(existing_source_dicts)
            source_refresh_required = _sources_need_refresh(existing)
            existing_brief_rows = list(
                (
                    await db.execute(
                        select(DiseaseKnowledgeBrief)
                        .where(DiseaseKnowledgeBrief.disease_id == disease["disease_id"])
                        .order_by(DiseaseKnowledgeBrief.language.asc())
                    )
                ).scalars().all()
            )
            existing_briefs_by_language = {
                brief.language: brief for brief in existing_brief_rows
            }
            target_sections_by_language = _profile_repair_sections_by_language(
                existing_brief_rows, disease
            )
            ordered_sections = [
                "brief",
                *disease["profile_schema"]["required_fields"],
                *disease["profile_schema"]["optional_fields"],
            ]
            if force:
                target_sections_by_language = {
                    language: list(ordered_sections) for language in ("en", "zh")
                }
            if source_only:
                target_sections_by_language = {
                    language: list(ordered_sections) for language in ("en", "zh")
                }
            if target_languages is not None:
                allowed_languages = {
                    str(language).strip().lower()
                    for language in target_languages
                    if str(language).strip().lower() in {"en", "zh"}
                }
                if allowed_languages:
                    target_sections_by_language = {
                        language: fields if language in allowed_languages else []
                        for language, fields in target_sections_by_language.items()
                    }
            target_set = {
                field
                for fields in target_sections_by_language.values()
                for field in fields
            }
            target_sections = [field for field in ordered_sections if field in target_set]
            disease_payload = {
                **disease,
                "target_sections": target_sections,
                "evidence_target_sections": ordered_sections,
            }
            source_refresh_reusable = (
                bool(source_refreshed_task_uuid)
                and not source_only
                and not force
                and existing_has_public_sources
            )
            candidates = []
            fetch_report = SourceFetchReport(
                candidates=[],
                adapter_outcomes={},
                adapter_durations={},
            )
            if (
                force
                or source_only
                or not existing
                or not existing_has_public_sources
                or (target_sections and not source_refresh_reusable)
                or (source_refresh_required and not source_refresh_reusable)
            ):
                fetch_report = await asyncio.to_thread(
                    _fetch_sources_with_report,
                    self.fetcher,
                    disease_payload,
                    enabled_sources=enabled_sources,
                    target_sections=target_sections,
                )
                candidates = fetch_report.candidates
                for candidate in candidates:
                    await _upsert_source(db, candidate)
                if force:
                    await _mark_stale_sources(
                        db,
                        disease["disease_id"],
                        candidates,
                        enabled_sources,
                        fetch_report.adapter_outcomes,
                    )
                await _log_task(
                    task_uuid,
                    entry_type="info",
                    title="Source Fetch Completed",
                    content=(
                        f"Fetched {len(candidates)} candidate source(s) for {disease['disease_id']} "
                        f"from {', '.join(enabled_sources)}"
                    ),
                    metadata={
                        "disease_id": disease["disease_id"],
                        "fetched_sources": len(candidates),
                        "source_groups": enabled_sources,
                        "adapter_outcomes": fetch_report.adapter_outcomes,
                        "adapter_durations": fetch_report.adapter_durations,
                        "target_sections": target_sections,
                        "source_only": source_only,
                        "workflow_stage": "source_fetch_completed",
                    },
                )
            else:
                await _log_task(
                    task_uuid,
                    entry_type="info",
                    title="Source Reuse",
                    content=(
                        f"Reusing {len(existing)} existing source row(s) for {disease['disease_id']} "
                        "because force refresh is disabled."
                    ),
                    metadata={
                        "disease_id": disease["disease_id"],
                        "reused_sources": len(existing),
                        "source_refreshed_task_uuid": source_refreshed_task_uuid,
                        "workflow_stage": "source_reuse",
                    },
                )

            await _raise_if_cancelled(task_uuid)
            if task_uuid:
                await task_manager.update_task_progress(task_uuid, 40)

            source_rows = await _existing_sources(db, disease["disease_id"])
            source_dicts = [source_to_dict(row) for row in source_rows]
            brief_rows = []
            profile_schema = resolve_knowledge_profile_schema(disease_payload)
            direct_packet = prepare_evidence_packet(
                source_dicts,
                profile_schema,
                target_sections=ordered_sections,
                **_knowledge_evidence_limits(),
                allowed_source_types=AIDiseaseBriefGenerator.PUBLIC_SOURCE_TYPES,
            )
            direct_evidence_quality = direct_packet.assessment
            inherited_sources = await _related_parent_sources(db, disease_payload)
            generation_packet = prepare_evidence_packet(
                [*direct_packet.sources, *inherited_sources],
                profile_schema,
                target_sections=ordered_sections,
                **_knowledge_evidence_limits(),
                allowed_source_types=AIDiseaseBriefGenerator.PUBLIC_SOURCE_TYPES,
            )
            evidence_quality = generation_packet.assessment
            generation_sources = list(generation_packet.sources)
            source_packet_manifest_id = generation_packet.manifest.manifest_id

            if source_only:
                source_gap = not evidence_quality.sufficient
                await _log_task(
                    task_uuid,
                    entry_type="warning" if source_gap else "success",
                    title=(
                        "Knowledge Sources Need Enrichment"
                        if source_gap
                        else "Knowledge Sources Refreshed"
                    ),
                    content=(
                        f"Refreshed {len(source_dicts)} direct source row(s) for "
                        f"{disease['disease_id']} without invoking the AI brief generator."
                    ),
                    metadata={
                        "disease_id": disease["disease_id"],
                        "source_count": len(source_dicts),
                        "inherited_source_count": len(inherited_sources),
                        "source_packet_manifest_id": source_packet_manifest_id,
                        "source_gap": source_gap,
                        "evidence_quality": evidence_quality.to_dict(),
                        "direct_evidence_quality": direct_evidence_quality.to_dict(),
                        "adapter_outcomes": fetch_report.adapter_outcomes,
                        "adapter_durations": fetch_report.adapter_durations,
                        "evidence_limits": _knowledge_evidence_limits(),
                        "workflow_stage": "knowledge_sources_refreshed",
                    },
                    success=not source_gap,
                )
                await db.commit()
                if task_uuid:
                    await task_manager.update_task_progress(task_uuid, 100)
                return {
                    "disease_id": disease["disease_id"],
                    "knowledge_profile_type": disease["knowledge_profile_type"],
                    "target_sections": ordered_sections,
                    "fetched_sources": len(candidates),
                    "total_sources": len(source_dicts),
                    "inherited_source_count": len(inherited_sources),
                    "evidence_quality": evidence_quality.to_dict(),
                    "direct_evidence_quality": direct_evidence_quality.to_dict(),
                    "adapter_outcomes": fetch_report.adapter_outcomes,
                    "adapter_durations": fetch_report.adapter_durations,
                    "source_packet_manifest_id": source_packet_manifest_id,
                    "source_gap": source_gap,
                    "source_only": True,
                }

            if not evidence_quality.sufficient:
                reason = _evidence_block_reason([*source_dicts, *inherited_sources])
                await _log_task(
                    task_uuid,
                    entry_type="error",
                    title="Knowledge Evidence Blocked",
                    content=(
                        f"Automatic source enrichment for {disease['disease_id']} did not produce "
                        f"usable evidence ({reason}). No disease brief was generated or persisted."
                    ),
                    metadata={
                        "disease_id": disease["disease_id"],
                        "source_count": len(source_dicts),
                        "reason": reason,
                        "workflow_stage": "knowledge_evidence_blocked",
                    },
                    success=False,
                )
                raise KnowledgeEvidenceInsufficientError(
                    f"{disease['disease_id']} source enrichment exhausted: {reason}. "
                    "No disease brief was generated or persisted."
                )

            if task_uuid:
                await task_manager.update_task_progress(task_uuid, 55)

            if refresh_existing_on_source_change:
                for language in ("en", "zh"):
                    existing_brief = existing_briefs_by_language.get(language)
                    if existing_brief is None:
                        continue
                    previous_packet_id = _brief_metadata(existing_brief).get(
                        "source_packet_manifest_id"
                    )
                    if previous_packet_id != source_packet_manifest_id:
                        target_sections_by_language[language] = list(ordered_sections)
            target_set = {
                field
                for fields in target_sections_by_language.values()
                for field in fields
            }
            target_sections = [field for field in ordered_sections if field in target_set]
            disease_payload = {
                **disease_payload,
                "target_sections": target_sections,
                "evidence_target_sections": ordered_sections,
                "_evidence_packet_prepared": True,
            }

            brief_languages = ("en", "zh")
            for language in brief_languages:
                language_targets = target_sections_by_language[language]
                await _log_task(
                    task_uuid,
                    entry_type="info",
                    title=(
                        f"Generating {language.upper()} Brief"
                        if language_targets
                        else f"Keeping {language.upper()} Brief Locked"
                    ),
                    content=(
                        f"Target sections for {language.upper()}: "
                        f"{', '.join(language_targets) if language_targets else 'none; existing profile is retained'}. "
                        f"Evidence includes {len(source_dicts)} direct and {len(inherited_sources)} scoped parent source row(s)."
                    ),
                    metadata={
                        "disease_id": disease["disease_id"],
                        "language": language,
                        "source_count": len(source_dicts),
                        "inherited_source_count": len(inherited_sources),
                        "target_sections": language_targets,
                        "workflow_stage": f"brief_generation_{language}",
                    },
                )

            generation_languages = [
                language for language in brief_languages if target_sections_by_language[language]
            ]
            results_by_language: dict[str, dict[str, Any]] = {}
            for language in generation_languages:
                language_targets = target_sections_by_language[language]
                if language == "zh" and _model_center_route_failure(results_by_language.get("en")):
                    skipped = _skipped_language_after_route_failure(
                        disease=disease_payload,
                        language=language,
                        target_sections=language_targets,
                        upstream_language="en",
                    )
                    results_by_language[language] = skipped
                    continue
                if (
                    language == "zh"
                    and ai_config.knowledge_translate_zh_from_en
                    and callable(getattr(generator, "translate_from_payload_with_trace", None))
                ):
                    english_result = results_by_language.get("en")
                    if english_result is None and "en" not in generation_languages:
                        existing_en = existing_briefs_by_language.get("en")
                        if existing_en is not None:
                            english_result = _locked_existing_brief_result(
                                existing_en,
                                disease_payload,
                            )
                    if _translation_source_usable(english_result):
                        translated = await _translate_brief_result(
                            generator,
                            disease={
                                **disease_payload,
                                "target_sections": language_targets,
                            },
                            english_payload=english_result["payload"],
                            sources=generation_sources,
                            target_sections=language_targets,
                        )
                        translation_trace = (
                            translated.get("trace")
                            if isinstance(translated.get("trace"), dict)
                            else {}
                        )
                        translated_payload = (
                            translated.get("payload")
                            if isinstance(translated.get("payload"), dict)
                            else {}
                        )
                        if (
                            not translation_trace.get("error")
                            and not translation_trace.get("citation_failures")
                            and str(translated_payload.get("status") or "").strip().lower()
                            == "published"
                        ):
                            results_by_language[language] = translated
                            continue
                        await _log_task(
                            task_uuid,
                            entry_type="warning",
                            title="ZH Translation Fallback",
                            content=(
                                "Grounded EN-to-ZH translation did not pass validation; "
                                "falling back to full evidence generation."
                            ),
                            metadata={
                                "disease_id": disease["disease_id"],
                                "language": language,
                                "target_sections": language_targets,
                                "trace_error": translation_trace.get("error"),
                                "citation_failures": translation_trace.get("citation_failures"),
                                "workflow_stage": "brief_generation_zh_translation_fallback",
                            },
                            success=False,
                        )
                generated = await _generate_brief_result(
                    generator,
                    disease={
                        **disease_payload,
                        "target_sections": language_targets,
                    },
                    sources=generation_sources,
                    language=language,
                )
                results_by_language[language] = generated
            for language in brief_languages:
                if language not in results_by_language:
                    existing_brief = existing_briefs_by_language.get(language)
                    if existing_brief is not None:
                        results_by_language[language] = _locked_existing_brief_result(
                            existing_brief, disease_payload
                        )
            generated_results = [
                results_by_language[language]
                for language in brief_languages
                if language in results_by_language
            ]

            for result in generated_results:
                language = str(result.get("payload", {}).get("language") or "")
                result["payload"] = _merge_repair_payload(
                    result["payload"],
                    existing_briefs_by_language.get(language),
                    target_sections_by_language[language],
                    disease_payload,
                )

            generation_failures = [
                *_generated_profile_failures(generated_results),
                *_bilingual_alignment_failures(generated_results),
            ]
            if generation_failures:
                await _log_task(
                    task_uuid,
                    entry_type="error",
                    title="Knowledge Generation Blocked",
                    content=(
                        "Generated profiles did not pass the publication gate. "
                        "No disease brief was generated or persisted.\n"
                        + "\n".join(generation_failures)
                    ),
                    metadata={
                        "disease_id": disease["disease_id"],
                        "failures": generation_failures,
                        "workflow_stage": "knowledge_generation_blocked",
                    },
                    success=False,
                )
                raise KnowledgeEvidenceInsufficientError(
                    f"{disease['disease_id']} generation did not pass the publication gate: "
                    + " | ".join(generation_failures)
                )

            if task_uuid:
                await task_manager.update_task_progress(task_uuid, 80)

            for result in generated_results:
                await _raise_if_cancelled(task_uuid)
                payload = result["payload"]
                payload["metadata"] = {
                    **(payload.get("metadata") or {}),
                    "evidence_quality": evidence_quality.to_dict(),
                    "direct_evidence_quality": direct_evidence_quality.to_dict(),
                    "source_packet_manifest_id": source_packet_manifest_id,
                    "adapter_outcomes": fetch_report.adapter_outcomes,
                    "adapter_durations": fetch_report.adapter_durations,
                }
                trace = result.get("trace") or {}
                language = str(payload.get("language") or trace.get("language") or "unknown").strip().lower()
                model_used = trace.get("model") if isinstance(trace.get("model"), str) else None
                provider_used = trace.get("provider") if isinstance(trace.get("provider"), str) else None
                token_usage = trace.get("token_usage") if isinstance(trace.get("token_usage"), dict) else {}
                tokens_used = _total_tokens_from_usage(token_usage)
                duration = float(trace.get("duration") or 0.0) if trace.get("duration") is not None else None
                error_message = trace.get("error") if isinstance(trace.get("error"), str) else None
                brief_rows.append(await _upsert_brief(db, payload))

                await _log_task(
                    task_uuid,
                    entry_type="success" if error_message is None else "warning",
                    title=f"{language.upper()} Brief Generated",
                    content=(
                        f"{language.upper()} brief status: {payload.get('status') or 'unknown'}\n"
                        f"Model: {model_used or '-'}\n"
                        f"Provider: {provider_used or '-'}\n"
                        f"Preferred shard models: {', '.join(trace.get('preferred_models') or []) or '-'}\n"
                        f"Cache hit: {'yes' if trace.get('cache_hit') else 'no'}"
                    ),
                    prompt=trace.get("prompt") if isinstance(trace.get("prompt"), str) else None,
                    response=trace.get("response") if isinstance(trace.get("response"), str) else None,
                    model_used=model_used,
                    tokens_used=tokens_used,
                    duration=duration,
                    metadata={
                        "disease_id": disease["disease_id"],
                        "language": language,
                        "workflow_stage": f"brief_generation_{language}",
                        "provider": provider_used,
                        "generator": trace.get("generator"),
                        "preferred_models": trace.get("preferred_models") or [],
                        "shard_index": trace.get("shard_index"),
                        "shard_key": trace.get("shard_key"),
                        "token_usage": token_usage,
                        "cache_hit": bool(trace.get("cache_hit")),
                        "status": payload.get("status"),
                        "quality_score": payload.get("quality_score"),
                        "source_count": len(source_dicts),
                        "error": error_message,
                    },
                    success=error_message is None,
                )

            await _log_task(
                task_uuid,
                entry_type="info",
                title="Brief Persistence Completed",
                content=(
                    f"Persisted {len(brief_rows)} brief row(s) for {disease['disease_id']}."
                ),
                metadata={
                    "disease_id": disease["disease_id"],
                    "brief_count": len(brief_rows),
                    "workflow_stage": "brief_persisted",
                },
            )

            await db.commit()

        brief_statuses = {brief.language: brief.status for brief in brief_rows}
        published_languages = [
            brief.language for brief in brief_rows if knowledge_brief_publication_tier(brief) == "published"
        ]
        blocked_languages = [
            brief.language for brief in brief_rows if knowledge_brief_publication_tier(brief) == "blocked"
        ]
        await _log_task(
            task_uuid,
            entry_type="success",
            title="Knowledge Update Completed",
            content=(
                f"{disease['disease_id']} completed with {len(source_dicts)} source row(s) "
                f"and briefs: {brief_statuses}."
            ),
            metadata={
                "disease_id": disease["disease_id"],
                "source_count": len(source_dicts),
                "brief_statuses": brief_statuses,
                "workflow_stage": "knowledge_complete",
            },
        )
        if task_uuid:
            await task_manager.update_task_progress(task_uuid, 100)

        return {
            "disease_id": disease["disease_id"],
            "knowledge_profile_type": disease["knowledge_profile_type"],
            "target_sections": target_sections,
            "fetched_sources": len(candidates),
            "total_sources": len(source_dicts),
            "inherited_source_count": len(inherited_sources),
            "evidence_quality": evidence_quality.to_dict(),
            "adapter_outcomes": fetch_report.adapter_outcomes,
            "adapter_durations": fetch_report.adapter_durations,
            "brief_statuses": brief_statuses,
            "published_languages": published_languages,
            "blocked_languages": blocked_languages,
        }

    async def execute_task(self, task: Task) -> dict[str, Any]:
        """Execute a task created by the dashboard or CLI."""
        inp = dict(task.input_data or {})
        disease_id = str(inp.get("disease_id") or inp.get("disease") or "").strip()
        if not disease_id:
            disease_ids = inp.get("disease_ids") or []
            if isinstance(disease_ids, list) and len(disease_ids) == 1:
                disease_id = str(disease_ids[0]).strip()
        if not disease_id:
            raise ValueError("Knowledge task is missing disease_id")

        source_groups = inp.get("source_groups") or inp.get("source") or []
        if isinstance(source_groups, str):
            source_groups = [source_groups]
        force = bool(inp.get("force", False))
        generator_mode = str(inp.get("generator", "auto"))
        source_only = bool(inp.get("source_only", False))
        refresh_existing_on_source_change = inp.get("refresh_existing_on_source_change")
        target_languages = inp.get("repair_languages") or inp.get("languages")
        if isinstance(target_languages, str):
            target_languages = [target_languages]
        if not isinstance(target_languages, list):
            target_languages = None

        return await self.update_disease(
            disease_id,
            enabled_sources=list(source_groups) if isinstance(source_groups, list) else [],
            force=force,
            generator_mode=generator_mode,
            target_languages=target_languages,
            dry_run=bool(inp.get("dry_run", False)),
            task_uuid=task.task_uuid,
            source_only=source_only,
            refresh_existing_on_source_change=(
                None
                if refresh_existing_on_source_change is None
                else bool(refresh_existing_on_source_change)
            ),
            source_refreshed_task_uuid=inp.get("source_refreshed_task_uuid"),
        )

    async def execute_source_refresh_task(self, task: Task) -> dict[str, Any]:
        """Execute a source-only task created by the dashboard or scheduler."""
        inp = dict(task.input_data or {})
        inp["source_only"] = True
        source_groups = inp.get("source_groups") or inp.get("source") or []
        if isinstance(source_groups, str):
            source_groups = [source_groups]
        disease_id = str(inp.get("disease_id") or inp.get("disease") or "").strip()
        if not disease_id:
            disease_ids = inp.get("disease_ids") or []
            if isinstance(disease_ids, list) and len(disease_ids) == 1:
                disease_id = str(disease_ids[0]).strip()
        if not disease_id:
            raise ValueError("Knowledge source refresh task is missing disease_id")

        result = await self.update_disease(
            disease_id,
            enabled_sources=list(source_groups) if isinstance(source_groups, list) else [],
            force=bool(inp.get("force", True)),
            generator_mode=str(inp.get("generator", "auto")),
            dry_run=bool(inp.get("dry_run", False)),
            task_uuid=task.task_uuid,
            source_only=True,
            refresh_existing_on_source_change=False,
        )
        if (
            inp.get("enqueue_ai_after_source_refresh")
            and not result.get("source_gap")
            and not bool(inp.get("dry_run", False))
        ):
            repair_input = {
                **inp,
                "source_only": False,
                "force": False,
                "generator": _normalize_generator_mode(str(inp.get("generator") or "ai")),
                "targeted_repair": True,
                "source_refreshed_task_uuid": task.task_uuid,
                "initiated_via": "knowledge-source-refresh-followup",
            }
            repair_input.pop("enqueue_ai_after_source_refresh", None)
            followup = await task_manager.create_task(
                task_type=TaskType.UPDATE_DISEASE_KNOWLEDGE,
                task_name=str(getattr(task, "task_name", "") or f"Repair {disease_id} knowledge profile").replace(
                    "Refresh sources before ",
                    "",
                    1,
                ),
                priority=TaskPriority.URGENT,
                description=(
                    f"Model-center repair for {disease_id} after source-only refresh "
                    f"{task.task_uuid}."
                ),
                input_data=repair_input,
                tags=sorted({*(getattr(task, "tags", None) or []), "knowledge", "auto_repair"}),
            )
            await task_manager.add_workbook_entry(
                followup.task_uuid,
                entry_type="info",
                title="Knowledge Repair Queued After Source Refresh",
                content=(
                    f"Queued model-center repair for {disease_id} after source refresh "
                    f"{task.task_uuid}."
                ),
                content_type="text",
                metadata={
                    "disease_id": disease_id,
                    "source_refreshed_task_uuid": task.task_uuid,
                    "workflow_stage": "knowledge_repair_after_source_refresh_queued",
                },
            )
            followup = await task_manager.update_task_status(followup.task_uuid, TaskStatus.QUEUED) or followup
            result["followup_task_uuid"] = followup.task_uuid
        return result


async def render_knowledge_preview(
    disease_id: str,
    *,
    enabled_sources: list[str] | None = None,
    generator_mode: str = "auto",
) -> dict[str, Any]:
    """Convenience wrapper used by the CLI dry-run mode."""
    service = DiseaseKnowledgeUpdateService()
    return await service.update_disease(
        disease_id,
        enabled_sources=enabled_sources,
        force=True,
        generator_mode=generator_mode,
        dry_run=True,
    )
