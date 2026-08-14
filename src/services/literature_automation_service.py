"""Versioned Research Radar autopilot with exception-only editorial review.

Automation is deliberately deterministic.  It can publish only records that
pass bibliographic, integrity, provenance, and confidence gates; all decisions
are written into existing audit metadata and status-event tables.  Explicit
editorial decisions always win and are never rewritten by this service.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
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
from src.literature.enrichment import ALLOWED_EVIDENCE, SUMMARY_FIELDS, source_fingerprint


POLICY_VERSION = "research-radar-autopilot.v1"
AUTOPILOT_ACTOR = "research-radar-autopilot"
_RUN_LOCK = asyncio.Lock()


@dataclass(frozen=True, slots=True)
class AutomationDecision:
    action: Literal["publish", "exclude", "confirm", "reject", "hold"]
    reasons: tuple[str, ...]


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
        return AutomationDecision("hold", tuple(hold_reasons))
    score = float(article.discovery_score or 0.0)
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


def decide_summary(summary: Any, article: Any, config: Any) -> AutomationDecision:
    """Validate a model summary against deterministic publication gates."""
    automation = dict((summary.generation_metadata or {}).get("autopilot") or {})
    if summary.status == "published" and (
        not automation
        or summary.generated_by == "control-plane-editor"
        or (summary.generation_metadata or {}).get("editorial_reviewed_at")
    ):
        return AutomationDecision("hold", ("summary is already published",))
    if article.publication_status != "published":
        return AutomationDecision("hold", ("article is not public",))
    if summary.generated_by != "literature-evidence-agent":
        return AutomationDecision("hold", ("summary was not produced by the evidence agent",))
    quality = float(summary.quality_score or 0.0)
    if quality < config.autopilot_summary_min_quality:
        return AutomationDecision("hold", (f"summary quality {quality:.3f} is below the automatic gate",))
    metadata = dict(summary.generation_metadata or {})
    if str(metadata.get("source_fingerprint") or "") != source_fingerprint(article):
        return AutomationDecision("hold", ("summary source fingerprint is stale",))
    if "verbatim-overlap" in str(summary.review_notes or "").lower():
        return AutomationDecision("hold", ("verbatim overlap was detected during generation",))
    required_fields = {"research_question", "study_design", "main_findings", "public_health_relevance", "gids_interpretation"}
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
    async def reconcile(self, *, dry_run: bool = False, export: bool | None = None) -> dict[str, Any]:
        config = get_config().literature
        if not config.autopilot_enabled and not dry_run:
            return {"enabled": False, "dry_run": False, "policy_version": POLICY_VERSION, "changed": 0}
        async with _RUN_LOCK:
            result = await self._reconcile_database(config, dry_run=dry_run)
            should_export = config.autopilot_export_on_change if export is None else export
            if not dry_run and should_export and result["changed"]:
                from src.services.literature_publication_service import export_public_research_artifacts

                result["public_export"] = await export_public_research_artifacts()
            return result

    async def _reconcile_database(self, config: Any, *, dry_run: bool) -> dict[str, Any]:
        at = _now()
        counts = {
            "articles_published": 0,
            "articles_excluded": 0,
            "article_exceptions": 0,
            "links_confirmed": 0,
            "links_rejected": 0,
            "link_exceptions": 0,
            "summaries_published": 0,
            "summaries_reopened": 0,
            "summary_exceptions": 0,
            "gaps_covered": 0,
            "gaps_reopened": 0,
        }
        published_article_ids: list[str] = []
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
            for link, article in links_with_articles:
                effective = link.status
                if link.status in {"review", "deprioritized"}:
                    decision = decide_evidence_link(link, article, config, now=at)
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
                        ).group_by(LiteratureDiseaseLink.article_id)
                    )
                ).all()
            }
            articles = (await db.execute(select(LiteratureArticle))).scalars().all()
            effective_article_status = {article.article_id: article.publication_status for article in articles}
            article_by_id = {article.article_id: article for article in articles}
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
                if decision.action == "publish":
                    desired = "published"
                    counts["articles_published"] += 1
                    published_article_ids.append(article.article_id)
                elif decision.action == "exclude":
                    desired = "excluded"
                    counts["articles_excluded"] += 1
                else:
                    desired = "review"
                    counts["article_exceptions"] += 1
                effective_article_status[article.article_id] = desired
                if dry_run or desired == article.publication_status:
                    continue
                previous = article.publication_status
                article.publication_status = desired
                article.metadata_ = {**(article.metadata_ or {}), "autopilot": _audit_payload(decision, at=at, config=config)}
                db.add(LiteratureStatusEvent(
                    article_id=article.article_id,
                    event_type="publication_status_changed",
                    previous_status=previous,
                    current_status=desired,
                    source=AUTOPILOT_ACTOR,
                    effective_at=at,
                    metadata_={"policy_version": POLICY_VERSION, "reasons": list(decision.reasons)},
                ))

            summaries = (
                await db.execute(
                    select(LiteratureSummary).where(LiteratureSummary.status.in_(("review", "published")))
                )
            ).scalars().all()
            for summary in summaries:
                article = article_by_id.get(summary.article_id)
                if article is None:
                    continue
                if dry_run and effective_article_status.get(article.article_id) != article.publication_status:
                    original_status = article.publication_status
                    article.publication_status = effective_article_status[article.article_id]
                    decision = decide_summary(summary, article, config)
                    article.publication_status = original_status
                else:
                    decision = decide_summary(summary, article, config)
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
                    "summaries_published", "summaries_reopened", "gaps_covered", "gaps_reopened",
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
        return {
            "enabled": config.autopilot_enabled,
            "dry_run": dry_run,
            "policy_version": POLICY_VERSION,
            "run_uuid": run_uuid,
            **counts,
            "changed": changed,
            "published_article_ids": published_article_ids[:100],
        }

    async def snapshot(self) -> dict[str, Any]:
        config = get_config().literature
        async with get_db() as db:
            articles = (
                await db.execute(select(LiteratureArticle).where(LiteratureArticle.publication_status == "published"))
            ).scalars().all()
            auto_articles = sum(
                1 for article in articles
                if (article.metadata_ or {}).get("autopilot", {}).get("policy_version") == POLICY_VERSION
            )
            auto_links = int((await db.execute(
                select(func.count()).select_from(LiteratureSignalArticleLink).where(
                    LiteratureSignalArticleLink.reviewed_by == AUTOPILOT_ACTOR,
                    LiteratureSignalArticleLink.status == "confirmed",
                )
            )).scalar_one() or 0)
            summaries = (
                await db.execute(select(LiteratureSummary).where(LiteratureSummary.status == "published"))
            ).scalars().all()
            auto_summaries = sum(
                1 for summary in summaries
                if (summary.generation_metadata or {}).get("autopilot", {}).get("policy_version") == POLICY_VERSION
            )
            exception_articles = int((await db.execute(
                select(func.count()).select_from(LiteratureArticle).where(LiteratureArticle.publication_status == "review")
            )).scalar_one() or 0)
            exception_links = int((await db.execute(
                select(func.count()).select_from(LiteratureSignalArticleLink).where(
                    LiteratureSignalArticleLink.status == "review"
                )
            )).scalar_one() or 0)
            exception_summaries = int((await db.execute(
                select(func.count()).select_from(LiteratureSummary).where(LiteratureSummary.status == "review")
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
