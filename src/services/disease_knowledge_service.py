"""Disease knowledge update service used by scripts, worker tasks, and dashboard APIs."""

from __future__ import annotations

import asyncio
import re
import threading
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

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
    DiseaseKnowledgeFetcher,
    EVIDENCE_POLICY_VERSION,
    KNOWLEDGE_SCHEMA_VERSION,
    KNOWLEDGE_SOURCE_STRATEGY_VERSION,
    ReviewedDiseaseBriefGenerator,
    SourceFetchReport,
    apply_knowledge_quality_gate,
    assess_knowledge_brief,
    assess_knowledge_evidence,
    attach_profile_schema,
    has_grounding_content,
    knowledge_brief_block_reason,
    knowledge_brief_publication_tier,
    prepare_evidence_packet,
    public_disease_page_exclusion_reason,
    resolve_disease_knowledge_status,
)
from src.knowledge.citations import (
    KNOWLEDGE_CITATION_FIELDS,
    normalize_knowledge_citations,
    validate_knowledge_citations,
)
from src.knowledge.profile_schema import (
    knowledge_profile_schema_signature,
    resolve_knowledge_profile_schema,
)
from src.knowledge.repair_policy import (
    _bilingual_alignment_failures,
    _knowledge_repair_task_priority,
    _normalize_repair_reasons_by_language,
    _normalize_requested_sections_by_language,
)
from src.ontology import load_disease_ontology
from src.services.disease_knowledge import (
    DEFAULT_DISEASE_CSV,
    KNOWLEDGE_PIPELINE_VERSION,
    KnowledgeEvidenceInsufficientError,
    ROOT,
    RetainedProfileEvidence,
    SOURCE_GROUPS,
    SOURCE_REFRESH_TTL_DAYS,
    _PUBLICATION_WORKFLOW_METADATA_KEYS,
    _SOURCE_TRANSPORT_DEFERRED,
    _SOURCE_TRANSPORT_FAILURES,
    _SUBSTANTIVE_SOURCE_ADAPTERS,
    _active_approved_source_ids,
    _active_knowledge_tasks_by_disease,
    _assess_retained_profile_evidence,
    _bilingual_publication_prerequisites,
    _brief_metadata,
    _brief_uses_current_pipeline,
    _brief_uses_current_profile_schema,
    _brief_value,
    _enrich_source_attribution,
    _evidence_block_reason,
    _evidence_entity_aliases,
    _evidence_gate_result,
    _fetch_sources_with_report,
    _generated_profile_failures,
    _has_approved_public_sources,
    _is_registry_definition_only_packet,
    _knowledge_evidence_limits as _evidence_knowledge_evidence_limits,
    _knowledge_snapshot,
    _locked_existing_brief_result,
    _merge_repair_payload,
    _metadata_field,
    _model_center_route_failure,
    _normalize_generator_mode,
    _profile_repair_diagnostics_by_language,
    _profile_repair_metadata,
    _profile_repair_sections,
    _profile_repair_sections_by_language,
    _publication_evidence_sections,
    _registry_definition_target_sections,
    _resolve_repair_sections_by_language,
    _restore_brief_with_retained_evidence,
    _safe_int,
    _select_knowledge_repair_candidates,
    _skipped_language_after_route_failure,
    _source_discovery_state,
    _source_gap_blocks_publication,
    _source_qualification_reason,
    _source_records_by_id,
    _source_transport_retry_delay_seconds,
    _sources_need_refresh,
    _total_tokens_from_usage,
    _translation_source_usable,
    _use_source_id_citation_markers,
    expand_sources,
    load_standard_diseases,
    source_to_dict,
)
from src.services.exceptions import TaskCancelledError

logger = get_logger(__name__)

_KNOWLEDGE_STAGE_RUN: ContextVar[Any] = ContextVar("knowledge_stage_run", default=None)

ACTIVE_KNOWLEDGE_TASK_STATUSES = (
    TaskStatus.PENDING,
    TaskStatus.QUEUED,
    TaskStatus.RUNNING,
    TaskStatus.RETRYING,
)


def _knowledge_evidence_limits(config: Any | None = None) -> dict[str, int]:
    return _evidence_knowledge_evidence_limits(
        config if config is not None else get_config()
    )


def _raise_if_model_work_failed() -> None:
    from src.ai.runtime import AIWorkFailed, current_parent

    parent = current_parent()
    if parent is not None and parent.failures:
        raise AIWorkFailed("Knowledge model work failed: " + "; ".join(parent.failures))


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


_NON_AUTHORITATIVE_REFRESH_MISS = "Not returned by latest forced refresh"


async def _restore_sources_demoted_by_refresh_miss(
    db,
    *,
    disease_id: str | None = None,
) -> int:
    """Undo the legacy destructive refresh rule without reviving real rejects.

    Search and catalogue adapters return a changing subset of relevant URLs;
    absence from one result set cannot establish that a previously approved
    source is invalid.  Earlier code nevertheless marked such rows stale and
    rejected.  The exact historical marker lets us reverse only that invalid
    inference, while sources rejected by qualification or an explicit future
    invalidation remain untouched.
    """

    query = select(DiseaseKnowledgeSource).where(
        DiseaseKnowledgeSource.status == "stale",
        DiseaseKnowledgeSource.review_status == "rejected",
    )
    if disease_id:
        query = query.where(DiseaseKnowledgeSource.disease_id == disease_id)
    rows = list((await db.execute(query)).scalars().all())
    restored = 0
    restored_at = datetime.now(timezone.utc).isoformat()
    for row in rows:
        metadata = row.metadata_ if isinstance(row.metadata_, dict) else {}
        if metadata.get("stale_reason") != _NON_AUTHORITATIVE_REFRESH_MISS:
            continue
        row.status = "active"
        row.review_status = "approved"
        row.metadata_ = {
            **metadata,
            "refresh_reconciliation": {
                "state": "retained",
                "reason": "non_authoritative_refresh_miss",
                "restored_at": restored_at,
            },
        }
        row.metadata_.pop("stale_reason", None)
        restored += 1
    if restored:
        await db.flush()
    return restored


async def _reconcile_source_refresh(
    db,
    disease_id: str,
    candidates: list,
    enabled_sources: list[str],
) -> dict[str, int]:
    """Record a source refresh without treating result-set absence as deletion.

    Refreshes are additive. A source is updated when the same canonical row is
    fetched again, while an approved URL omitted by a search, PubMed query, or
    changing upstream index remains eligible evidence. Explicit revocation is
    a separate operation and must carry direct evidence of invalidity.
    """

    refreshed_keys = {
        (str(candidate.disease_id), str(candidate.source_type), str(candidate.url))
        for candidate in candidates
    }
    source_rows = await _existing_sources(db, disease_id)
    retained = sum(
        1
        for row in source_rows
        if row.status == "active"
        and row.review_status == "approved"
        and row.source_type in enabled_sources
        and (row.disease_id, row.source_type, row.url) not in refreshed_keys
    )
    return {
        "refreshed_source_count": len(refreshed_keys),
        "retained_source_count": retained,
    }


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
        payload["status"] = "draft"
        payload["metadata"] = {
            **payload["metadata"],
            "automation_state": "awaiting_evidence",
            "block_reason": "citation_validation_failed",
        }
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



async def _acquire_disease_knowledge_lock(db, disease_id: str) -> None:
    """Serialize updates for one disease across API, CLI and worker processes."""
    bind = db.get_bind()
    if getattr(getattr(bind, "dialect", None), "name", None) != "postgresql":
        return
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:scope))"),
        {"scope": f"disease-knowledge:{disease_id.upper()}"},
    )


async def _automatic_model_repair_is_superseded(
    disease_id: str,
    source_refreshed_task_uuid: str | None,
) -> bool:
    """Return whether a certified-source repair is older than a published profile.

    A source task can finish while another repair publishes the same disease.
    Its already-queued model follow-up is then stale work, not a reason to
    regenerate or overwrite the newer bilingual profile.
    """
    source_task_uuid = str(source_refreshed_task_uuid or "").strip()
    if not source_task_uuid:
        return False

    async with get_database() as db:
        source_completed_at = (
            await db.execute(
                select(Task.completed_at).where(Task.task_uuid == source_task_uuid)
            )
        ).scalar_one_or_none()
        if source_completed_at is None:
            return False
        if source_completed_at.tzinfo is None:
            source_completed_at = source_completed_at.replace(tzinfo=timezone.utc)

        briefs = list(
            (
                await db.execute(
                    select(DiseaseKnowledgeBrief).where(
                        DiseaseKnowledgeBrief.disease_id == disease_id.upper(),
                        DiseaseKnowledgeBrief.language.in_(("en", "zh")),
                    )
                )
            ).scalars().all()
        )

    by_language = {str(brief.language).lower(): brief for brief in briefs}
    if set(by_language) != {"en", "zh"}:
        return False
    for brief in by_language.values():
        updated_at = brief.updated_at
        if str(brief.status or "").lower() != "published" or updated_at is None:
            return False
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        if updated_at < source_completed_at:
            return False
    return True



async def _archive_non_public_disease_briefs(
    db,
    disease_id: str,
    archive_reason: str,
) -> int:
    """Terminally archive public-profile rows for a non-public catalogue item.

    The catalogue is the ownership boundary for public disease pages. Keeping
    this transition here makes direct dashboard calls, queued workers, and
    periodic reconciliation converge on the same durable state.
    """

    rows = list(
        (
            await db.execute(
                select(DiseaseKnowledgeBrief)
                .where(
                    DiseaseKnowledgeBrief.disease_id == disease_id,
                    DiseaseKnowledgeBrief.status.in_(
                        ["draft", "published", "requires_review"]
                    ),
                )
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
    )
    if not rows:
        return 0

    archived_at = datetime.now(timezone.utc).isoformat()
    for brief in rows:
        metadata = _brief_metadata(brief)
        brief.status = "archived"
        brief.metadata_ = {
            key: value
            for key, value in metadata.items()
            if key not in _PUBLICATION_WORKFLOW_METADATA_KEYS
        }
        brief.metadata_ = {
            **brief.metadata_,
            "catalogue_record_state": "archived",
            "catalogue_archive_reason": archive_reason,
            "catalogue_archived_at": archived_at,
        }
    await db.flush()
    return len(rows)



async def _qualify_pending_sources(
    db,
    disease_id: str,
) -> dict[str, int]:
    """Resolve deterministic source eligibility before evidence is assessed.

    Title-only records are useful provenance, but neither a human nor a model
    can transform them into source-grounded claims.  Keeping them in a review
    queue obscures the actual evidence backlog and starves usable sources.
    """

    rows = await _existing_sources(db, disease_id)
    rejected = 0
    retained = 0
    for row in rows:
        if row.status != "active" or row.review_status != "requires_review":
            continue
        source = source_to_dict(row)
        reason = _source_qualification_reason(source)
        if reason is None:
            retained += 1
            continue
        row.review_status = "rejected"
        row.metadata_ = {
            **(row.metadata_ or {}),
            "qualification_state": "not_grounding_eligible",
            "qualification_reason": reason,
        }
        rejected += 1
    return {"rejected": rejected, "retained_for_ai_review": retained}



async def _generate_brief_result(
    generator: AIDiseaseBriefGenerator | ReviewedDiseaseBriefGenerator,
    *,
    disease: dict[str, Any],
    sources: list[dict[str, Any]],
    language: str,
) -> dict[str, Any]:
    result = await generator.generate_with_trace(
        disease=disease, sources=sources, language=language,
    )
    _raise_if_model_work_failed()
    return result


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
    result = await translate(
        disease=disease,
        english_payload=english_payload,
        sources=sources,
        target_sections=target_sections,
        language="zh",
    )
    _raise_if_model_work_failed()
    return result



async def _fetch_sources_for_update(
    fetcher: DiseaseKnowledgeFetcher,
    disease: dict[str, Any],
    *,
    enabled_sources: list[str],
    target_sections: list[str],
) -> SourceFetchReport:
    """Run blocking source discovery while allowing worker shutdown to unwind.

    `asyncio.to_thread` cannot cancel an already-running requests call. The
    event gives the fetcher's adapter wait loop a cooperative stop signal, so
    the worker can requeue its durable task instead of waiting for a stale
    source deadline during deployment or recovery.
    """
    cancel_event = threading.Event()
    try:
        return await asyncio.to_thread(
            _fetch_sources_with_report,
            fetcher,
            disease,
            enabled_sources=enabled_sources,
            target_sections=target_sections,
            cancel_event=cancel_event,
        )
    except asyncio.CancelledError:
        cancel_event.set()
        raise



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
        priorities: set[str] | None = None,
        excluded_disease_ids: set[str] | None = None,
        source_retry_context_by_disease: Mapping[str, Mapping[str, Any]] | None = None,
        source_first: bool = True,
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
            priorities=priorities,
            excluded_disease_ids=excluded_disease_ids,
        )

        created: list[Task] = []
        for item in candidates:
            disease_id = str(item["disease_id"])
            retry_context = (source_retry_context_by_disease or {}).get(disease_id, {})
            try:
                previous_transport_attempt = int(
                    retry_context.get("source_transport_attempt") or 0
                )
            except (AttributeError, TypeError, ValueError):
                previous_transport_attempt = 0
            source_transport_attempt = max(1, previous_transport_attempt + 1)
            repair_sections = list(item.get("repair_sections") or [])
            repair_priority = str(item.get("repair_priority") or "high")
            task_priority = _knowledge_repair_task_priority(priority, repair_priority)
            name_en = str(item.get("name_en") or disease_id)
            task_name = f"Repair {name_en} knowledge profile"
            description = (
                f"Automatic knowledge repair for {disease_id}: "
                f"{', '.join(repair_sections)}"
            )
            task_type = (
                TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES
                if source_first
                else TaskType.UPDATE_DISEASE_KNOWLEDGE
            )
            queued_task_name = f"Refresh sources before {task_name}" if source_first else task_name
            task = await task_manager.create_task(
                task_type=task_type,
                task_name=queued_task_name,
                priority=task_priority,
                description=(
                    f"Source-first stage for {disease_id}; model repair will be queued after "
                    "evidence quality is confirmed."
                    if source_first
                    else description
                ),
                input_data={
                    "disease_id": disease_id,
                    "disease_ids": [disease_id],
                    "source_groups": source_groups,
                    "source": source_groups,
                    "force": force,
                    "generator": _normalize_generator_mode(generator_mode),
                    "targeted_repair": True,
                    "repair_sections": repair_sections,
                    "repair_sections_by_language": item.get("repair_sections_by_language") or {},
                    "repair_reasons_by_language": item.get("repair_reasons_by_language") or {},
                    "repair_priority": repair_priority,
                    "knowledge_completeness": item.get("knowledge_completeness"),
                    "knowledge_display_mode": item.get("knowledge_display_mode"),
                    "initiated_via": initiated_via,
                    "requested_by": requested_by,
                    "source_only": source_first,
                    "enqueue_ai_after_source_refresh": source_first,
                    "source_transport_attempt": source_transport_attempt,
                },
                tags=(
                    ["knowledge", "auto_repair", "source_refresh", "source_first"]
                    if source_first
                    else ["knowledge", "auto_repair"]
                ),
            )
            await task_manager.add_workbook_entry(
                task.task_uuid,
                entry_type="info",
                title=(
                    "Knowledge Source Refresh Queued"
                    if source_first
                    else "Automatic Knowledge Repair Queued"
                ),
                content=(
                    (
                        f"Queued source-first refresh for {disease_id}; a model-center repair "
                        "will follow only when the requested evidence is available. "
                    )
                    if source_first
                    else f"Queued model-center repair for {disease_id}. "
                ) + f"Target sections: {', '.join(repair_sections)}.",
                content_type="text",
                metadata={
                    "disease_id": disease_id,
                    "repair_sections": repair_sections,
                    "repair_sections_by_language": item.get("repair_sections_by_language") or {},
                    "repair_reasons_by_language": item.get("repair_reasons_by_language") or {},
                    "repair_priority": repair_priority,
                    "source_groups": source_groups,
                    "generator": _normalize_generator_mode(generator_mode),
                    "force": force,
                    "source_transport_attempt": source_transport_attempt,
                    "workflow_stage": (
                        "knowledge_source_first_queued"
                        if source_first
                        else "knowledge_repair_queued"
                    ),
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

    async def rebalance_active_repair_task_priorities(self, db) -> dict[str, int]:
        """Restore current canonical priority after earlier queue decisions.

        Repair priority is a projection of the latest brief diagnostics, not a
        durable property of the task that first observed it. Recompute queued
        automatic work from the catalogue so a once-draft but now-complete
        record cannot continue to outrank a real required-field gap.
        """
        tasks = list(
            (
                await db.execute(
                    select(Task)
                    .where(
                        Task.task_type.in_(
                            (
                                TaskType.UPDATE_DISEASE_KNOWLEDGE,
                                TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES,
                            )
                        ),
                        Task.status.in_((TaskStatus.PENDING, TaskStatus.QUEUED)),
                    )
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()
        )
        catalogue = await self.list_catalogue(db)
        canonical_priority_by_disease = {
            str(item.get("disease_id") or "").strip().upper(): str(
                item.get("repair_priority") or ""
            ).strip().lower()
            for item in catalogue
            if str(item.get("disease_id") or "").strip()
        }
        changed = 0
        examined = 0
        for task in tasks:
            input_data = dict(task.input_data or {})
            tags = {str(tag) for tag in (task.tags or [])}
            if not input_data.get("targeted_repair") and "auto_repair" not in tags:
                continue
            disease_ids = _active_knowledge_tasks_by_disease([task])
            disease_id = next(iter(disease_ids), None)
            repair_priority = canonical_priority_by_disease.get(
                str(disease_id or "").upper(),
                str(input_data.get("repair_priority") or "").strip().lower(),
            )
            if repair_priority not in {"urgent", "high", "normal", "low"}:
                continue
            examined += 1
            desired = _knowledge_repair_task_priority(None, repair_priority)
            current = str(
                getattr(getattr(task, "priority", None), "value", getattr(task, "priority", ""))
            ).lower()
            if current != desired.value:
                task.priority = desired
                changed += 1
            if input_data.get("repair_priority") != repair_priority:
                input_data["repair_priority"] = repair_priority
                task.input_data = input_data
        return {"examined": examined, "changed": changed}

    async def invalidate_profiles_with_unresolved_source_gaps(
        self,
        db,
        latest_source_tasks_by_disease: Mapping[str, Task],
    ) -> dict[str, int]:
        """Demote only profiles whose newest source task still lacks evidence.

        A model task may have been queued before source-first execution became
        mandatory.  Its old partial publication must not survive a newer
        source task that proves a required field is still unsupported.  The
        caller supplies only the newest task per disease, so an older failure
        can never override a later ready source packet.
        """

        affected_disease_ids = {
            disease_id
            for disease_id, task in latest_source_tasks_by_disease.items()
            if isinstance(task.output_data, dict)
            and _source_gap_blocks_publication(task.output_data)
        }
        changed = 0
        retained = 0
        archived = 0
        for disease_id in affected_disease_ids:
            task = latest_source_tasks_by_disease[disease_id]
            try:
                disease = self._find_disease(disease_id)
            except ValueError:
                disease = None
            exclusion_reason = (
                public_disease_page_exclusion_reason(disease)
                if disease is not None
                else None
            )
            if exclusion_reason:
                archived += await _archive_non_public_disease_briefs(
                    db,
                    disease_id,
                    exclusion_reason,
                )
                continue
            output = task.output_data if isinstance(task.output_data, dict) else {}
            evidence_gate = output.get("evidence_gate")
            coverage = (
                evidence_gate.get("coverage")
                if isinstance(evidence_gate, dict) and isinstance(evidence_gate.get("coverage"), dict)
                else {}
            )
            reason = (
                str(evidence_gate.get("reason") or "insufficient_source_evidence")
                if isinstance(evidence_gate, dict)
                else "insufficient_source_evidence"
            )
            rows = list(
                (
                    await db.execute(
                        select(DiseaseKnowledgeBrief)
                        .where(DiseaseKnowledgeBrief.disease_id == disease_id)
                        .with_for_update(skip_locked=True)
                    )
                ).scalars().all()
            )
            active_source_ids = _active_approved_source_ids(
                await _existing_sources(db, disease_id)
            )
            source_records = _source_records_by_id(
                await _existing_sources(db, disease_id)
            )
            for brief in rows:
                if disease is not None:
                    retention = _restore_brief_with_retained_evidence(
                        brief,
                        disease,
                        active_source_ids,
                        source_records,
                    )
                    if retention.eligible:
                        retained += 1
                        continue
                metadata = _brief_metadata(brief)
                if (
                    brief.status == "draft"
                    and metadata.get("automation_state") == "awaiting_evidence"
                    and metadata.get("source_task_uuid") == task.task_uuid
                ):
                    continue
                brief.status = "draft"
                brief.metadata_ = {
                    **metadata,
                    "automation_state": "awaiting_evidence",
                    "block_reason": reason,
                    "source_task_uuid": task.task_uuid,
                    "source_coverage": coverage,
                    "evidence_policy_version": output.get(
                        "evidence_policy_version", EVIDENCE_POLICY_VERSION
                    ),
                    "source_strategy_version": output.get(
                        "source_strategy_version", KNOWLEDGE_SOURCE_STRATEGY_VERSION
                    ),
                }
                changed += 1
        if changed or archived:
            await db.flush()
        return {
            "affected_disease_count": len(affected_disease_ids),
            "updated_brief_count": changed,
            "retained_brief_count": retained,
            "archived_profile_count": archived,
        }

    async def reconcile_retained_profiles(self, db) -> dict[str, int]:
        """Recover evidence wrongly demoted by legacy refreshes, then republish it.

        The operation is idempotent and intentionally conservative: it revives
        only rows carrying the historical non-authoritative-refresh marker and
        only publishes profiles that independently pass the retained-evidence
        boundary. Everything else remains a source/model automation task.
        """

        restored_sources = await _restore_sources_demoted_by_refresh_miss(db)
        canonical_diseases = {
            str(disease.get("disease_id") or "").strip().upper(): disease
            for disease in self.load_standard_diseases()
            if str(disease.get("disease_id") or "").strip()
        }
        archived_profiles = 0
        for disease_id, canonical_disease in canonical_diseases.items():
            exclusion_reason = public_disease_page_exclusion_reason(canonical_disease)
            if exclusion_reason:
                archived_profiles += await _archive_non_public_disease_briefs(
                    db,
                    disease_id,
                    exclusion_reason,
                )

        orphaned_disease_ids = list(
            (
                await db.execute(
                    select(DiseaseKnowledgeBrief.disease_id)
                    .where(
                        DiseaseKnowledgeBrief.status.in_(
                            ["draft", "published", "requires_review"]
                        )
                    )
                    .distinct()
                )
            ).scalars().all()
        )
        for raw_disease_id in orphaned_disease_ids:
            disease_id = str(raw_disease_id or "").strip().upper()
            canonical_disease = canonical_diseases.get(disease_id)
            archive_reason = (
                "not_in_canonical_disease_catalogue"
                if canonical_disease is None
                else public_disease_page_exclusion_reason(canonical_disease)
            )
            if archive_reason is None:
                continue
            archived_profiles += await _archive_non_public_disease_briefs(
                db,
                disease_id,
                archive_reason,
            )
        rows = list(
            (
                await db.execute(
                    select(DiseaseKnowledgeBrief)
                    .where(DiseaseKnowledgeBrief.status == "draft")
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()
        )
        draft_disease_ids = {
            str(brief.disease_id or "").strip().upper()
            for brief in rows
            if str(brief.disease_id or "").strip()
        }
        source_rows = list(
            (
                await db.execute(
                    select(DiseaseKnowledgeSource).where(
                        DiseaseKnowledgeSource.disease_id.in_(draft_disease_ids)
                    )
                )
            ).scalars().all()
        ) if draft_disease_ids else []
        sources_by_disease: dict[str, list[DiseaseKnowledgeSource]] = {}
        for source in source_rows:
            sources_by_disease.setdefault(source.disease_id, []).append(source)
        active_source_ids_by_disease = {
            disease_id: _active_approved_source_ids(
                sources_by_disease.get(disease_id, [])
            )
            for disease_id in draft_disease_ids
        }
        source_records_by_disease = {
            disease_id: _source_records_by_id(
                sources_by_disease.get(disease_id, [])
            )
            for disease_id in draft_disease_ids
        }
        restored_profiles = 0
        examined_profiles = 0
        for brief in rows:
            disease_id = str(brief.disease_id or "").strip().upper()
            try:
                disease = self._find_disease(disease_id)
            except ValueError:
                continue
            if public_disease_page_exclusion_reason(disease):
                continue
            examined_profiles += 1
            retention = _restore_brief_with_retained_evidence(
                brief,
                disease,
                active_source_ids_by_disease[disease_id],
                source_records_by_disease[disease_id],
            )
            if retention.eligible:
                restored_profiles += 1
        if restored_profiles or archived_profiles:
            await db.flush()
        return {
            "restored_source_count": restored_sources,
            "examined_profile_count": examined_profiles,
            "restored_profile_count": restored_profiles,
            "archived_profile_count": archived_profiles,
        }

    async def restore_profiles_after_transient_source_transport(
        self,
        db,
        latest_source_tasks_by_disease: Mapping[str, Task],
    ) -> dict[str, int]:
        """Restore valid profiles that an older transport failure demoted.

        Earlier source-first revisions conservatively demoted every source
        gap, including adapter outages. A profile is restored only when its
        latest source task explicitly records a transport state, the task was
        the one that demoted it, and the stored profile still passes current
        schema, source, and citation gates. A later evidence gap can demote it
        again through ``invalidate_profiles_with_unresolved_source_gaps``.
        """

        restored = 0
        examined = 0
        for disease_id, task in latest_source_tasks_by_disease.items():
            output = task.output_data if isinstance(task.output_data, dict) else {}
            if _source_discovery_state(output) != "awaiting_source_transport":
                continue
            try:
                disease = self._find_disease(disease_id)
            except ValueError:
                continue
            if public_disease_page_exclusion_reason(disease):
                continue
            rows = list(
                (
                    await db.execute(
                        select(DiseaseKnowledgeBrief)
                        .where(DiseaseKnowledgeBrief.disease_id == disease_id)
                        .with_for_update(skip_locked=True)
                    )
                ).scalars().all()
            )
            active_source_ids = _active_approved_source_ids(
                await _existing_sources(db, disease_id)
            )
            source_records = _source_records_by_id(
                await _existing_sources(db, disease_id)
            )
            for brief in rows:
                metadata = _brief_metadata(brief)
                if (
                    brief.status != "draft"
                    or metadata.get("source_task_uuid") != task.task_uuid
                ):
                    continue
                examined += 1
                retention = _restore_brief_with_retained_evidence(
                    brief,
                    disease,
                    active_source_ids,
                    source_records,
                )
                if retention.eligible:
                    restored += 1
        if restored:
            await db.flush()
        return {"examined_brief_count": examined, "restored_brief_count": restored}

    async def enqueue_source_refresh_tasks(
        self,
        db,
        *,
        disease_ids: list[str] | None = None,
        source_groups: list[str] | None = None,
        force: bool = True,
        enqueue_ai_after_source_refresh: bool = False,
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
            if (not requested_ids or str(item.get("disease_id") or "").upper() in requested_ids)
            and not public_disease_page_exclusion_reason(item)
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
                    "enqueue_ai_after_source_refresh": bool(
                        enqueue_ai_after_source_refresh
                    ),
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
                    "enqueue_ai_after_source_refresh": bool(
                        enqueue_ai_after_source_refresh
                    ),
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
                try:
                    detail = self._ontology.concept_detail(wanted)
                except KeyError:
                    return attach_profile_schema(row)
                disease = attach_profile_schema(
                    self._with_ontology_profile_context(row, detail)
                )
                catalogue = {
                    str(item.get("disease_id") or "").upper(): item
                    for item in self.load_standard_diseases()
                }
                related_entities: list[dict[str, Any]] = []
                query_aliases: list[str] = []
                seen_aliases: set[str] = set()

                def add_alias(
                    value: Any,
                    *,
                    _seen_aliases: set[str] = seen_aliases,
                    _query_aliases: list[str] = query_aliases,
                ) -> None:
                    alias = " ".join(str(value or "").split()).strip()
                    key = alias.casefold()
                    if alias and key not in _seen_aliases:
                        _seen_aliases.add(key)
                        _query_aliases.append(alias)

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

    @staticmethod
    def _with_ontology_profile_context(
        disease: dict[str, Any],
        detail: dict[str, Any],
    ) -> dict[str, Any]:
        """Attach stable semantic facets required by profile resolution."""
        return {
            **disease,
            "ontology_context": {
                "definition": detail.get("definition"),
                "facet_tags": detail.get("facet_tags") or {},
            },
        }

    def _attach_ontology_profile_context(self, disease: dict[str, Any]) -> dict[str, Any]:
        disease_id = str(disease.get("disease_id") or "").strip().upper()
        if not disease_id:
            return disease
        try:
            detail = self._ontology.concept_detail(disease_id)
        except KeyError:
            return disease
        return self._with_ontology_profile_context(disease, detail)

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
            # Quality/repair assessment is CPU work. Yield between diseases so
            # JSON publication and heartbeat callbacks do not wait for the
            # entire catalogue; cancellation also takes effect at this boundary.
            await asyncio.sleep(0)
            disease = attach_profile_schema(
                self._attach_ontology_profile_context(raw_disease)
            )
            disease_id = str(disease.get("disease_id") or "")
            language_briefs = briefs_by_disease.get(disease_id, {})
            language_quality = {
                lang: assess_knowledge_brief(brief, lang, disease=disease)
                for lang, brief in language_briefs.items()
            }
            repair_metadata = _profile_repair_metadata(
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
            exclusion_reason = public_disease_page_exclusion_reason(disease)
            if exclusion_reason:
                # Aggregate and summary rows are deliberately not public
                # knowledge pages. Historical drafts must not look like
                # unfinished review work or consume the repair backlog.
                published_languages = []
                blocked_languages = sorted(language_briefs)
                knowledge_status = "blocked"
                repair_metadata = {
                    "repair_sections": [],
                    "repair_sections_by_language": {"en": [], "zh": []},
                    "repair_reasons_by_language": {"en": [], "zh": []},
                    "required_gap_sections": [],
                    "repair_priority": "none",
                }
            else:
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
                "blocked"
                if exclusion_reason
                else "full"
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
                    **repair_metadata,
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
        repair_metadata = _profile_repair_metadata(list(brief_rows), disease)
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
        exclusion_reason = public_disease_page_exclusion_reason(disease)
        if exclusion_reason:
            published_languages = []
            blocked_languages = sorted(brief.language for brief in brief_rows)
            knowledge_status = "blocked"
            repair_metadata = {
                "repair_sections": [],
                "repair_sections_by_language": {"en": [], "zh": []},
                "repair_reasons_by_language": {"en": [], "zh": []},
                "required_gap_sections": [],
                "repair_priority": "none",
            }
        else:
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
            "blocked"
            if exclusion_reason
            else "full"
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
            **repair_metadata,
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

    @property
    def _stage_run(self):
        # A service instance may execute multiple tasks concurrently.
        return _KNOWLEDGE_STAGE_RUN.get()

    async def _store_source_refresh(
        self,
        *,
        disease: dict[str, Any],
        disease_payload: dict[str, Any],
        target_sections: list[str],
        publication_evidence_sections: list[str],
        fetch_report: SourceFetchReport,
        enabled_sources: list[str],
        task_uuid: str | None,
    ) -> dict[str, Any]:
        """Persist candidates and publication eligibility in one store transaction."""
        candidates = fetch_report.candidates
        if getattr(self, "_stage_run", None):
            await self._stage_run.transition("store", metrics={"step": "sources"})
        async with get_database() as db:
            await _acquire_disease_knowledge_lock(db, disease["disease_id"])
            legacy_source_recovery = await _restore_sources_demoted_by_refresh_miss(
                db, disease_id=disease["disease_id"],
            )
            for candidate in candidates:
                await _upsert_source(db, candidate)
            source_qualification = await _qualify_pending_sources(db, disease["disease_id"])
            source_reconciliation = await _reconcile_source_refresh(
                db, disease["disease_id"], candidates, enabled_sources,
            )
            existing_brief_rows = list(
                (await db.execute(
                    select(DiseaseKnowledgeBrief)
                    .where(DiseaseKnowledgeBrief.disease_id == disease["disease_id"])
                    .order_by(DiseaseKnowledgeBrief.language.asc())
                )).scalars().all()
            )
            source_rows = await _existing_sources(db, disease["disease_id"])
            source_dicts = [source_to_dict(row) for row in source_rows]
            profile_schema = resolve_knowledge_profile_schema(disease_payload)
            profile_schema_signature = knowledge_profile_schema_signature(disease_payload)
            direct_packet = prepare_evidence_packet(
                source_dicts,
                profile_schema,
                target_sections=publication_evidence_sections,
                entity_aliases=disease_payload["evidence_entity_aliases"],
                **_knowledge_evidence_limits(),
                allowed_source_types=AIDiseaseBriefGenerator.PUBLIC_SOURCE_TYPES,
            )
            direct_evidence_quality = direct_packet.assessment
            inherited_sources = await _related_parent_sources(db, disease_payload)
            generation_packet = prepare_evidence_packet(
                [*direct_packet.sources, *inherited_sources],
                profile_schema,
                target_sections=publication_evidence_sections,
                entity_aliases=disease_payload["evidence_entity_aliases"],
                **_knowledge_evidence_limits(),
                allowed_source_types=AIDiseaseBriefGenerator.PUBLIC_SOURCE_TYPES,
            )
            evidence_quality = generation_packet.assessment
            evidence_gate = _evidence_gate_result(generation_packet)
            source_packet_manifest_id = generation_packet.manifest.manifest_id

            source_gap = evidence_gate["state"] != "ready_for_generation"
            source_discovery_result = {
                "source_gap": source_gap,
                "adapter_outcomes": fetch_report.adapter_outcomes,
            }
            source_discovery_state = _source_discovery_state(source_discovery_result)
            source_publication_blocked = _source_gap_blocks_publication(
                source_discovery_result
            )
            retained_profiles = 0
            if source_publication_blocked:
                reason = str(evidence_gate.get("reason") or "insufficient_source_evidence")
                coverage = evidence_gate.get("coverage") or {}
                active_source_ids = _active_approved_source_ids(source_rows)
                source_records = _source_records_by_id(source_rows)
                for brief in existing_brief_rows:
                    retention = _restore_brief_with_retained_evidence(
                        brief,
                        disease,
                        active_source_ids,
                        source_records,
                    )
                    if retention.eligible:
                        retained_profiles += 1
                        continue
                    brief.status = "draft"
                    brief.metadata_ = {
                        **_brief_metadata(brief),
                        "automation_state": "awaiting_evidence",
                        "block_reason": reason,
                        "evidence_policy_version": EVIDENCE_POLICY_VERSION,
                        "source_strategy_version": KNOWLEDGE_SOURCE_STRATEGY_VERSION,
                        "source_packet_manifest_id": source_packet_manifest_id,
                        "source_coverage": coverage,
                        "profile_schema_signature": profile_schema_signature,
                    }
                await db.flush()
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
                    "source_qualification": source_qualification,
                    "legacy_source_recovery": legacy_source_recovery,
                    "source_reconciliation": source_reconciliation,
                    "inherited_source_count": len(inherited_sources),
                    "source_packet_manifest_id": source_packet_manifest_id,
                    "source_gap": source_gap,
                    "source_discovery_state": source_discovery_state,
                    "source_publication_blocked": source_publication_blocked,
                    "retained_profiles": retained_profiles,
                    "evidence_gate": evidence_gate,
                    "evidence_quality": evidence_quality.to_dict(),
                    "direct_evidence_quality": direct_evidence_quality.to_dict(),
                    "adapter_outcomes": fetch_report.adapter_outcomes,
                    "adapter_durations": fetch_report.adapter_durations,
                    "evidence_limits": _knowledge_evidence_limits(),
                    "evidence_policy_version": EVIDENCE_POLICY_VERSION,
                    "source_strategy_version": KNOWLEDGE_SOURCE_STRATEGY_VERSION,
                    "profile_schema_signature": profile_schema_signature,
                    "workflow_stage": "knowledge_sources_refreshed",
                },
                success=not source_gap,
            )
            if getattr(self, "_stage_run", None):
                await self._stage_run.mark_dirty(db, "sources")
            from src.services.ingestion.capture import _current
            if _current.get():
                _current.get().verify()
            await db.commit()
            if task_uuid:
                await task_manager.update_task_progress(task_uuid, 100)
            return {
                "disease_id": disease["disease_id"],
                "knowledge_profile_type": disease["knowledge_profile_type"],
                "target_sections": target_sections,
                "evidence_target_sections": publication_evidence_sections,
                "fetched_sources": len(candidates),
                "total_sources": len(source_dicts),
                "inherited_source_count": len(inherited_sources),
                "evidence_quality": evidence_quality.to_dict(),
                "direct_evidence_quality": direct_evidence_quality.to_dict(),
                "adapter_outcomes": fetch_report.adapter_outcomes,
                "adapter_durations": fetch_report.adapter_durations,
                "source_packet_manifest_id": source_packet_manifest_id,
                "source_gap": source_gap,
                "source_discovery_state": source_discovery_state,
                "source_publication_blocked": source_publication_blocked,
                "retained_profiles": retained_profiles,
                "evidence_gate": evidence_gate,
                "evidence_policy_version": EVIDENCE_POLICY_VERSION,
                "source_strategy_version": KNOWLEDGE_SOURCE_STRATEGY_VERSION,
                "profile_schema_signature": profile_schema_signature,
                "uncovered_sections": list(
                    generation_packet.coverage.missing_required_sections
                ),
                "source_only": True,
            }

    async def update_disease(
        self,
        disease_id: str,
        *,
        enabled_sources: list[str] | None = None,
        force: bool = False,
        generator_mode: str = "auto",
        target_languages: list[str] | None = None,
        requested_sections_by_language: dict[str, list[str]] | None = None,
        dry_run: bool = False,
        task_uuid: str | None = None,
        source_only: bool = False,
        refresh_existing_on_source_change: bool | None = None,
        source_refreshed_task_uuid: str | None = None,
        repair_reasons_by_language: Mapping[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        disease = self._find_disease(disease_id)
        exclusion_reason = public_disease_page_exclusion_reason(disease)
        if exclusion_reason:
            archived_profiles = 0
            if not dry_run:
                run = self._stage_run
                if run:
                    # Collect already started for a brief. Record that this pass
                    # only saw the registry exclusion, then archive in store.
                    if run.current == "collect":
                        await run.finish(
                            metrics={"step": "registry", "source_only": bool(source_only)}
                        )
                    if run.current != "organize":
                        await run.transition("organize", metrics={"step": "non_public"})
                    await run.transition("store", metrics={"step": "archive"})
                async with get_database() as db:
                    await _acquire_disease_knowledge_lock(db, disease["disease_id"])
                    archived_profiles = await _archive_non_public_disease_briefs(
                        db,
                        disease["disease_id"],
                        exclusion_reason,
                    )
                    if archived_profiles and getattr(self, "_stage_run", None):
                        await self._stage_run.mark_dirty(db, disease["disease_id"])
                    await db.commit()
            await _log_task(
                task_uuid,
                entry_type="info",
                title="Knowledge Update Skipped",
                content=(
                    f"{disease['disease_id']} is excluded from public disease pages "
                    f"({exclusion_reason}); no source or model work was performed."
                ),
                metadata={
                    "disease_id": disease["disease_id"],
                    "catalogue_archive_reason": exclusion_reason,
                    "archived_profile_count": archived_profiles,
                    "workflow_stage": "knowledge_non_public_skipped",
                },
            )
            if task_uuid:
                await task_manager.update_task_progress(task_uuid, 100)
            return {
                "disease_id": disease["disease_id"],
                "skipped": True,
                "skip_reason": exclusion_reason,
                "archived_profile_count": archived_profiles,
                "source_only": source_only,
            }
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
                "requested_target_sections_by_language": requested_sections_by_language or {},
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
                "evidence_entity_aliases": _evidence_entity_aliases(disease),
            }
            fetch_report = SourceFetchReport(candidates=[], adapter_outcomes={}, adapter_durations={})
            candidates = []
            if source_only:
                fetch_report = await _fetch_sources_for_update(
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
            else:
                async with get_database() as db:
                    source_dicts = [source_to_dict(row) for row in await _existing_sources(db, disease["disease_id"])]
                    source_dicts.extend(await _related_parent_sources(db, disease_payload))
            if self._stage_run and self._stage_run.current != "organize":
                await self._stage_run.transition("organize", metrics={"step": "evidence" if source_only else "brief"})
            profile_schema = resolve_knowledge_profile_schema(disease_payload)
            evidence_packet = prepare_evidence_packet(
                source_dicts,
                profile_schema,
                target_sections=target_sections,
                entity_aliases=disease_payload["evidence_entity_aliases"],
                **_knowledge_evidence_limits(),
                allowed_source_types=AIDiseaseBriefGenerator.PUBLIC_SOURCE_TYPES,
            )
            source_dicts = list(evidence_packet.sources)
            evidence_quality = evidence_packet.assessment
            if source_only:
                await self._advance_dry_run_to_store()
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
                    f"{disease['disease_id']} stored evidence insufficient: {reason}. "
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
            await self._advance_dry_run_to_store()
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

        publication_evidence_sections = _publication_evidence_sections(
            resolve_knowledge_profile_schema(disease)
        )
        if source_only:
            disease_payload = {
                **disease,
                "target_sections": publication_evidence_sections,
                "evidence_target_sections": publication_evidence_sections,
                "evidence_entity_aliases": _evidence_entity_aliases(disease),
            }
            fetch_report = await _fetch_sources_for_update(
                self.fetcher, disease_payload, enabled_sources=enabled_sources,
                target_sections=publication_evidence_sections,
            )
            if getattr(self, "_stage_run", None):
                await self._stage_run.transition("organize", metrics={"step": "evidence"})
            await _raise_if_cancelled(task_uuid)
            return await self._store_source_refresh(
                disease=disease, disease_payload=disease_payload,
                target_sections=publication_evidence_sections,
                publication_evidence_sections=publication_evidence_sections,
                fetch_report=fetch_report, enabled_sources=enabled_sources, task_uuid=task_uuid,
            )

        # Collect only reads rows already stored. The session closes before organize.
        async with get_database() as db:
            source_rows = await _existing_sources(db, disease["disease_id"])
            source_dicts = [source_to_dict(row) for row in source_rows]
            inherited_sources = await _related_parent_sources(db, disease)
            existing_brief_rows = list(
                (await db.execute(
                    select(DiseaseKnowledgeBrief)
                    .where(DiseaseKnowledgeBrief.disease_id == disease["disease_id"])
                    .order_by(DiseaseKnowledgeBrief.language.asc())
                )).scalars().all()
            )
            snapshot = _knowledge_snapshot(source_rows, existing_brief_rows, inherited_sources)
        if self._stage_run and self._stage_run.current != "organize":
            await self._stage_run.transition("organize", metrics={"step": "brief"})

        existing_briefs_by_language = {
            brief.language: brief for brief in existing_brief_rows
        }
        ordered_sections = [
            "brief",
            *disease["profile_schema"]["required_fields"],
            *disease["profile_schema"]["optional_fields"],
        ]
        target_sections_by_language = _resolve_repair_sections_by_language(
            existing_brief_rows,
            disease,
            ordered_sections=ordered_sections,
            force=force,
            target_languages=target_languages,
            requested_sections_by_language=requested_sections_by_language,
        )
        normalized_repair_reasons = _normalize_repair_reasons_by_language(
            repair_reasons_by_language
        )
        target_set = {
            field
            for fields in target_sections_by_language.values()
            for field in fields
        }
        target_sections = [field for field in ordered_sections if field in target_set]
        publication_evidence_sections = _publication_evidence_sections(
            resolve_knowledge_profile_schema(disease)
        )
        disease_payload = {
            **disease,
            # Source discovery and publication validation always cover the
            # complete required profile.  ``target_sections`` later narrows
            # only the prose being rewritten, never the evidence contract.
            "target_sections": publication_evidence_sections,
            "evidence_target_sections": publication_evidence_sections,
            "repair_target_sections": target_sections,
            "evidence_entity_aliases": _evidence_entity_aliases(disease),
        }
        candidates = []
        fetch_report = SourceFetchReport(candidates=[], adapter_outcomes={}, adapter_durations={})
        brief_rows = []
        await _raise_if_cancelled(task_uuid)
        if task_uuid:
            await task_manager.update_task_progress(task_uuid, 40)
        profile_schema = resolve_knowledge_profile_schema(disease_payload)
        direct_packet = prepare_evidence_packet(
            source_dicts,
            profile_schema,
            target_sections=publication_evidence_sections,
            entity_aliases=disease_payload["evidence_entity_aliases"],
            **_knowledge_evidence_limits(),
            allowed_source_types=AIDiseaseBriefGenerator.PUBLIC_SOURCE_TYPES,
        )
        direct_evidence_quality = direct_packet.assessment
        generation_packet = prepare_evidence_packet(
            [*direct_packet.sources, *inherited_sources],
            profile_schema,
            target_sections=publication_evidence_sections,
            entity_aliases=disease_payload["evidence_entity_aliases"],
            **_knowledge_evidence_limits(),
            allowed_source_types=AIDiseaseBriefGenerator.PUBLIC_SOURCE_TYPES,
        )
        evidence_quality = generation_packet.assessment
        evidence_gate = _evidence_gate_result(generation_packet)
        registry_definition_only = _is_registry_definition_only_packet(
            generation_packet
        )
        generation_sources = list(generation_packet.sources)
        source_packet_manifest_id = generation_packet.manifest.manifest_id

        if evidence_gate["state"] != "ready_for_generation":
            reason = str(evidence_gate["reason"] or "insufficient_source_evidence")
            await _log_task(
                task_uuid,
                entry_type="error",
                title="Knowledge Evidence Blocked",
                content=(
                    f"Stored sources for {disease['disease_id']} lack usable evidence ({reason}). "
                    "Run REFRESH_DISEASE_KNOWLEDGE_SOURCES before retrying the brief task."
                ),
                metadata={
                    "disease_id": disease["disease_id"],
                    "source_count": len(source_dicts),
                    "reason": reason,
                    "evidence_gate": evidence_gate,
                    "workflow_stage": "knowledge_evidence_blocked",
                },
                success=False,
            )
            raise KnowledgeEvidenceInsufficientError(
                f"{disease['disease_id']} stored evidence insufficient: {reason}. "
                f"Missing required sections: {', '.join(generation_packet.coverage.missing_required_sections) or 'none'}. "
                "Run REFRESH_DISEASE_KNOWLEDGE_SOURCES before retrying. No brief was persisted."
            )

        if task_uuid:
            await task_manager.update_task_progress(task_uuid, 55)

        brief_languages = ("en", "zh")
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
        # Registry evidence can publish the entity definition and a short
        # lead only. Optional legacy sections are cleared by
        # _merge_repair_payload during a semantic contract migration.
        target_sections_by_language = _registry_definition_target_sections(
            target_sections_by_language,
            registry_definition_only=registry_definition_only,
        )
        target_set = {
            field
            for fields in target_sections_by_language.values()
            for field in fields
        }
        target_sections = [field for field in ordered_sections if field in target_set]
        disease_payload = {
            **disease_payload,
            "target_sections": target_sections,
            "evidence_target_sections": publication_evidence_sections,
            "_evidence_packet_prepared": True,
        }

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
                    "repair_reasons": normalized_repair_reasons.get(language, []),
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
                if _translation_source_usable(
                    english_result,
                    target_sections=language_targets,
                ):
                    translated = await _translate_brief_result(
                        generator,
                        disease={
                            **disease_payload,
                            "target_sections": language_targets,
                            "repair_context": normalized_repair_reasons.get(language, []),
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
                    "repair_context": normalized_repair_reasons.get(language, []),
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
            *_generated_profile_failures(
                generated_results,
                target_sections_by_language=target_sections_by_language,
                allow_progressive_drafts=bool(requested_sections_by_language),
            ),
            *_bilingual_alignment_failures(generated_results),
        ]
        if generation_failures:
            for result in generated_results:
                payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
                trace = result.get("trace") if isinstance(result.get("trace"), dict) else {}
                language = str(payload.get("language") or trace.get("language") or "unknown").strip().lower()
                await _log_task(
                    task_uuid,
                    entry_type="warning",
                    title=f"{language.upper()} Brief Attempt Rejected",
                    content=(
                        "The generated attempt was not persisted because the publication gate "
                        "found an unsupported, incomplete, or invalid target field."
                    ),
                    metadata={
                        "disease_id": disease["disease_id"],
                        "language": language,
                        "target_sections": target_sections_by_language.get(language, []),
                        "trace_error": trace.get("error"),
                        "citation_failures": trace.get("citation_failures") or [],
                        "quality_repair": (payload.get("metadata") or {}).get("quality_repair"),
                        "workflow_stage": f"brief_generation_{language}_rejected",
                    },
                    success=False,
                    prompt=trace.get("prompt") if isinstance(trace.get("prompt"), str) else None,
                    response=trace.get("response") if isinstance(trace.get("response"), str) else None,
                    model_used=trace.get("model") if isinstance(trace.get("model"), str) else None,
                    tokens_used=_total_tokens_from_usage(trace.get("token_usage")),
                    duration=(
                        float(trace.get("duration"))
                        if trace.get("duration") is not None
                        else None
                    ),
                )
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

        _raise_if_model_work_failed()
        if getattr(self, "_stage_run", None):
            await self._stage_run.transition("store", metrics={"step": "briefs"})
        async with get_database() as db:
            await _acquire_disease_knowledge_lock(db, disease["disease_id"])
            current_sources = await _existing_sources(db, disease["disease_id"])
            current_briefs = list((await db.execute(
                select(DiseaseKnowledgeBrief)
                .where(DiseaseKnowledgeBrief.disease_id == disease["disease_id"])
                .order_by(DiseaseKnowledgeBrief.language.asc())
            )).scalars().all())
            current_inherited = await _related_parent_sources(db, disease)
            if snapshot != _knowledge_snapshot(current_sources, current_briefs, current_inherited):
                raise KnowledgeEvidenceInsufficientError(
                    "Knowledge sources or briefs changed during generation; retry using the latest stored evidence."
                )
            if getattr(self, "_stage_run", None):
                await self._stage_run.mark_dirty(db, disease["disease_id"])
            for result in generated_results:
                await _raise_if_cancelled(task_uuid)
                payload = result["payload"]
                payload["metadata"] = {
                    **(payload.get("metadata") or {}),
                    "evidence_quality": evidence_quality.to_dict(),
                    "evidence_gate": evidence_gate,
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

            from src.services.ingestion.capture import _current
            if _current.get():
                _current.get().verify()
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
            "evidence_gate": evidence_gate,
            "adapter_outcomes": fetch_report.adapter_outcomes,
            "adapter_durations": fetch_report.adapter_durations,
            "brief_statuses": brief_statuses,
            "published_languages": published_languages,
            "blocked_languages": blocked_languages,
        }

    async def _with_collection_stages(self, task: Task, execute, *, source_only=False) -> dict[str, Any]:
        from src.services.ingestion.capture import capture_sources
        from src.services.ingestion.stages import StageRun

        run = await StageRun.start(task.task_uuid, "knowledge")
        token = _KNOWLEDGE_STAGE_RUN.set(run)
        try:
            with capture_sources(task.task_uuid, run.attempt, "knowledge") as capture:
                # Both tasks start at collect. A brief only reads stored sources.
                await run.transition(
                    "collect",
                    metrics={
                        "source_only": source_only,
                        "step": "sources" if source_only else "stored_sources",
                    },
                )
                result = await execute(task)
                capture.verify()
                _raise_if_model_work_failed()
                await run.finish()
                return result
        except BaseException as exc:
            await run.finish(error=str(exc) or type(exc).__name__)
            raise
        finally:
            _KNOWLEDGE_STAGE_RUN.reset(token)

    async def execute_task(self, task: Task) -> dict[str, Any]:
        return await self._with_collection_stages(task, self._execute_task)

    async def _advance_dry_run_to_store(self) -> None:
        """Record a completed store stage for a preview that writes nothing."""
        run = self._stage_run
        if run is None or run.current != "organize":
            return
        await run.transition(
            "store",
            metrics={"step": "dry_run", "dry_run": True, "brief_written": False},
        )

    async def _complete_superseded_brief_stages(self) -> None:
        """Close collect, organize, and store when no brief is written.

        Only while collect is still current. A later stage means this repair
        already entered model or store work, and this path must not add a
        publish_dirty row.
        """
        run = self._stage_run
        if run is None or run.current != "collect":
            return
        metrics = {
            "step": "superseded",
            "skip_reason": "superseded_by_newer_published_profile",
            "source_only": False,
            "brief_written": False,
        }
        await run.finish(metrics=metrics)
        await run.transition("organize", metrics=metrics)
        await run.transition("store", metrics=metrics)

    async def _execute_task(self, task: Task) -> dict[str, Any]:
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
        if inp.get("source_only"):
            raise ValueError(
                "Source collection requires REFRESH_DISEASE_KNOWLEDGE_SOURCES; "
                "UPDATE_DISEASE_KNOWLEDGE only reads stored sources."
            )
        source_only = False
        automatic_repair = bool(
            inp.get("targeted_repair") or "auto_repair" in (task.tags or [])
        )
        if (
            automatic_repair
            and not source_only
            and await _automatic_model_repair_is_superseded(
                disease_id,
                inp.get("source_refreshed_task_uuid"),
            )
        ):
            await _log_task(
                task.task_uuid,
                entry_type="info",
                title="Superseded Knowledge Repair Skipped",
                content=(
                    "A newer bilingual published profile already incorporates work after "
                    "this task's source certificate. No model request was made."
                ),
                metadata={
                    "disease_id": disease_id,
                    "source_refreshed_task_uuid": inp.get("source_refreshed_task_uuid"),
                    "workflow_stage": "knowledge_repair_superseded",
                },
            )
            await task_manager.update_task_progress(task.task_uuid, 100)
            await self._complete_superseded_brief_stages()
            return {
                "disease_id": disease_id,
                "skipped": True,
                "skip_reason": "superseded_by_newer_published_profile",
                "source_refreshed_task_uuid": inp.get("source_refreshed_task_uuid"),
            }
        refresh_existing_on_source_change = inp.get("refresh_existing_on_source_change")
        target_languages = inp.get("repair_languages") or inp.get("languages")
        if isinstance(target_languages, str):
            target_languages = [target_languages]
        if not isinstance(target_languages, list):
            target_languages = None
        requested_sections_by_language = inp.get("repair_sections_by_language")
        if not isinstance(requested_sections_by_language, dict):
            requested_sections_by_language = None
        repair_reasons_by_language = inp.get("repair_reasons_by_language")
        if not isinstance(repair_reasons_by_language, dict):
            repair_reasons_by_language = None

        return await self.update_disease(
            disease_id,
            enabled_sources=list(source_groups) if isinstance(source_groups, list) else [],
            force=force,
            generator_mode=generator_mode,
            target_languages=target_languages,
            requested_sections_by_language=requested_sections_by_language,
            dry_run=bool(inp.get("dry_run", False)),
            task_uuid=task.task_uuid,
            source_only=source_only,
            refresh_existing_on_source_change=(
                None
                if refresh_existing_on_source_change is None
                else bool(refresh_existing_on_source_change)
            ),
            source_refreshed_task_uuid=inp.get("source_refreshed_task_uuid"),
            repair_reasons_by_language=repair_reasons_by_language,
        )

    async def execute_source_refresh_task(self, task: Task) -> dict[str, Any]:
        return await self._with_collection_stages(task, self._execute_source_refresh_task, source_only=True)

    async def _execute_source_refresh_task(self, task: Task) -> dict[str, Any]:
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

        requested_sections_by_language = inp.get("repair_sections_by_language")
        if not isinstance(requested_sections_by_language, dict):
            legacy_sections = inp.get("repair_sections")
            if isinstance(legacy_sections, list):
                requested_sections_by_language = {
                    language: list(legacy_sections) for language in ("en", "zh")
                }
            else:
                requested_sections_by_language = None
        target_languages = inp.get("repair_languages") or inp.get("languages")
        if isinstance(target_languages, str):
            target_languages = [target_languages]
        if not isinstance(target_languages, list):
            target_languages = None

        result = await self.update_disease(
            disease_id,
            enabled_sources=list(source_groups) if isinstance(source_groups, list) else [],
            force=bool(inp.get("force", True)),
            generator_mode=str(inp.get("generator", "auto")),
            target_languages=target_languages,
            requested_sections_by_language=requested_sections_by_language,
            dry_run=bool(inp.get("dry_run", False)),
            task_uuid=task.task_uuid,
            source_only=True,
            refresh_existing_on_source_change=False,
        )
        if result.get("skipped"):
            return result
        discovery_round = max(1, int(inp.get("source_discovery_round") or 1))
        discovery_round_limit = int(
            get_config().ai.knowledge_source_discovery_max_rounds
        )
        source_gap = bool(result.get("source_gap"))
        source_discovery_state = _source_discovery_state(result)
        uncovered_sections = [
            str(section).strip()
            for section in (result.get("uncovered_sections") or [])
            if str(section).strip()
        ]
        if (
            source_gap
            and inp.get("enqueue_ai_after_source_refresh")
            and not bool(inp.get("dry_run", False))
        ):
            if source_discovery_state == "awaiting_source_transport":
                try:
                    transport_attempt = max(1, int(inp.get("source_transport_attempt") or 1))
                except (TypeError, ValueError):
                    transport_attempt = 1
                cfg = get_config().ai
                retry_delay_seconds = _source_transport_retry_delay_seconds(
                    attempt=transport_attempt,
                    initial_delay_seconds=cfg.knowledge_automation_source_retry_seconds,
                    maximum_delay_seconds=cfg.knowledge_automation_evidence_retry_seconds,
                )
                retry_after = datetime.now(timezone.utc) + timedelta(
                    seconds=retry_delay_seconds
                )
                result.update(
                    {
                        "automation_state": source_discovery_state,
                        "source_discovery_exhausted": False,
                        "source_transport_attempt": transport_attempt,
                        "source_retry_after": retry_after.isoformat(),
                    }
                )
            elif discovery_round < discovery_round_limit and uncovered_sections:
                repair_scope = {
                    language: list(uncovered_sections) for language in ("en", "zh")
                }
                recovery_input = {
                    **inp,
                    "source_only": True,
                    "force": True,
                    "targeted_repair": True,
                    "repair_sections_by_language": repair_scope,
                    "repair_languages": ["en", "zh"],
                    "source_discovery_round": discovery_round + 1,
                    "source_recovery_of": task.task_uuid,
                    "initiated_via": "knowledge-section-evidence-recovery",
                }
                recovery_input.pop("repair_sections", None)
                existing_followup = None
                parent_task_id = getattr(task, "id", None)
                if isinstance(parent_task_id, int):
                    async with get_database() as db:
                        children = list(
                            (
                                await db.execute(
                                    select(Task).where(
                                        Task.parent_task_id == parent_task_id,
                                        Task.task_type
                                        == TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES,
                                    )
                                )
                            ).scalars().all()
                        )
                        existing_followup = next(
                            (
                                child
                                for child in children
                                if isinstance(child.input_data, dict)
                                and child.input_data.get("source_recovery_of")
                                == task.task_uuid
                            ),
                            None,
                        )
                followup = existing_followup
                if followup is None:
                    followup = await task_manager.create_task(
                        task_type=TaskType.REFRESH_DISEASE_KNOWLEDGE_SOURCES,
                        task_name=(
                            f"Enrich {disease_id} evidence for "
                            f"{', '.join(uncovered_sections)}"
                        ),
                        parent_task_id=parent_task_id if isinstance(parent_task_id, int) else None,
                        priority=_knowledge_repair_task_priority(
                            None,
                            str(inp.get("repair_priority") or "high"),
                        ),
                        description=(
                            f"Round {discovery_round + 1}/{discovery_round_limit} targeted source "
                            f"enrichment for {disease_id}; missing evidence: "
                            f"{', '.join(uncovered_sections)}."
                        ),
                        input_data=recovery_input,
                        tags=sorted(
                            {
                                *(getattr(task, "tags", None) or []),
                                "knowledge",
                                "auto_repair",
                                "evidence_recovery",
                            }
                        ),
                    )
                    await task_manager.add_workbook_entry(
                        followup.task_uuid,
                        entry_type="info",
                        title="Targeted Evidence Recovery Queued",
                        content=(
                            f"Queued source discovery round {discovery_round + 1}/"
                            f"{discovery_round_limit} for {disease_id}: "
                            f"{', '.join(uncovered_sections)}."
                        ),
                        content_type="text",
                        metadata={
                            "disease_id": disease_id,
                            "source_recovery_of": task.task_uuid,
                            "source_discovery_round": discovery_round + 1,
                            "source_discovery_round_limit": discovery_round_limit,
                            "repair_sections_by_language": repair_scope,
                            "workflow_stage": "knowledge_evidence_recovery_queued",
                        },
                    )
                    followup = (
                        await task_manager.update_task_status(
                            followup.task_uuid, TaskStatus.QUEUED
                        )
                        or followup
                    )
                result["source_followup_task_uuid"] = followup.task_uuid
                result["automation_state"] = "awaiting_evidence_refresh"
            else:
                result["automation_state"] = "awaiting_evidence"
                result["source_discovery_exhausted"] = True
            result["source_discovery_state"] = source_discovery_state
            result["source_discovery_round"] = discovery_round
            result["source_discovery_round_limit"] = discovery_round_limit
            await task_manager.merge_task_metadata(
                task.task_uuid,
                {
                    "knowledge_automation_state": result["automation_state"],
                    "source_discovery_state": source_discovery_state,
                    "source_discovery_round": discovery_round,
                    "source_discovery_round_limit": discovery_round_limit,
                    "uncovered_sections": uncovered_sections,
                    "source_followup_task_uuid": result.get("source_followup_task_uuid"),
                    "source_retry_after": result.get("source_retry_after"),
                    "source_transport_attempt": result.get("source_transport_attempt"),
                    "evidence_policy_version": EVIDENCE_POLICY_VERSION,
                    "source_strategy_version": KNOWLEDGE_SOURCE_STRATEGY_VERSION,
                },
            )
        if (
            inp.get("enqueue_ai_after_source_refresh")
            and not source_gap
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
                priority=_knowledge_repair_task_priority(
                    None,
                    str(inp.get("repair_priority") or "high"),
                ),
                description=(
                    f"Model-center repair for {disease_id} after source-only refresh "
                    f"{task.task_uuid}."
                ),
                input_data=repair_input,
                tags=sorted({*(getattr(task, "tags", None) or []), "knowledge", "auto_repair"}),
                parent_task_id=getattr(task, "id", None),
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
