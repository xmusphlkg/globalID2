"""Versioned Research Radar autopilot with exception-only editorial review.

Automation is deliberately deterministic.  It can publish only records that
pass bibliographic, integrity, provenance, and confidence gates; all decisions
are written into existing audit metadata and status-event tables.  Explicit
editorial decisions always win and are never rewritten by this service.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Literal, Mapping
import uuid

from sqlalchemy import func, select

from src.core.config import get_config
from src.core.database import get_db
from src.domain import (
    LiteratureArticle,
    LiteratureDiseaseLink,
    LiteratureEvidenceGap,
    LiteratureIngestRun,
    LiteratureSignalArticleLink,
    LiteratureStatusEvent,
    LiteratureSummary,
)
from src.literature.enrichment import (
    ALLOWED_EVIDENCE,
    SUMMARY_FIELDS,
    canonical_summary_fingerprint,
    source_fingerprint,
)


POLICY_VERSION = "research-radar-autopilot.v1"
AUTOPILOT_ACTOR = "research-radar-autopilot"
_RUN_LOCK = asyncio.Lock()


@dataclass(frozen=True, slots=True)
class AutomationDecision:
    action: Literal["publish", "exclude", "confirm", "reject", "hold", "defer", "archive"]
    reasons: tuple[str, ...]


_CORRECTION_TITLE = re.compile(r"^\s*(?:correction|corrigendum|erratum)\s*(?::|to\b)", re.IGNORECASE)
_STRICT_ANIMAL_ONLY_MAX_SCORE = 0.20
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
            reason = "discovery score is below the automatic exclusion gate"
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


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


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
    score = float(article.discovery_score or 0.0)
    if (
        _research_domain(article) == "animal_only"
        and max_disease_confidence <= 0.0
        and score <= min(float(config.autopilot_article_exclude_below_score), _STRICT_ANIMAL_ONLY_MAX_SCORE)
    ):
        return AutomationDecision(
            "exclude",
            ("strict animal-only record has no disease link and extremely low discovery score",),
        )
    hold_reasons = _article_hold_reasons(article, now=now)
    if hold_reasons:
        incomplete_markers = {
            "DOI/PMID/PMCID is missing",
            "title is incomplete",
            "journal is missing",
            "authors are missing",
        }
        deterministic_exclusion = (
            config.autopilot_auto_exclude_preprints and "record is not peer reviewed" in hold_reasons
        ) or (
            config.autopilot_auto_exclude_incomplete and bool(incomplete_markers.intersection(hold_reasons))
        )
        if deterministic_exclusion:
            return AutomationDecision("exclude", tuple(hold_reasons))
        if hold_reasons == ["publication date is in the future"]:
            return AutomationDecision(
                "defer",
                ("publication date is in the future; scheduled for automatic re-evaluation",),
            )
        return AutomationDecision("hold", tuple(hold_reasons))
    exact = "exact_disease_geography" in confirmed_relation_levels
    strong_context = "disease_context" in confirmed_relation_levels
    catalogue_gate = (
        score >= config.autopilot_article_min_score
        and max_disease_confidence >= config.autopilot_disease_min_confidence
    )
    if exact or strong_context or catalogue_gate:
        reasons = ["bibliographic and integrity gates passed"]
        if exact:
            reasons.append("high-confidence exact signal relationship")
        elif strong_context:
            reasons.append("high-confidence disease-context relationship")
        else:
            reasons.extend((
                f"discovery score {score:.3f}",
                f"disease confidence {max_disease_confidence:.2f}",
            ))
        return AutomationDecision("publish", tuple(reasons))
    if score < config.autopilot_article_exclude_below_score:
        return AutomationDecision(
            "exclude",
            (f"discovery score {score:.3f} is below the automatic exclusion gate",),
        )
    return AutomationDecision("hold", ("article relevance is inside the exception-review band",))


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
    if article.publication_status == "excluded" and summary.status in {"review", "archived"}:
        return AutomationDecision("archive", ("parent article is excluded",))
    if article.publication_status != "published":
        return AutomationDecision("defer", ("article decision is not final for public release",))
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


class LiteratureAutomationService:
    async def reconcile(
        self,
        *,
        dry_run: bool = False,
        export: bool | None = None,
        policy_overrides: Mapping[str, float] | None = None,
        diagnostics: bool = False,
    ) -> dict[str, Any]:
        config = _effective_config(get_config().literature, policy_overrides)
        if not config.autopilot_enabled and not dry_run:
            return {"enabled": False, "dry_run": False, "policy_version": POLICY_VERSION, "changed": 0}
        async with _RUN_LOCK:
            result = await self._reconcile_database(
                config, dry_run=dry_run, diagnostics=diagnostics
            )
            should_export = config.autopilot_export_on_change if export is None else export
            if not dry_run and should_export and result["changed"]:
                from src.services.literature_publication_service import export_public_research_artifacts

                result["public_export"] = await export_public_research_artifacts()
            return result

    async def _reconcile_database(
        self, config: Any, *, dry_run: bool, diagnostics: bool = False
    ) -> dict[str, Any]:
        at = _now()
        counts = {
            "articles_published": 0,
            "articles_excluded": 0,
            "articles_deferred": 0,
            "article_exceptions": 0,
            "links_confirmed": 0,
            "links_rejected": 0,
            "link_exceptions": 0,
            "summaries_published": 0,
            "summaries_archived": 0,
            "summaries_deferred": 0,
            "summaries_restored": 0,
            "summaries_reopened": 0,
            "summary_exceptions": 0,
            "gaps_covered": 0,
            "gaps_reopened": 0,
        }
        published_article_ids: list[str] = []
        decision_reasons: dict[str, Counter[str]] = {
            "articles": Counter(),
            "links": Counter(),
            "summaries": Counter(),
        }

        def record_reason(entity: str, decision: AutomationDecision) -> None:
            if diagnostics:
                decision_reasons[entity][_diagnostic_reason(decision)] += 1

        async with get_db() as db:
            links_with_articles = (
                await db.execute(
                    select(LiteratureSignalArticleLink, LiteratureArticle).join(
                        LiteratureArticle,
                        LiteratureArticle.article_id == LiteratureSignalArticleLink.article_id,
                    )
                )
            ).all()
            effective_link_status: dict[int, str] = {}
            links_by_article: dict[str, list[tuple[LiteratureSignalArticleLink, str]]] = {}
            links_by_gap: dict[str, list[tuple[LiteratureSignalArticleLink, LiteratureArticle]]] = {}
            article_by_id: dict[str, LiteratureArticle] = {}
            for link, article in links_with_articles:
                article_by_id[article.article_id] = article
                effective = link.status
                if link.status in {"review", "deprioritized"}:
                    decision = decide_evidence_link(link, article, config, now=at)
                    record_reason("links", decision)
                    if decision.action == "confirm":
                        effective = "confirmed"
                        counts["links_confirmed"] += 1
                    elif decision.action == "reject":
                        effective = "rejected"
                        counts["links_rejected"] += 1
                    elif link.status == "review":
                        counts["link_exceptions"] += 1
                    if not dry_run and effective != link.status:
                        link.status = effective
                        link.reviewed_at = at
                        link.reviewed_by = AUTOPILOT_ACTOR
                        link.review_note = "; ".join(decision.reasons)
                        link.metadata_ = {**(link.metadata_ or {}), "autopilot": _audit_payload(decision, at=at, config=config)}
                effective_link_status[link.id] = effective
                links_by_article.setdefault(article.article_id, []).append((link, effective))
                if link.gap_id:
                    links_by_gap.setdefault(link.gap_id, []).append((link, article))

            disease_confidence = {
                article_id: float(value or 0.0)
                for article_id, value in (
                    await db.execute(
                        select(
                            LiteratureDiseaseLink.article_id,
                            func.max(LiteratureDiseaseLink.confidence),
                        )
                        .join(
                            LiteratureArticle,
                            LiteratureArticle.article_id == LiteratureDiseaseLink.article_id,
                        )
                        .where(LiteratureArticle.publication_status == "review")
                        .group_by(LiteratureDiseaseLink.article_id)
                    )
                ).all()
            }
            # Only review articles are eligible for an article decision. Loading
            # every article also materializes abstracts and multi-provider JSON;
            # at production scale that exceeded 9 GiB even for a dry run.
            articles = (
                await db.execute(
                    select(LiteratureArticle).where(
                        LiteratureArticle.publication_status == "review"
                    )
                )
            ).scalars().all()
            article_by_id.update({article.article_id: article for article in articles})
            effective_article_status = {
                article_id: article.publication_status
                for article_id, article in article_by_id.items()
            }
            for article in articles:
                if article.publication_status != "review":
                    continue
                confirmed_levels = {
                    link.relation_level
                    for link, effective in links_by_article.get(article.article_id, [])
                    if effective == "confirmed"
                }
                decision = decide_article(
                    article,
                    config,
                    max_disease_confidence=disease_confidence.get(article.article_id, 0.0),
                    confirmed_relation_levels=confirmed_levels,
                    now=at,
                )
                record_reason("articles", decision)
                if decision.action == "publish":
                    desired = "published"
                    counts["articles_published"] += 1
                    published_article_ids.append(article.article_id)
                elif decision.action == "exclude":
                    desired = "excluded"
                    counts["articles_excluded"] += 1
                elif decision.action == "defer":
                    desired = "review"
                    counts["articles_deferred"] += 1
                else:
                    desired = "review"
                    counts["article_exceptions"] += 1
                effective_article_status[article.article_id] = desired
                if dry_run:
                    continue
                # Refresh the final evaluated decision even when status remains
                # review. This replaces stale exclude metadata after a newer
                # classification legitimately reopens the record.
                if not (article.metadata_ or {}).get("editorial_locked"):
                    article.metadata_ = {
                        **(article.metadata_ or {}),
                        "autopilot": _audit_payload(decision, at=at, config=config),
                    }
                if desired == article.publication_status:
                    continue
                previous = article.publication_status
                article.publication_status = desired
                db.add(LiteratureStatusEvent(
                    article_id=article.article_id,
                    event_type="publication_status_changed",
                    previous_status=previous,
                    current_status=desired,
                    source=AUTOPILOT_ACTOR,
                    effective_at=at,
                    metadata_={"policy_version": POLICY_VERSION, "reasons": list(decision.reasons)},
                ))

            summaries_with_articles = (
                await db.execute(
                    select(LiteratureSummary, LiteratureArticle)
                    .join(
                        LiteratureArticle,
                        LiteratureArticle.article_id == LiteratureSummary.article_id,
                    )
                    .where(
                        LiteratureSummary.status.in_(("review", "published", "archived"))
                    )
                )
            ).all()
            english_canonical_fingerprints = {
                summary.article_id: canonical_summary_fingerprint({
                    field: getattr(summary, field) for field in SUMMARY_FIELDS
                })
                for summary, _article in summaries_with_articles
                if summary.language == "en"
            }
            for summary, article in summaries_with_articles:
                article_by_id[article.article_id] = article
                projected_article_status = effective_article_status.get(
                    article.article_id, article.publication_status
                )
                if dry_run and projected_article_status != article.publication_status:
                    original_status = article.publication_status
                    article.publication_status = projected_article_status
                    decision = decide_summary(
                        summary,
                        article,
                        config,
                        expected_canonical_summary_fingerprint=english_canonical_fingerprints.get(summary.article_id),
                    )
                    article.publication_status = original_status
                else:
                    decision = decide_summary(
                        summary,
                        article,
                        config,
                        expected_canonical_summary_fingerprint=english_canonical_fingerprints.get(summary.article_id),
                    )
                record_reason("summaries", decision)
                if decision.action == "publish":
                    if summary.status != "published":
                        counts["summaries_published"] += 1
                    if not dry_run and summary.status != "published":
                        summary.status = "published"
                        summary.generation_metadata = {
                            **(summary.generation_metadata or {}),
                            "publication_gate": "automated-quality-gate",
                            "autopilot": _audit_payload(decision, at=at, config=config),
                        }
                        summary.review_notes = (
                            f"{summary.review_notes or ''} Automatically published by {POLICY_VERSION}."
                        ).strip()
                elif decision.action == "archive":
                    if summary.status != "archived":
                        counts["summaries_archived"] += 1
                    if not dry_run and summary.status != "archived":
                        summary.status = "archived"
                        summary.generation_metadata = {
                            **(summary.generation_metadata or {}),
                            "publication_gate": "parent-article-excluded",
                            "autopilot": _audit_payload(decision, at=at, config=config),
                        }
                elif decision.action == "defer":
                    counts["summaries_deferred"] += 1
                    if summary.status == "archived":
                        counts["summaries_restored"] += 1
                    if not dry_run:
                        if summary.status == "archived":
                            summary.status = "review"
                        summary.generation_metadata = {
                            **(summary.generation_metadata or {}),
                            "publication_gate": "awaiting-article-decision",
                            "autopilot": _audit_payload(decision, at=at, config=config),
                        }
                elif summary.status == "review":
                    counts["summary_exceptions"] += 1
                elif (summary.generation_metadata or {}).get("autopilot"):
                    counts["summaries_reopened"] += 1
                    counts["summary_exceptions"] += 1
                    if not dry_run:
                        summary.status = "review"
                        summary.generation_metadata = {
                            **(summary.generation_metadata or {}),
                            "publication_gate": "automatic-revalidation-failed",
                            "autopilot_revalidation": _audit_payload(decision, at=at, config=config),
                        }

            gaps = (
                await db.execute(
                    select(LiteratureEvidenceGap).where(
                        LiteratureEvidenceGap.status.notin_(("dismissed", "inactive"))
                    )
                )
            ).scalars().all()
            for gap in gaps:
                related = links_by_gap.get(gap.gap_id, [])
                is_covered = any(
                    effective_link_status.get(link.id, link.status) == "confirmed"
                    and link.relation_level == "exact_disease_geography"
                    and effective_article_status.get(article.article_id, article.publication_status) == "published"
                    for link, article in related
                )
                has_exception = any(
                    effective_link_status.get(link.id, link.status) == "review"
                    for link, _ in related
                )
                if is_covered:
                    desired = "covered"
                elif has_exception:
                    desired = "review"
                elif related and gap.status not in {"no_results", "error"}:
                    desired = "open"
                else:
                    desired = gap.status
                if desired == "covered" and gap.status != "covered":
                    counts["gaps_covered"] += 1
                elif desired == "open" and gap.status != "open":
                    counts["gaps_reopened"] += 1
                if not dry_run and desired != gap.status:
                    gap.status = desired
                    gap.resolved_at = at if desired == "covered" else None
                    gap.resolution_note = (
                        f"Automatically covered by an exact public evidence link under {POLICY_VERSION}."
                        if desired == "covered"
                        else None
                    )
                if not dry_run:
                    gap.latest_metrics = {
                        **(gap.latest_metrics or {}),
                        "autopilot_policy_version": POLICY_VERSION,
                        "autopilot_last_evaluated_at": at.isoformat(),
                    }

            changed = sum(
                counts[key]
                for key in (
                    "articles_published", "articles_excluded", "links_confirmed", "links_rejected",
                    "summaries_published", "summaries_archived", "summaries_restored",
                    "summaries_reopened",
                    "gaps_covered", "gaps_reopened",
                )
            )
            if not dry_run and changed:
                run_uuid = str(uuid.uuid4())
                db.add(LiteratureIngestRun(
                    run_uuid=run_uuid,
                    source="research-radar-autopilot",
                    status="completed",
                    started_at=at,
                    completed_at=_now(),
                    through_indexed_at=at,
                    checkpoint={"policy_version": POLICY_VERSION, "mode": "apply"},
                    counts={**counts, "changed": changed},
                ))
            else:
                run_uuid = None
            if not dry_run:
                await db.commit()
        result = {
            "enabled": config.autopilot_enabled,
            "dry_run": dry_run,
            "policy_version": POLICY_VERSION,
            "effective_article_min_score": float(config.autopilot_article_min_score),
            "run_uuid": run_uuid,
            **counts,
            "changed": changed,
            "published_article_ids": published_article_ids[:100],
        }
        if diagnostics:
            result["decision_reasons"] = {
                entity: dict(counter.most_common())
                for entity, counter in decision_reasons.items()
            }
        return result

    async def snapshot(self) -> dict[str, Any]:
        config = get_config().literature
        async with get_db() as db:
            article_metadata = (
                await db.execute(
                    select(LiteratureArticle.metadata_).where(
                        LiteratureArticle.publication_status == "published"
                    )
                )
            ).scalars().all()
            auto_articles = sum(
                1 for metadata in article_metadata
                if (metadata or {}).get("autopilot", {}).get("policy_version") == POLICY_VERSION
            )
            auto_links = int((await db.execute(
                select(func.count()).select_from(LiteratureSignalArticleLink).where(
                    LiteratureSignalArticleLink.reviewed_by == AUTOPILOT_ACTOR,
                    LiteratureSignalArticleLink.status == "confirmed",
                )
            )).scalar_one() or 0)
            summary_metadata = (
                await db.execute(
                    select(LiteratureSummary.generation_metadata).where(
                        LiteratureSummary.status == "published"
                    )
                )
            ).scalars().all()
            auto_summaries = sum(
                1 for metadata in summary_metadata
                if (metadata or {}).get("autopilot", {}).get("policy_version") == POLICY_VERSION
            )
            review_article_metadata = (
                await db.execute(
                    select(LiteratureArticle.metadata_).where(
                        LiteratureArticle.publication_status == "review"
                    )
                )
            ).scalars().all()
            deferred_articles = sum(
                1 for metadata in review_article_metadata
                if _persisted_autopilot_decision(metadata) == "defer"
            )
            archived_decision_articles = sum(
                1 for metadata in review_article_metadata
                if _persisted_autopilot_decision(metadata) == "archive"
            )
            exception_articles = (
                len(review_article_metadata) - deferred_articles - archived_decision_articles
            )
            exception_links = int((await db.execute(
                select(func.count()).select_from(LiteratureSignalArticleLink).where(
                    LiteratureSignalArticleLink.status == "review"
                )
            )).scalar_one() or 0)
            review_summary_metadata = (
                await db.execute(
                    select(LiteratureSummary.generation_metadata).where(
                        LiteratureSummary.status == "review"
                    )
                )
            ).scalars().all()
            deferred_summaries = sum(
                1 for metadata in review_summary_metadata
                if _persisted_autopilot_decision(metadata) == "defer"
            )
            archived_decision_summaries = sum(
                1 for metadata in review_summary_metadata
                if _persisted_autopilot_decision(metadata) == "archive"
            )
            exception_summaries = (
                len(review_summary_metadata) - deferred_summaries - archived_decision_summaries
            )
            archived_summaries = int((await db.execute(
                select(func.count()).select_from(LiteratureSummary).where(LiteratureSummary.status == "archived")
            )).scalar_one() or 0)
            latest_run = (
                await db.execute(
                    select(LiteratureIngestRun)
                    .where(LiteratureIngestRun.source == "research-radar-autopilot")
                    .order_by(LiteratureIngestRun.started_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        return {
            "enabled": config.autopilot_enabled,
            "policy_version": POLICY_VERSION,
            "mode": "exception-only-review" if config.autopilot_enabled else "manual-review",
            "thresholds": {
                "article_min_score": config.autopilot_article_min_score,
                "article_exclude_below_score": config.autopilot_article_exclude_below_score,
                "disease_min_confidence": config.autopilot_disease_min_confidence,
                "exact_relation_min_confidence": config.autopilot_exact_relation_min_confidence,
                "context_relation_min_confidence": config.autopilot_context_relation_min_confidence,
                "summary_min_quality": config.autopilot_summary_min_quality,
            },
            "automatic": {
                "published_articles": auto_articles,
                "confirmed_links": auto_links,
                "published_summaries": auto_summaries,
            },
            "exceptions": {
                "articles": exception_articles,
                "links": exception_links,
                "summaries": exception_summaries,
                "total": exception_articles + exception_links + exception_summaries,
            },
            "deferred": {
                "articles": deferred_articles,
                "summaries": deferred_summaries,
                "total": deferred_articles + deferred_summaries,
            },
            "archived": {
                "summaries": archived_summaries,
                "review_article_decisions": archived_decision_articles,
                "review_summary_decisions": archived_decision_summaries,
            },
            "last_run": (
                {
                    "run_uuid": latest_run.run_uuid,
                    "completed_at": latest_run.completed_at,
                    "counts": latest_run.counts or {},
                }
                if latest_run else None
            ),
        }


literature_automation_service = LiteratureAutomationService()


__all__ = [
    "AUTOPILOT_ACTOR",
    "POLICY_VERSION",
    "AutomationDecision",
    "LiteratureAutomationService",
    "decide_article",
    "decide_evidence_link",
    "decide_summary",
    "literature_automation_service",
]
