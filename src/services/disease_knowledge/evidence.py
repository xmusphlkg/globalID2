"""Pure evidence, source, brief-inspection, and attribution helpers for disease knowledge."""

from __future__ import annotations

import csv
import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from src.core import get_config
from src.domain import DiseaseKnowledgeBrief, DiseaseKnowledgeSource
from src.knowledge import (
    AIDiseaseBriefGenerator,
    DiseaseKnowledgeFetcher,
    EVIDENCE_POLICY_VERSION,
    KNOWLEDGE_SCHEMA_VERSION,
    SourceFetchReport,
    assess_knowledge_brief,
    assess_knowledge_evidence,
    has_grounding_content,
    prepare_evidence_packet,
)
from src.knowledge.citations import validate_knowledge_citations
from src.knowledge.profile_schema import (
    knowledge_profile_schema_signature,
    resolve_knowledge_profile_schema,
)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DISEASE_CSV = ROOT / "configs" / "standard_diseases.csv"
KNOWLEDGE_PIPELINE_VERSION = 3
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

_SOURCE_TRANSPORT_FAILURES = frozenset({"timeout", "error"})
_SOURCE_TRANSPORT_DEFERRED = frozenset({"busy", "cooldown"})
_SUBSTANTIVE_SOURCE_ADAPTERS = frozenset(
    {"who", "who_don", "web_search", "wikipedia", "pubmed"}
)

_PUBLICATION_WORKFLOW_METADATA_KEYS = frozenset(
    {
        "automation_state",
        "block_reason",
        "source_task_uuid",
        "source_coverage",
        "progressive_repair",
    }
)


class KnowledgeEvidenceInsufficientError(RuntimeError):
    """Raised when automatic source enrichment cannot produce publishable knowledge."""


@dataclass(frozen=True)
class RetainedProfileEvidence:
    """Result of validating a stored profile against currently active sources."""

    eligible: bool
    citation_fields: tuple[str, ...]
    reason: str | None = None
    rebuilt_manifest: dict[str, Any] | None = None


def _knowledge_evidence_limits(config: Any | None = None) -> dict[str, int]:
    ai_config = (config if config is not None else get_config()).ai
    return {
        "max_sources": int(ai_config.knowledge_evidence_max_sources),
        "max_manifest_characters": int(ai_config.knowledge_evidence_manifest_max_characters),
    }


def _evidence_entity_aliases(disease: dict[str, Any]) -> list[str]:
    """Return reviewed entity labels used to scope evidence fragments."""
    values: list[Any] = [
        disease.get("name_en"),
        disease.get("standard_name_en"),
        disease.get("name_zh"),
        disease.get("standard_name_zh"),
    ]
    query_aliases = disease.get("query_aliases")
    if isinstance(query_aliases, (list, tuple, set)):
        values.extend(query_aliases)
    aliases: list[str] = []
    seen: set[str] = set()
    for value in values:
        alias = " ".join(str(value or "").split()).strip()
        key = alias.casefold()
        if alias and key not in seen:
            seen.add(key)
            aliases.append(alias)
    return aliases


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
        # MSD is retained as an explicit provenance-only adapter, but it never
        # yields reusable grounding text. Do not spend automatic recovery
        # rounds fetching rows the qualification gate must reject.
        values = ["who", "search", "wikidata", "wikipedia", "pubmed"]
    expanded: list[str] = []
    for value in values:
        for adapter in SOURCE_GROUPS.get(value, []):
            if adapter not in expanded:
                expanded.append(adapter)
    return expanded


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


def _knowledge_snapshot(
    sources: list[DiseaseKnowledgeSource],
    briefs: list[DiseaseKnowledgeBrief],
    inherited_sources: list[dict[str, Any]],
) -> str:
    """Detect concurrent refresh/repair before writing a model result."""
    return json.dumps(
        {
            "sources": [source_to_dict(row) for row in sources],
            "briefs": [
                {
                    attr.key: getattr(row, attr.key)
                    for attr in DiseaseKnowledgeBrief.__mapper__.column_attrs
                }
                for row in briefs
            ],
            "inherited_sources": inherited_sources,
        },
        sort_keys=True,
        default=str,
    )


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


def _brief_uses_current_pipeline(
    brief: Any,
    *,
    pipeline_version: int = KNOWLEDGE_PIPELINE_VERSION,
) -> bool:
    metadata = _brief_metadata(brief)
    return (
        _safe_int(metadata.get("pipeline_version")) == pipeline_version
        and _safe_int(metadata.get("knowledge_schema_version")) == KNOWLEDGE_SCHEMA_VERSION
        and _safe_int(metadata.get("evidence_policy_version")) == EVIDENCE_POLICY_VERSION
        and _safe_int(metadata.get("citation_version")) == 2
    )


def _brief_uses_current_profile_schema(brief: Any, disease: dict[str, Any]) -> bool:
    """Keep a generated profile current when its semantic contract changes."""

    metadata = _brief_metadata(brief)
    stored_signature = str(metadata.get("profile_schema_signature") or "").strip()
    if stored_signature:
        return stored_signature == knowledge_profile_schema_signature(disease)
    stored_schema = metadata.get("profile_schema")
    current_schema = disease.get("profile_schema")
    if not isinstance(stored_schema, dict) or not isinstance(current_schema, dict):
        # A profile without a persisted schema is handled by the regular
        # pipeline version check; do not infer a semantic migration blindly.
        return True
    fields = ("profile_type", "required_fields", "optional_fields", "not_applicable_fields")
    return all(
        stored_schema.get(field) == current_schema.get(field) for field in fields
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


def _evidence_gate_result(packet: Any) -> dict[str, Any]:
    """Return the next machine action from global quality and field coverage."""

    assessment = packet.assessment
    coverage = packet.coverage
    if (
        coverage.complete
        and coverage.required_sections == ("definition",)
        and any(
            str(source.get("source_type") or "") == "registry_definition"
            and str(source.get("status") or "active").lower() == "active"
            and str(source.get("review_status") or "").lower() == "approved"
            and bool(
                (source.get("metadata") or {}).get("registry_definition")
                if isinstance(source.get("metadata"), dict)
                else False
            )
            and has_grounding_content(source)
            for source in packet.sources
        )
    ):
        return {
            "state": "ready_for_generation",
            "reason": None,
            "next_action": "generate_profile",
            "evidence_quality": assessment.to_dict(),
            "coverage": coverage.to_dict(),
        }
    if not assessment.sufficient:
        return {
            "state": "awaiting_evidence",
            "reason": _evidence_block_reason(list(packet.sources)),
            "next_action": "targeted_source_discovery",
            "evidence_quality": assessment.to_dict(),
            "coverage": coverage.to_dict(),
        }
    if not coverage.complete:
        return {
            "state": "awaiting_evidence",
            "reason": "section_coverage_missing",
            "next_action": "targeted_source_discovery",
            "evidence_quality": assessment.to_dict(),
            "coverage": coverage.to_dict(),
        }
    return {
        "state": "ready_for_generation",
        "reason": None,
        "next_action": "generate_profile",
        "evidence_quality": assessment.to_dict(),
        "coverage": coverage.to_dict(),
    }


def _is_registry_definition_only_packet(packet: Any) -> bool:
    """Whether the packet may ground only a registry-defined profile lead.

    A definition-only registry source is sufficient for a narrow surveillance
    or classification entity, but it must never be used to refresh optional
    clinical or epidemiological prose left over from an older profile type.
    """

    coverage = packet.coverage
    return (
        coverage.required_sections == ("definition",)
        and any(
            str(source.get("source_type") or "") == "registry_definition"
            and bool(
                (source.get("metadata") or {}).get("registry_definition")
                if isinstance(source.get("metadata"), dict)
                else False
            )
            for source in packet.sources
        )
    )


def _registry_definition_target_sections(
    target_sections_by_language: Mapping[str, list[str]],
    *,
    registry_definition_only: bool,
) -> dict[str, list[str]]:
    """Restrict registry-only profiles to the sections their evidence can support."""

    if not registry_definition_only:
        return {
            str(language): list(sections)
            for language, sections in target_sections_by_language.items()
        }
    return {
        language: ["brief", "definition"]
        for language in ("en", "zh")
    }


def _source_discovery_state(result: Mapping[str, Any]) -> str:
    """Classify a source-only result without treating a transport fault as no evidence.

    The model is never allowed to bridge a missing evidence boundary.  A failed
    upstream is nevertheless different from a completed multi-source search
    that has no support for a required section: the former should retry with
    backoff, while the latter should wait for a source-strategy change.
    """

    if not bool(result.get("source_gap")):
        return "ready_for_generation"
    outcomes = result.get("adapter_outcomes")
    if isinstance(outcomes, Mapping):
        substantive_outcomes = [
            str(outcome or "").lower()
            for adapter, outcome in outcomes.items()
            if str(adapter or "").lower() in _SUBSTANTIVE_SOURCE_ADAPTERS
        ]
        unavailable = _SOURCE_TRANSPORT_FAILURES | _SOURCE_TRANSPORT_DEFERRED
        # A single optional endpoint timing out must not hide successful WHO,
        # PubMed, or direct public-health discovery.  Treat the result as a
        # true transport issue only when every substantive route was
        # unavailable; otherwise the next stage can target the actual missing
        # section instead of re-running the whole adapter fan-out.
        if substantive_outcomes and all(
            outcome in unavailable for outcome in substantive_outcomes
        ):
            return "awaiting_source_transport"
        if not substantive_outcomes and any(
            str(outcome or "").lower() in _SOURCE_TRANSPORT_FAILURES
            for outcome in outcomes.values()
        ):
            return "awaiting_source_transport"
    return "awaiting_evidence"


def _source_gap_blocks_publication(result: Mapping[str, Any]) -> bool:
    """Return whether a source result proves a profile must leave publication.

    A timeout says nothing about the validity of a previously published,
    grounded profile. Only a completed discovery pass that still lacks required
    evidence can revoke that profile while automatic enrichment continues.
    """

    return bool(result.get("source_gap")) and _source_discovery_state(result) == "awaiting_evidence"


def _brief_value(brief: Any, field: str) -> Any:
    if isinstance(brief, Mapping):
        return brief.get(field)
    return getattr(brief, field, None)


def _active_approved_source_ids(sources: list[Any]) -> set[int]:
    """Return IDs that remain eligible for public citation."""

    active_ids: set[int] = set()
    for source in sources:
        source_id = _safe_int(_brief_value(source, "id"))
        if source_id is None:
            continue
        if (
            str(_brief_value(source, "status") or "").lower() == "active"
            and str(_brief_value(source, "review_status") or "").lower()
            == "approved"
        ):
            active_ids.add(source_id)
    return active_ids


def _source_records_by_id(sources: list[Any]) -> dict[int, dict[str, Any]]:
    """Normalize active source rows for retained-evidence revalidation."""

    records: dict[int, dict[str, Any]] = {}
    for source in sources:
        record = dict(source) if isinstance(source, Mapping) else source_to_dict(source)
        source_id = _safe_int(record.get("id") or record.get("source_id"))
        if source_id is not None:
            records[source_id] = record
    return records


def _assess_retained_profile_evidence(
    brief: Any,
    disease: dict[str, Any],
    active_source_ids: set[int],
    source_records_by_id: Mapping[int, dict[str, Any]] | None = None,
    *,
    max_manifest_characters: int | None = None,
) -> RetainedProfileEvidence:
    """Prove a stored profile is still publishable without generating prose.

    A source discovery result only describes the newly collected packet. It
    cannot revoke a profile whose every publication-field citation still maps
    to an active, approved source. For legacy rows that never persisted a
    manifest, the current source corpus is used to rebuild one before the same
    section-level citation validation runs.
    """

    language = str(_brief_value(brief, "language") or "en").strip().lower()
    assessment = assess_knowledge_brief(brief, language, disease=disease)
    citation_fields = tuple(dict.fromkeys(["brief", *assessment.required_fields]))
    if not assessment.publishable:
        return RetainedProfileEvidence(False, citation_fields, "profile_incomplete")
    if not _brief_uses_current_profile_schema(brief, disease):
        return RetainedProfileEvidence(False, citation_fields, "profile_contract_changed")

    metadata = _brief_metadata(brief)
    payload = {
        field: _brief_value(brief, field)
        for field in citation_fields
    }
    payload["source_attribution"] = _brief_value(brief, "source_attribution") or []

    attribution = payload["source_attribution"]
    if not isinstance(attribution, list):
        return RetainedProfileEvidence(False, citation_fields, "missing_source_attribution")
    marker_to_source_id: dict[int, int] = {}
    for position, source in enumerate(attribution, start=1):
        if not isinstance(source, Mapping):
            continue
        marker = (
            _safe_int(source.get("citation_index"))
            or _safe_int(source.get("position"))
            or position
        )
        source_id = _safe_int(source.get("source_id")) or _safe_int(source.get("id"))
        if source_id is not None:
            marker_to_source_id[marker] = source_id
    cited_source_ids: list[int] = []
    for field in citation_fields:
        markers = [
            int(marker)
            for marker in re.findall(r"\[(\d+)\]", str(payload.get(field) or ""))
        ]
        if not markers:
            return RetainedProfileEvidence(False, citation_fields, f"{field}_uncited")
        if any(marker_to_source_id.get(marker) not in active_source_ids for marker in markers):
            return RetainedProfileEvidence(False, citation_fields, f"{field}_inactive_source")
        for marker in markers:
            source_id = marker_to_source_id[marker]
            if source_id not in cited_source_ids:
                cited_source_ids.append(source_id)

    manifest = metadata.get("evidence_manifest")
    if isinstance(manifest, Mapping) and isinstance(manifest.get("fragments"), list):
        payload["metadata"] = metadata
        if not validate_knowledge_citations(payload, fields=list(citation_fields)):
            return RetainedProfileEvidence(True, citation_fields)

    if source_records_by_id is None:
        return RetainedProfileEvidence(False, citation_fields, "citation_manifest_unavailable")
    cited_sources = [
        source_records_by_id[source_id]
        for source_id in cited_source_ids
        if source_id in source_records_by_id
    ]
    if len(cited_sources) != len(cited_source_ids):
        return RetainedProfileEvidence(False, citation_fields, "cited_source_record_missing")
    profile_schema = resolve_knowledge_profile_schema(disease)
    manifest_limit = (
        max_manifest_characters
        if max_manifest_characters is not None
        else _knowledge_evidence_limits()["max_manifest_characters"]
    )
    packet = prepare_evidence_packet(
        cited_sources,
        profile_schema,
        target_sections=citation_fields,
        entity_aliases=_evidence_entity_aliases(disease),
        max_sources=len(cited_sources),
        max_manifest_characters=manifest_limit,
        allowed_source_types=AIDiseaseBriefGenerator.PUBLIC_SOURCE_TYPES,
    )
    if not packet.coverage.complete:
        return RetainedProfileEvidence(False, citation_fields, "rebuilt_manifest_incomplete")
    rebuilt_manifest = packet.manifest.to_dict()
    payload["metadata"] = {**metadata, "evidence_manifest": rebuilt_manifest}
    if validate_knowledge_citations(payload, fields=list(citation_fields)):
        return RetainedProfileEvidence(False, citation_fields, "citation_validation_failed")
    return RetainedProfileEvidence(
        True,
        citation_fields,
        rebuilt_manifest=rebuilt_manifest,
    )


def _restore_brief_with_retained_evidence(
    brief: Any,
    disease: dict[str, Any],
    active_source_ids: set[int],
    source_records_by_id: Mapping[int, dict[str, Any]] | None = None,
    *,
    restored_at: datetime | None = None,
    pipeline_version: int = KNOWLEDGE_PIPELINE_VERSION,
) -> RetainedProfileEvidence:
    """Publish a draft only after the retained-evidence boundary passes."""

    retention = _assess_retained_profile_evidence(
        brief,
        disease,
        active_source_ids,
        source_records_by_id,
    )
    if not retention.eligible:
        return retention
    metadata = _brief_metadata(brief)
    refreshed_metadata = {
        key: value
        for key, value in metadata.items()
        if key not in _PUBLICATION_WORKFLOW_METADATA_KEYS
    }
    timestamp = (restored_at or datetime.now(timezone.utc)).isoformat()
    refreshed_metadata.update(
        {
            "pipeline_version": pipeline_version,
            "knowledge_schema_version": KNOWLEDGE_SCHEMA_VERSION,
            "evidence_policy_version": EVIDENCE_POLICY_VERSION,
            "citation_validation": {
                "valid": True,
                "validated_fields": list(retention.citation_fields),
                "failures": [],
            },
            "retained_evidence_revalidated_at": timestamp,
        }
    )
    if retention.rebuilt_manifest is not None:
        refreshed_metadata["evidence_manifest"] = retention.rebuilt_manifest
        refreshed_metadata["evidence_manifest_reconstructed_at"] = timestamp
    if isinstance(brief, Mapping):
        brief["status"] = "published"
        brief["metadata"] = refreshed_metadata
    else:
        brief.status = "published"
        brief.metadata_ = refreshed_metadata
    return retention


def _source_transport_retry_delay_seconds(
    *,
    attempt: int,
    initial_delay_seconds: int,
    maximum_delay_seconds: int,
) -> int:
    """Return bounded exponential source-transport backoff for one disease."""

    multiplier = 2 ** min(max(0, attempt - 1), 6)
    return min(
        max(1, int(maximum_delay_seconds)),
        max(1, int(initial_delay_seconds)) * multiplier,
    )


def _publication_evidence_sections(profile_schema: Any) -> list[str]:
    """Return the evidence boundary required before either language can publish."""

    required_fields = getattr(profile_schema, "required_fields", ())
    return ["brief", *dict.fromkeys(str(field) for field in required_fields if field)]


def _source_qualification_reason(source: dict[str, Any]) -> str | None:
    """Classify sources that can never become grounding evidence without AI."""

    metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    content_kind = str(metadata.get("content_kind") or "").strip().lower()
    if metadata.get("metadata_only") or content_kind in {"scholarly_metadata", "search_result"}:
        return "metadata_only"
    if not has_grounding_content(source):
        return "no_grounding_content"
    return None


def _fetch_sources_with_report(
    fetcher: DiseaseKnowledgeFetcher,
    disease: dict[str, Any],
    *,
    enabled_sources: list[str],
    target_sections: list[str],
    cancel_event: threading.Event | None = None,
) -> SourceFetchReport:
    if hasattr(fetcher, "fetch_with_report"):
        return fetcher.fetch_with_report(
            disease,
            enabled_sources=enabled_sources,
            target_sections=target_sections,
            cancel_event=cancel_event,
        )
    candidates = fetcher.fetch(
        disease,
        enabled_sources=enabled_sources,
        target_sections=target_sections,
        cancel_event=cancel_event,
    )
    return SourceFetchReport(
        candidates=list(candidates),
        adapter_outcomes={source: "success" for source in enabled_sources},
        adapter_durations={},
    )
