"""Deterministic publication decisions; no database, locks or task dispatch."""
from __future__ import annotations
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from src.literature.summary_contracts import ALLOWED_EVIDENCE, SUMMARY_FIELDS, source_fingerprint

POLICY_VERSION = "research-radar-autopilot.v3"

AUTOPILOT_ACTOR = "research-radar-autopilot"

@dataclass(frozen=True, slots=True)
class AutomationDecision:
    action: Literal["publish", "exclude", "confirm", "reject", "hold", "defer", "archive"]
    reasons: tuple[str, ...]

_CORRECTION_TITLE = re.compile(r"^\s*(?:correction|corrigendum|erratum)\s*(?::|to\b)", re.IGNORECASE)

_POLICY_OVERRIDE_FIELDS = frozenset({"autopilot_article_min_score"})

_SCORED_REASON = re.compile(r"^(?:discovery score|summary quality)\s+[0-9.]+")

def _effective_config(config: Any, overrides: Mapping[str, float] | None) -> Any:
    """Return a validated, in-memory policy variant without mutating settings.

    Runtime calibration is deliberately narrow: callers may project or apply a
    different publication threshold, but cannot silently weaken integrity,
    provenance, or summary-quality gates.
    """

    if not overrides:
        return config
    unknown = sorted(set(overrides) - _POLICY_OVERRIDE_FIELDS)
    if unknown:
        raise ValueError("unsupported autopilot policy overrides: " + ", ".join(unknown))
    values = {name: float(value) for name, value in overrides.items()}
    threshold = values.get("autopilot_article_min_score")
    if threshold is not None and not 0.0 <= threshold <= 1.0:
        raise ValueError("autopilot_article_min_score must be between zero and one")
    if threshold is not None and threshold < float(config.autopilot_article_exclude_below_score):
        raise ValueError("article publication threshold cannot be below the exclusion threshold")
    copier = getattr(config, "model_copy", None)
    return copier(update=values) if copier else config.copy(update=values)

def _diagnostic_reason(decision: AutomationDecision) -> str:
    """Collapse numeric score variants into stable, aggregate audit buckets."""

    reason = decision.reasons[0] if decision.reasons else "no reason recorded"
    if _SCORED_REASON.match(reason):
        if reason.startswith("discovery score") and "below" in reason:
            if "exclusion gate" in reason:
                reason = "discovery score is below the automatic exclusion gate"
            else:
                reason = "discovery score is below the automatic publication gate"
        elif reason.startswith("summary quality") and "below" in reason:
            reason = "summary quality is below the automatic gate"
        elif reason.startswith("summary quality"):
            reason = "summary quality passed the automatic gate"
    return f"{decision.action}: {reason}"

def _persisted_autopilot_decision(metadata: Any) -> str | None:
    """Return only exact, auditable non-actionable decision markers."""

    if not isinstance(metadata, Mapping):
        return None
    automation = metadata.get("autopilot")
    if not isinstance(automation, Mapping):
        return None
    decision = automation.get("decision")
    return decision if decision in {"defer", "archive"} else None

def _is_autopilot_managed(metadata: Any) -> bool:
    if not isinstance(metadata, Mapping):
        return False
    automation = metadata.get("autopilot")
    if not isinstance(automation, Mapping):
        return False
    return (
        automation.get("policy_version") in {POLICY_VERSION, "research-radar-autopilot.v1", "research-radar-autopilot.v2"}
        and automation.get("actor") == AUTOPILOT_ACTOR
    )

def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

def _article_hold_reasons(article: Any, *, now: datetime) -> list[str]:
    reasons: list[str] = []
    if str(article.integrity_status) != "current":
        reasons.append(f"integrity status is {article.integrity_status}")
    published_at = _aware(article.published_at)
    if published_at is None:
        reasons.append("publication date is missing")
    elif published_at > now:
        reasons.append("publication date is in the future")
    if str(article.peer_review_status) != "peer_reviewed":
        reasons.append("record is not peer reviewed")
    if not (article.doi or article.pmid or article.pmcid):
        reasons.append("DOI/PMID/PMCID is missing")
    if len(str(article.title or "").strip()) < 20:
        reasons.append("title is incomplete")
    if not str(article.journal or "").strip():
        reasons.append("journal is missing")
    if not (article.authors or []):
        reasons.append("authors are missing")
    return reasons

def _research_domain(article: Any) -> str:
    evidence = ((article.metadata_ or {}).get("classification_evidence") or {}).get("research_domain") or {}
    return str(evidence.get("value") or "not_determined")

def _has_explicit_correction_parent(article: Any) -> bool:
    """Recognize a correction notice only when Crossref names its parent.

    A normal article can carry a ``relation.correction`` edge pointing to a
    later notice, so integrity status or a relation key alone is insufficient.
    The correction/corrigendum/erratum title and a non-self DOI in ``update-to``
    are both required before the notice is excluded as an independent study.
    """
    if not _CORRECTION_TITLE.search(str(article.title or "")):
        return False
    source_payload = getattr(article, "_autopilot_source_payload", None)
    if source_payload is None:
        source_payload = getattr(article, "source_payload", None)
    payload = source_payload if isinstance(source_payload, dict) else {}
    updates = payload.get("update-to")
    if not isinstance(updates, list):
        return False
    own_doi = str(article.doi or "").strip().lower()
    for item in updates:
        if not isinstance(item, dict):
            continue
        update_type = str(item.get("type") or "").strip().lower()
        parent_doi = str(item.get("DOI") or item.get("doi") or "").strip().lower()
        if ("correct" in update_type or "errat" in update_type) and parent_doi and parent_doi != own_doi:
            return True
    return False

def decide_evidence_link(link: Any, article: Any, config: Any, *, now: datetime) -> AutomationDecision:
    """Return an auditable relationship decision without mutating state."""
    if link.status in {"confirmed", "rejected"}:
        return AutomationDecision("hold", ("existing final decision is preserved",))
    if str(article.integrity_status) in {"retracted", "expression_of_concern"}:
        return AutomationDecision("reject", (f"integrity status is {article.integrity_status}",))
    if link.relation_level == "candidate" and config.autopilot_auto_reject_weak_links:
        return AutomationDecision("reject", ("disease confidence is below the contextual evidence gate",))
    hold_reasons = _article_hold_reasons(article, now=now)
    if hold_reasons:
        incomplete_markers = {
            "DOI/PMID/PMCID is missing",
            "title is incomplete",
            "journal is missing",
            "authors are missing",
        }
        deterministic_rejection = (
            config.autopilot_auto_exclude_preprints and "record is not peer reviewed" in hold_reasons
        ) or (
            config.autopilot_auto_exclude_incomplete and bool(incomplete_markers.intersection(hold_reasons))
        )
        if deterministic_rejection:
            return AutomationDecision("reject", tuple(hold_reasons))
        return AutomationDecision("hold", tuple(hold_reasons))
    confidence = float(link.confidence or 0.0)
    if (
        link.relation_level == "exact_disease_geography"
        and confidence >= config.autopilot_exact_relation_min_confidence
    ):
        return AutomationDecision(
            "confirm",
            (
                f"exact disease-and-geography confidence {confidence:.2f}",
                "bibliographic and integrity gates passed",
            ),
        )
    if (
        link.relation_level == "disease_context"
        and confidence >= config.autopilot_context_relation_min_confidence
    ):
        return AutomationDecision(
            "confirm",
            (
                f"disease-context confidence {confidence:.2f}",
                "bibliographic and integrity gates passed",
            ),
        )
    return AutomationDecision("hold", ("confidence is inside the exception-review band",))

def decide_article(
    article: Any,
    config: Any,
    *,
    max_disease_confidence: float,
    confirmed_relation_levels: set[str],
    now: datetime,
) -> AutomationDecision:
    """Return an automatic publication decision for an unpublished article."""
    metadata = dict(article.metadata_ or {})
    if metadata.get("editorial_locked"):
        return AutomationDecision("hold", ("explicit editorial decision is locked",))
    if str(article.integrity_status) in {"retracted", "expression_of_concern"}:
        return AutomationDecision("exclude", (f"integrity status is {article.integrity_status}",))
    if _has_explicit_correction_parent(article):
        return AutomationDecision(
            "exclude",
            ("correction/corrigendum record has an explicit parent DOI",),
        )
    research_domain = _research_domain(article)
    if research_domain == "plant_only":
        return AutomationDecision(
            "hold",
            (f"research domain is {research_domain} and requires editorial review",),
        )
    hold_reasons = _article_hold_reasons(article, now=now)
    if hold_reasons:
        # Missing bibliography or peer-review metadata prevents automatic
        # release, but does not prove the study is outside the disease scope.
        if hold_reasons == ["publication date is in the future"]:
            return AutomationDecision(
                "defer",
                ("publication date is in the future; scheduled for automatic re-evaluation",),
            )
        return AutomationDecision("hold", tuple(hold_reasons))
    # Classifier matches and even confirmed signal links can be based on the
    # title alone. They find candidates; neither establishes article aboutness.
    # Contextual relevance review is a separate decision with source evidence.
    return AutomationDecision("hold", ("disease relevance requires contextual review",))

def _is_parent_archived_summary(summary: Any) -> bool:
    """Only a parent-caused archive may be automatically restored.

    An AI archive can retain a previous parent-related gate, so its explicit
    governance decision takes precedence over that historical marker.
    """
    metadata = summary.generation_metadata or {}
    governance = metadata.get("ai_content_governance") or {}
    return (
        summary.status == "archived"
        and metadata.get("publication_gate") in {"parent-article-excluded", "awaiting-article-decision"}
        and governance.get("decision") not in {"archive", "reject"}
    )

def decide_summary(
    summary: Any,
    article: Any,
    config: Any,
    *,
    expected_canonical_summary_fingerprint: str | None = None,
) -> AutomationDecision:
    """Validate a model summary against deterministic publication gates."""
    automation = dict((summary.generation_metadata or {}).get("autopilot") or {})
    if summary.status == "published" and (
        not automation
        or summary.generated_by == "control-plane-editor"
        or (summary.generation_metadata or {}).get("editorial_reviewed_at")
    ):
        return AutomationDecision("hold", ("summary is already published",))
    if article.publication_status == "excluded":
        # An excluded parent already keeps this summary off the public site; do
        # not archive it permanently, because the article can be reopened. Use
        # defer so the reconciler leaves a review-status summary alone and moves
        # any already-archived summary back to review when the article is later
        # reopened.  The old archive path is restored by
        # restore_parent_excluded_summaries for rows archived before this change.
        return AutomationDecision("defer", ("article decision is not final for public release",))
    if article.publication_status != "published":
        return AutomationDecision("defer", ("article decision is not final for public release",))
    if summary.status == "archived" and not _is_parent_archived_summary(summary):
        return AutomationDecision("hold", ("summary was archived independently of the parent article",))
    if summary.generated_by != "literature-evidence-agent":
        return AutomationDecision("hold", ("summary was not produced by the evidence agent",))
    quality = float(summary.quality_score or 0.0)
    if quality < config.autopilot_summary_min_quality:
        return AutomationDecision("hold", (f"summary quality {quality:.3f} is below the automatic gate",))
    metadata = dict(summary.generation_metadata or {})
    if str(metadata.get("source_fingerprint") or "") != source_fingerprint(article):
        return AutomationDecision("hold", ("summary source fingerprint is stale",))
    if getattr(summary, "language", None) == "zh" and int(metadata.get("protocol_version") or 0) >= 2:
        alignment = metadata.get("bilingual_alignment")
        if not isinstance(alignment, dict) or (
            alignment.get("protocol_version") != "canonical-en-translation.v1"
            or alignment.get("canonical_language") != "en"
            or alignment.get("canonical_summary_fingerprint") != expected_canonical_summary_fingerprint
        ):
            return AutomationDecision("hold", ("bilingual canonical alignment evidence is missing or stale",))
    if getattr(summary, "language", None) == "fr" and int(metadata.get("protocol_version") or 0) >= 2:
        alignment = metadata.get("translation_alignment")
        if not isinstance(alignment, dict) or (
            alignment.get("protocol_version") != "canonical-en-translation.v2"
            or alignment.get("canonical_language") != "en"
            or alignment.get("target_language") != "fr"
            or alignment.get("canonical_summary_fingerprint") != expected_canonical_summary_fingerprint
        ):
            return AutomationDecision("hold", ("French canonical alignment evidence is missing or stale",))
    if "verbatim-overlap" in str(summary.review_notes or "").lower():
        return AutomationDecision("hold", ("verbatim overlap was detected during generation",))
    required_fields = {
        "research_question",
        "study_design",
        "main_findings",
        "public_health_relevance",
        "limitations",
        "gids_interpretation",
    }
    missing = sorted(field for field in required_fields if not str(getattr(summary, field, None) or "").strip())
    if missing:
        return AutomationDecision("hold", (f"required evidence fields are missing: {', '.join(missing)}",))
    evidence_map = dict(summary.evidence_map or {})
    for field in SUMMARY_FIELDS:
        text = str(getattr(summary, field, None) or "").strip()
        if not text:
            continue
        evidence = evidence_map.get(field)
        if not isinstance(evidence, dict):
            return AutomationDecision("hold", (f"evidence trace is missing for {field}",))
        sources = set(evidence.get("sources") or [])
        if not sources or not sources.issubset(ALLOWED_EVIDENCE):
            return AutomationDecision("hold", (f"evidence trace is invalid for {field}",))
        if float(evidence.get("confidence") or 0.0) < 0.70:
            return AutomationDecision("hold", (f"field confidence is too low for {field}",))
    return AutomationDecision(
        "publish",
        (
            f"summary quality {quality:.3f}",
            "source fingerprint, evidence traces, and required fields passed",
        ),
    )

def _published_revalidation_status(article: Any, decision: AutomationDecision, *, now: datetime) -> str:
    """Return a replacement status only for hard public-boundary failures."""
    research_domain = _research_domain(article)
    if research_domain == "plant_only":
        return "excluded"
    if str(article.integrity_status) in {"retracted", "expression_of_concern"}:
        return "excluded"
    if _has_explicit_correction_parent(article):
        return "excluded"
    published_at = _aware(article.published_at)
    if published_at is not None and published_at > now:
        return "review"
    if decision.action == "publish":
        return "published"
    return "published"

def _audit_payload(decision: AutomationDecision, *, at: datetime, config: Any) -> dict[str, Any]:
    return {
        "policy_version": POLICY_VERSION,
        "decision": decision.action,
        "decided_at": at.isoformat(),
        "actor": AUTOPILOT_ACTOR,
        "reasons": list(decision.reasons),
        "thresholds": {
            "article_min_score": config.autopilot_article_min_score,
            "article_exclude_below_score": config.autopilot_article_exclude_below_score,
            "disease_min_confidence": config.autopilot_disease_min_confidence,
            "exact_relation_min_confidence": config.autopilot_exact_relation_min_confidence,
            "context_relation_min_confidence": config.autopilot_context_relation_min_confidence,
            "summary_min_quality": config.autopilot_summary_min_quality,
        },
    }

_PARENT_EXCLUDED_GATE = "parent-article-excluded"
