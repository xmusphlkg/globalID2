"""Pure repair diagnostics, candidate selection, payload merging, and route-failure helpers."""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional

from src.domain import Task
from src.knowledge import (
    EVIDENCE_POLICY_VERSION,
    KNOWLEDGE_SCHEMA_VERSION,
    apply_knowledge_quality_gate,
    assess_knowledge_brief,
    public_disease_page_exclusion_reason,
)
from src.knowledge.citations import (
    normalize_knowledge_citations,
    validate_knowledge_citations,
)
from src.knowledge.profile_schema import knowledge_profile_schema_signature
from src.knowledge.repair_policy import _normalize_requested_sections_by_language

from .evidence import (
    KNOWLEDGE_PIPELINE_VERSION,
    _brief_metadata,
    _brief_uses_current_pipeline,
    _brief_uses_current_profile_schema,
)


def _generated_profile_failures(
    results: list[dict[str, Any]],
    *,
    target_sections_by_language: Mapping[str, list[str]] | None = None,
    allow_progressive_drafts: bool = False,
) -> list[str]:
    """Return only failures that make a generated result unsafe to persist.

    A targeted repair can safely advance an existing draft even when a
    different required field remains absent. The public status stays ``draft``
    until the whole profile passes the normal publication gate, but discarding
    a grounded, validated field would make independent gaps alternate forever.
    """
    failures: list[str] = []
    languages_seen: set[str] = set()
    targets_by_language = target_sections_by_language or {}
    for result in results:
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else result
        trace = result.get("trace") if isinstance(result.get("trace"), dict) else {}
        language = str(payload.get("language") or "unknown").strip().lower()
        languages_seen.add(language)
        if trace.get("error"):
            failures.append(f"{language}: generator error: {trace['error']}")
        assessment = assess_knowledge_brief(payload, language)
        target_sections = list(targets_by_language.get(language) or [])
        if allow_progressive_drafts and target_sections:
            publication_targets = {
                "brief",
                *assessment.required_fields,
            }
            unavailable_targets = [
                field
                for field in target_sections
                if field in publication_targets
                and field in assessment.fields
                and not assessment.fields[field].available
            ]
            if unavailable_targets:
                failures.append(
                    f"{language}: missing repaired target sections ("
                    + ", ".join(unavailable_targets)
                    + ")"
                )
        else:
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


def _profile_repair_diagnostics_by_language(
    briefs: list[Any],
    disease: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Explain why each localized profile needs work without conflating states.

    A policy/schema upgrade makes an older published profile eligible for
    evidence revalidation, but it does not mean every public chapter vanished.
    Keeping that distinction here gives the catalogue, scheduler, and task
    workbook one truthful source of repair semantics.
    """

    schema = disease["profile_schema"]
    ordered_fields = ["brief", *schema["required_fields"], *schema["optional_fields"]]
    by_language = {
        str(
            getattr(brief, "language", None)
            or (brief.get("language") if isinstance(brief, dict) else "")
        ): brief
        for brief in briefs
    }
    result: dict[str, dict[str, Any]] = {}
    for language in ("en", "zh"):
        brief = by_language.get(language)
        if brief is None:
            result[language] = {
                "sections": list(ordered_fields),
                "reasons": ["missing_language_profile"],
                "missing_required_sections": list(schema["required_fields"]),
                "pipeline_current": False,
            }
            continue

        raw_status = (
            getattr(brief, "status", None)
            if not isinstance(brief, dict)
            else brief.get("status")
        )
        status = str(raw_status or "").strip().lower()
        assessment = assess_knowledge_brief(brief, language, disease=disease)
        content_targets = set(assessment.missing_required_fields)
        if not assessment.fields["brief"].available:
            content_targets.add("brief")
        profile_schema_current = _brief_uses_current_profile_schema(brief, disease)
        pipeline_current = _brief_uses_current_pipeline(brief) and profile_schema_current
        reasons: list[str] = []
        is_draft = bool(status) and status != "published"
        if is_draft:
            reasons.append("draft_profile")
        if content_targets:
            reasons.append("content_gap")
        if not pipeline_current:
            reasons.append(
                "evidence_revalidation"
                if profile_schema_current
                else "profile_contract_migration"
            )

        progressive_repair = bool(_brief_metadata(brief).get("progressive_repair"))
        # Legacy drafts and policy migrations need a whole-profile generation:
        # partial patching would incorrectly claim that unexamined legacy text
        # has passed the current entity-scoped citation policy. Drafts written
        # by progressive repair have already validated prior target fields, so
        # only their remaining gaps should be scheduled next.
        sections = (
            list(ordered_fields)
            if (is_draft and not progressive_repair) or not pipeline_current
            else [field for field in ordered_fields if field in content_targets]
        )
        result[language] = {
            "sections": sections,
            "reasons": reasons,
            "missing_required_sections": list(assessment.missing_required_fields),
            "pipeline_current": pipeline_current,
        }
    return result


def _profile_repair_sections_by_language(
    briefs: list[Any],
    disease: dict[str, Any],
) -> dict[str, list[str]]:
    """Plan localized repairs so a complete translation is never regenerated."""
    diagnostics = _profile_repair_diagnostics_by_language(briefs, disease)
    return {
        language: list(diagnostics[language]["sections"])
        for language in ("en", "zh")
    }


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


def _profile_repair_metadata(
    briefs: list[Any],
    disease: dict[str, Any],
) -> dict[str, Any]:
    """Return a catalogue-safe repair projection shared by list and detail APIs."""

    diagnostics = _profile_repair_diagnostics_by_language(briefs, disease)
    repair_sections_by_language = {
        language: list(diagnostics[language]["sections"])
        for language in ("en", "zh")
    }
    repair_reasons_by_language = {
        language: list(diagnostics[language]["reasons"])
        for language in ("en", "zh")
    }
    ordered_fields = [
        "brief",
        *disease["profile_schema"]["required_fields"],
        *disease["profile_schema"]["optional_fields"],
    ]
    repair_sections = [
        field
        for field in ordered_fields
        if any(field in repair_sections_by_language[language] for language in ("en", "zh"))
    ]
    required_gap_sections = [
        field
        for field in ["brief", *disease["profile_schema"]["required_fields"]]
        if any(
            field in diagnostics[language]["missing_required_sections"]
            or (
                field == "brief"
                and field in diagnostics[language]["sections"]
                and "content_gap" in diagnostics[language]["reasons"]
            )
            for language in ("en", "zh")
        )
    ]
    # A draft is not automatically a content gap. A complete profile can be a
    # draft solely because it needs current-policy citation revalidation. If
    # that routine maintenance is given the same priority as an absent
    # required field, it occupies the source-first backlog and starves pages
    # that cannot yet be published at all.
    has_content_gap = any(
        any(
            reason in {"missing_language_profile", "content_gap"}
            for reason in repair_reasons_by_language[language]
        )
        for language in ("en", "zh")
    )
    has_maintenance_repair = any(
        any(
            reason in {"draft_profile", "evidence_revalidation"}
            for reason in repair_reasons_by_language[language]
        )
        for language in ("en", "zh")
    )
    has_profile_contract_migration = any(
        "profile_contract_migration" in repair_reasons_by_language[language]
        for language in ("en", "zh")
    )
    return {
        "repair_sections": repair_sections,
        "repair_sections_by_language": repair_sections_by_language,
        "repair_reasons_by_language": repair_reasons_by_language,
        "required_gap_sections": required_gap_sections,
        "repair_priority": (
            "urgent"
            if not briefs
            else "high"
            if has_content_gap or has_profile_contract_migration
            else "normal"
            if has_maintenance_repair
            else "none"
        ),
    }


def _active_knowledge_tasks_by_disease(tasks: list[Task]) -> dict[str, Task]:
    """Map active knowledge tasks to their disease IDs for idempotent enqueueing."""
    result: dict[str, Task] = {}
    for task in tasks:
        input_data = dict(getattr(task, "input_data", None) or {})
        candidates: list[str] = []
        disease_ids = input_data.get("disease_ids")
        if isinstance(disease_ids, list):
            candidates.extend(
                str(value).strip().upper()
                for value in disease_ids
                if str(value).strip()
            )
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
    excluded_disease_ids: set[str] | None = None,
    limit: int | None = None,
    priorities: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return catalogue rows that should continue through automatic repair."""
    active_by_disease = active_by_disease or {}
    excluded_disease_ids = {
        str(disease_id).strip().upper()
        for disease_id in (excluded_disease_ids or set())
        if str(disease_id).strip()
    }
    priorities = priorities or {"urgent", "high"}
    selected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    priority_rank = {"urgent": 0, "high": 1, "normal": 2, "none": 3}
    repair_reason_rank = {
        "missing_language_profile": 0,
        "draft_profile": 1,
        "content_gap": 2,
        "evidence_revalidation": 3,
    }
    ordered = sorted(
        catalogue,
        key=lambda item: (
            priority_rank.get(str(item.get("repair_priority") or "none"), 9),
            min(
                (
                    repair_reason_rank.get(str(reason), 9)
                    for reasons in (item.get("repair_reasons_by_language") or {}).values()
                    for reason in (reasons or [])
                ),
                default=9,
            ),
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
        if disease_id in excluded_disease_ids:
            skipped.append(
                {
                    "disease_id": disease_id,
                    "reason": "awaiting_evidence_backoff",
                }
            )
            continue
        exclusion_reason = public_disease_page_exclusion_reason(item)
        if exclusion_reason:
            skipped.append(
                {
                    "disease_id": disease_id,
                    "reason": exclusion_reason,
                }
            )
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


def _bilingual_publication_prerequisites(
    briefs: list[Any],
    disease: dict[str, Any],
) -> dict[str, list[str]]:
    """Return the minimal companion sections needed for bilingual publication.

    A targeted repair may originate from only one locale, while publication is
    deliberately a bilingual contract.  Locking a draft companion profile
    would make the requested repair fail after spending a model call, then
    cause governance to alternate between EN and ZH retries.  This helper
    adds only the companion locale's actual required gaps; it does not turn a
    narrow content repair into a full legacy-profile regeneration.
    """
    schema = disease["profile_schema"]
    ordered_fields = ["brief", *schema["required_fields"], *schema["optional_fields"]]
    by_language = {
        str(
            getattr(brief, "language", None)
            or (brief.get("language") if isinstance(brief, dict) else "")
        ).strip().lower(): brief
        for brief in briefs
    }
    prerequisites: dict[str, list[str]] = {}
    for language in ("en", "zh"):
        brief = by_language.get(language)
        if brief is None:
            prerequisites[language] = list(ordered_fields)
            continue
        assessment = assess_knowledge_brief(brief, language, disease=disease)
        required = set(assessment.missing_required_fields)
        if not assessment.fields["brief"].available:
            required.add("brief")
        prerequisites[language] = [
            field for field in ordered_fields if field in required
        ]
    return prerequisites


def _resolve_repair_sections_by_language(
    briefs: list[Any],
    disease: dict[str, Any],
    *,
    ordered_sections: list[str],
    force: bool,
    target_languages: list[str] | None,
    requested_sections_by_language: dict[str, list[str]] | None,
) -> dict[str, list[str]]:
    """Resolve a narrow repair request without breaking bilingual publication.

    The catalogue supplies a broad candidate, governance may narrow it to a
    field-level retry, and the API may select a locale.  The result keeps that
    intent but cannot leave a known required gap in the companion locale.
    """
    targets = _profile_repair_sections_by_language(briefs, disease)
    if force:
        targets = {language: list(ordered_sections) for language in ("en", "zh")}
    if target_languages is not None:
        allowed_languages = {
            str(language).strip().lower()
            for language in target_languages
            if str(language).strip().lower() in {"en", "zh"}
        }
        if allowed_languages:
            targets = {
                language: fields if language in allowed_languages else []
                for language, fields in targets.items()
            }
    requested = _normalize_requested_sections_by_language(
        requested_sections_by_language,
        ordered_sections=ordered_sections,
    )
    if not requested:
        return targets

    for language, sections in requested.items():
        targets[language] = sections
    companion_prerequisites = _bilingual_publication_prerequisites(briefs, disease)
    for language, sections in companion_prerequisites.items():
        if language in requested or not sections:
            continue
        selected = {*targets.get(language, []), *sections}
        targets[language] = [
            field for field in ordered_sections if field in selected
        ]
    return targets


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


def _use_source_id_citation_markers(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert sequential citations to stable source IDs before section merging."""
    result = dict(payload)
    sources = (
        result.get("source_attribution")
        if isinstance(result.get("source_attribution"), list)
        else []
    )
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

    for field in (
        "brief",
        "definition",
        "clinical_features",
        "epidemiology",
        "transmission",
        "prevention",
        "surveillance_note",
        "risk_groups",
    ):
        value = result.get(field)
        if isinstance(value, str):
            result[field] = re.sub(r"\[(\d+)\]", replace, value)
    return result


def _merge_repair_payload(
    payload: dict[str, Any],
    existing: Any | None,
    target_sections: list[str],
    disease: dict[str, Any],
    *,
    pipeline_version: int = KNOWLEDGE_PIPELINE_VERSION,
) -> dict[str, Any]:
    """Lock already-published fields while replacing only requested gaps."""
    merged = _use_source_id_citation_markers(dict(payload))
    if existing is not None:
        profile_contract_migration = not _brief_uses_current_profile_schema(
            existing,
            disease,
        )
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
        for field in (
            "brief",
            "definition",
            "clinical_features",
            "epidemiology",
            "transmission",
            "prevention",
            "surveillance_note",
            "risk_groups",
        ):
            if field not in target_sections:
                if (
                    profile_contract_migration
                    and field in disease["profile_schema"]["optional_fields"]
                ):
                    merged[field] = None
                else:
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
        "profile_schema_signature": knowledge_profile_schema_signature(disease),
        "target_sections": target_sections,
        "repair_mode": existing is not None,
    }
    merged = normalize_knowledge_citations(
        merged,
        marker_mode="source_id",
        prune_uncited_sources=True,
    )
    merged, assessment = apply_knowledge_quality_gate(merged)
    citation_failures = validate_knowledge_citations(
        merged,
        fields=target_sections,
    )
    merged["metadata"] = {
        **(merged.get("metadata") or {}),
        "pipeline_version": pipeline_version,
        "knowledge_schema_version": KNOWLEDGE_SCHEMA_VERSION,
        "evidence_policy_version": EVIDENCE_POLICY_VERSION,
        "citation_validation": {
            "valid": not citation_failures,
            "validated_fields": list(target_sections),
            "failures": citation_failures,
        },
    }
    if citation_failures:
        merged["status"] = "draft"
        merged["metadata"] = {
            **merged["metadata"],
            "automation_state": "awaiting_evidence",
            "block_reason": "citation_validation_failed",
        }
    else:
        target_set = set(target_sections)
        publication_targets = target_set & {"brief", *assessment.required_fields}
        accepted_targets = all(
            assessment.fields[field].available
            for field in publication_targets
            if field in assessment.fields
        )
        remaining_required = [
            field
            for field in assessment.missing_required_fields
            if field not in target_set
        ]
        if accepted_targets and remaining_required:
            metadata = dict(merged.get("metadata") or {})
            if metadata.get("block_reason") == "missing_required_sections":
                metadata.pop("block_reason", None)
            merged["metadata"] = {
                **metadata,
                "automation_state": "repairing_remaining_sections",
                "progressive_repair": {
                    "completed_sections": [
                        field for field in target_sections if field in publication_targets
                    ],
                    "remaining_required_sections": remaining_required,
                },
            }
        else:
            metadata = dict(merged.get("metadata") or {})
            if str(merged.get("status") or "").lower() == "published":
                # Source or content recovery states are only meaningful while
                # publication is blocked. Keeping one after a successful merge
                # makes a published profile look as if it still needs review.
                metadata.pop("automation_state", None)
                metadata.pop("block_reason", None)
            metadata.pop("progressive_repair", None)
            merged["metadata"] = metadata
    return merged


def _translation_source_usable(
    result: dict[str, Any] | None,
    *,
    target_sections: list[str],
) -> bool:
    if not isinstance(result, dict):
        return False
    trace = result.get("trace") if isinstance(result.get("trace"), dict) else {}
    if trace.get("error"):
        return False
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    citation_repair = (
        metadata.get("citation_repair")
        if isinstance(metadata.get("citation_repair"), dict)
        else {}
    )
    final_failures = citation_repair.get("final_failures")
    if isinstance(final_failures, list) and final_failures:
        return False
    trace_failures = trace.get("citation_failures")
    if isinstance(trace_failures, list) and trace_failures:
        return False
    language = "zh" if payload.get("language") == "zh" else "en"
    assessment = assess_knowledge_brief(payload, language)
    if any(
        not assessment.fields[field].available
        for field in target_sections
        if field in assessment.fields
    ):
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
        "status": "draft",
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
            "automation_state": "waiting_for_model_capacity",
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


def _normalize_generator_mode(mode: str | None) -> str:
    value = (mode or "ai").strip().lower()
    if value not in {"ai", "auto"}:
        return "ai"
    return value


def _total_tokens_from_usage(token_usage: Any) -> Optional[int]:
    if not isinstance(token_usage, dict):
        return None
    total = token_usage.get("total")
    if isinstance(total, int):
        return total
    if isinstance(total, float):
        return int(total)
    return None
