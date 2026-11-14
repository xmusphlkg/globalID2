"""Deterministic knowledge repair planning, independent of model and DB work."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from src.domain import Task, TaskPriority, TaskStatus, TaskType


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
    return mapping.get(str(repair_priority or "").strip().lower(), TaskPriority.HIGH)



def _normalize_requested_sections_by_language(
    value: Any,
    *,
    ordered_sections: list[str],
) -> dict[str, list[str]]:
    """Validate a persisted governance repair plan before it reaches a prompt."""
    if not isinstance(value, dict):
        return {}
    allowed = set(ordered_sections)
    normalized: dict[str, list[str]] = {}
    for language in ("en", "zh"):
        raw_sections = value.get(language)
        if not isinstance(raw_sections, list):
            continue
        selected = {
            str(section).strip()
            for section in raw_sections
            if str(section).strip() in allowed
        }
        if selected:
            normalized[language] = [
                section for section in ordered_sections if section in selected
            ]
    return normalized



def _normalize_repair_reasons_by_language(value: Any) -> dict[str, list[str]]:
    """Keep deterministic gate feedback concise before it enters a model prompt."""
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, list[str]] = {}
    for language in ("en", "zh"):
        raw_reasons = value.get(language)
        if isinstance(raw_reasons, str):
            raw_reasons = [raw_reasons]
        if not isinstance(raw_reasons, list):
            continue
        reasons: list[str] = []
        seen: set[str] = set()
        for raw_reason in raw_reasons:
            reason = " ".join(str(raw_reason or "").split()).strip()
            if not reason:
                continue
            reason = reason[:600]
            key = reason.casefold()
            if key in seen:
                continue
            seen.add(key)
            reasons.append(reason)
            if len(reasons) >= 3:
                break
        if reasons:
            normalized[language] = reasons
    return normalized



def _text(value: Any, *, limit: int = 5000) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]



_RECOVERABLE_KNOWLEDGE_MARKERS = (
    "connection error",
    "timeout",
    "temporarily unavailable",
    "no candidate model available",
    "agent completion failed",
    "all models failed",
    "rate limit",
    "substantive brief is required",
    "profile was not generated",
    "generation did not pass the publication gate",
)



_KNOWLEDGE_REPAIR_FIELDS = (
    "brief",
    "definition",
    "clinical_features",
    "epidemiology",
    "transmission",
    "prevention",
    "surveillance_note",
    "risk_groups",
)



_TRANSIENT_MODEL_MARKERS = (
    "connection error",
    "timeout",
    "temporarily unavailable",
    "no candidate model available",
    "agent completion failed",
    "all models failed",
    "rate limit",
    "quota",
    "profile was not generated",
    # A truncated or malformed structured response is a transport/output
    # failure. Retrying only its current model scope is safer than treating
    # fallback scaffolding as a real content omission.
    "generator error",
    "unterminated string",
    "jsondecodeerror",
    "invalid json",
    "json decode",
)



_EVIDENCE_BLOCK_MARKERS = (
    "missing traceable sources",
    "citation validation failed",
    "source enrichment exhausted",
    "insufficient evidence",
)



@dataclass(frozen=True, slots=True)
class KnowledgeRepairPlan:
    """A bounded next action derived from a publication-gate failure."""

    category: str
    sections_by_language: Mapping[str, tuple[str, ...]]
    languages: tuple[str, ...]
    fingerprint: str
    reason: str



def _ordered_sections(values: Iterable[str]) -> tuple[str, ...]:
    selected = {str(value).strip() for value in values if str(value).strip()}
    return tuple(field for field in _KNOWLEDGE_REPAIR_FIELDS if field in selected)



def _language_sections_from_failure(error: str) -> dict[str, tuple[str, ...]]:
    """Parse deterministic publication-gate sections without trusting prose."""
    selected: dict[str, set[str]] = {"en": set(), "zh": set()}
    for match in re.finditer(
        # The publication gate emits ``missing repaired target sections`` for
        # a narrow repair and ``missing required sections`` for a full
        # profile.  Both are the same deterministic contract: the named
        # localized fields need another grounded model pass.  Keep them in one
        # parser so a wording change can never silently turn a content repair
        # into an unnecessary source refresh.
        r"\b(?P<language>en|zh):[^|]*?(?:missing repaired target sections|missing required sections|required sections incomplete)\s*\((?P<sections>[^)]*)\)",
        error,
        flags=re.IGNORECASE,
    ):
        language = match.group("language").lower()
        values = re.split(r"\s*,\s*", match.group("sections"))
        selected[language].update(value.strip() for value in values)
    for match in re.finditer(
        r"\b(?P<language>en|zh):[^|]*?substantive brief is required",
        error,
        flags=re.IGNORECASE,
    ):
        selected[match.group("language").lower()].add("brief")
    return {
        language: _ordered_sections(fields)
        for language, fields in selected.items()
        if _ordered_sections(fields)
    }



def _model_failure_languages(error: str) -> tuple[str, ...]:
    languages: list[str] = []
    for match in re.finditer(
        r"\b(?P<language>en|zh):[^|]*?(?:generator error|agent completion failed|timeout|connection error|no candidate model|profile was not generated)",
        error,
        flags=re.IGNORECASE,
    ):
        language = match.group("language").lower()
        if language not in languages:
            languages.append(language)
    return tuple(languages)



def _publication_status_languages(error: str) -> tuple[str, ...]:
    """Extract locales whose payload was rejected without field diagnostics."""
    languages: list[str] = []
    for match in re.finditer(
        r"\b(?P<language>en|zh):[^|]*?status is not published",
        error,
        flags=re.IGNORECASE,
    ):
        language = match.group("language").lower()
        if language not in languages:
            languages.append(language)
    return tuple(languages)



def plan_failed_knowledge_repair(error: str | None) -> KnowledgeRepairPlan:
    """Classify a failed repair into one minimal, deterministic next action.

    The error strings are emitted by the publication gate, not by a model.  We
    therefore only extract known schema field names and use a stable digest to
    stop an identical content repair from looping indefinitely.
    """
    message = _text(error, limit=5000)
    normalized = message.lower()
    sections = _language_sections_from_failure(message)
    model_languages = _model_failure_languages(message)
    publication_status_languages = _publication_status_languages(message)
    has_model_failure = any(marker in normalized for marker in _TRANSIENT_MODEL_MARKERS)
    has_evidence_block = any(marker in normalized for marker in _EVIDENCE_BLOCK_MARKERS)

    if has_model_failure:
        category = "model_transient"
        languages = model_languages
        reason = "A runtime model route failed before a valid localized repair was produced."
        # Missing fields from an empty fallback scaffold are not a content plan.
        sections = {}
    elif sections:
        category = "content_gap"
        languages = tuple(language for language in ("en", "zh") if language in sections)
        reason = "The publication gate identified explicit localized missing sections."
    elif publication_status_languages:
        category = "publication_status"
        languages = publication_status_languages
        reason = (
            "A localized legacy draft was rejected without field-level diagnostics; "
            "recompute its full profile scope from the current evidence packet."
        )
    elif has_evidence_block:
        category = "evidence_block"
        languages = ()
        reason = "The evidence or citation gate blocked publication; regenerating prose would not fix it."
    else:
        category = "nonrecoverable"
        languages = ()
        reason = "The failure has no deterministic automated repair plan."

    canonical = json.dumps(
        {
            "category": category,
            # Content gaps are repaired from the already certified evidence
            # packet. Source-first work is reserved for an evidence block.
            # Version the strategy so an old terminal source-refresh loop can
            # receive one materially different, bounded model repair.
            "strategy": "targeted_model_v4" if category == "content_gap" else category,
            "languages": list(languages),
            "sections_by_language": {key: list(value) for key, value in sorted(sections.items())},
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return KnowledgeRepairPlan(
        category=category,
        sections_by_language=sections,
        languages=languages,
        fingerprint=hashlib.sha256(canonical.encode("ascii")).hexdigest()[:16],
        reason=reason,
    )



def _same_recovery_plan_was_attempted(task: Task | Any, plan: KnowledgeRepairPlan) -> bool:
    history = (getattr(task, "metadata_", None) or {}).get("ai_content_governance_retries")
    if not isinstance(history, list):
        return False
    input_data = dict(getattr(task, "input_data", None) or {})
    recovery_epoch = input_data.get("model_recovery_epoch")
    return any(
        isinstance(item, Mapping)
        and item.get("category") == plan.category
        and item.get("failure_fingerprint") == plan.fingerprint
        and item.get("model_recovery_epoch") == recovery_epoch
        for item in history
    )



def _all_current_briefs_are_published(statuses: Iterable[Any]) -> bool:
    normalized = [str(status or "").strip().lower() for status in statuses]
    return bool(normalized) and all(status == "published" for status in normalized)



def is_recoverable_knowledge_failure(
    task: Task | Any,
    error: str | None = None,
    *,
    allow_running: bool = False,
) -> bool:
    if getattr(task, "task_type", None) != TaskType.UPDATE_DISEASE_KNOWLEDGE:
        return False
    status = getattr(task, "status", None)
    if status != TaskStatus.FAILED and not (
        allow_running and status == TaskStatus.RUNNING
    ):
        return False
    input_data = dict(getattr(task, "input_data", None) or {})
    tags = set(getattr(task, "tags", None) or [])
    if not input_data.get("targeted_repair") and "auto_repair" not in tags:
        return False
    plan = plan_failed_knowledge_repair(
        error if error is not None else getattr(task, "last_error", "") or ""
    )
    if plan.category == "content_gap":
        # One minimal repair can correct an omitted structured field. Repeating
        # that exact plan has no new information and must remain terminal. It
        # remains available once even after legacy broad retries are exhausted:
        # the execution strategy and token budget are materially different.
        return not _same_recovery_plan_was_attempted(task, plan)
    if plan.category == "publication_status":
        # A draft with complete-looking fields can still carry invalid legacy
        # citations or an obsolete pipeline marker. Recompute it once using
        # the current profile rather than retaining an opaque terminal state.
        return not _same_recovery_plan_was_attempted(task, plan)
    if plan.category == "model_transient":
        # A model-center admission/circuit deployment materially changes the
        # execution environment, so an inherited terminal retry budget must
        # not strand recoverable work. Allow one durable recovery attempt,
        # then leave the task terminal rather than loop on route instability.
        return not _same_recovery_plan_was_attempted(task, plan)
    if plan.category == "evidence_block":
        # Evidence failures are repaired through the independent source-first
        # workflow, never by asking a model to fabricate a fuller brief.
        return not _same_recovery_plan_was_attempted(task, plan)
    if int(getattr(task, "retry_count", 0) or 0) >= int(getattr(task, "max_retries", 0) or 0):
        return False
    return False
