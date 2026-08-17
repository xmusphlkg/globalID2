"""Persistent, reviewable discovery for surveillance-linked evidence gaps."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_config
from src.core.database import get_db
from src.core.task_manager import task_manager
from src.domain import (
    Country,
    LiteratureArticle,
    LiteratureCountryLink,
    LiteratureDiseaseLink,
    LiteratureEvidenceGap,
    LiteratureIngestRun,
    LiteratureSignalArticleLink,
    StandardDisease,
    Task,
)
from src.generation.site_data_literature import build_surveillance_evidence
from src.literature.classification import apply_surveillance_relation, classify_candidate
from src.literature.clients import CrossrefClient, EuropePmcClient
from src.literature.normalization import apply_europe_pmc, normalize_crossref, normalize_europe_pmc
from src.literature.pipeline import _global_country_catalogue
from src.literature.repository import LiteratureRepository
from src.services.situation_v3.persistence import latest_report_v3


ROOT = Path(__file__).resolve().parents[2]
ACTIVE_GAP_STATUSES = {"open", "searching", "review", "no_results", "error"}
REVIEW_STATUSES = {"review", "confirmed", "rejected"}
RELATION_LEVELS = {"exact_disease_geography", "disease_context", "candidate"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware(parsed)


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return payload


def _gap_id(signal_id: str, disease_id: str) -> str:
    digest = hashlib.sha256(f"{signal_id}|{disease_id}".encode("utf-8")).hexdigest()
    return f"gap_{digest[:24]}"


def _quoted(value: str) -> str:
    return '"' + value.replace('"', " ").strip() + '"'


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def build_gap_query_plan(
    *,
    disease_id: str,
    disease_name: str,
    aliases: list[str],
    country_names: list[str],
    lookback_days: int,
) -> dict[str, Any]:
    """Build transparent provider-specific queries without model expansion."""
    disease_terms = []
    seen_disease_terms: set[str] = set()
    for raw_term in [disease_name, *aliases]:
        term = str(raw_term).strip()
        normalized = term.casefold()
        if len(term) < 3 or normalized in seen_disease_terms:
            continue
        seen_disease_terms.add(normalized)
        disease_terms.append(term)
        if len(disease_terms) == 6:
            break
    places = []
    seen_places: set[str] = set()
    for raw_place in country_names:
        place = str(raw_place).strip()
        normalized = place.casefold()
        if not place or normalized in seen_places:
            continue
        seen_places.add(normalized)
        places.append(place)
        if len(places) == 4:
            break
    primary_disease = disease_terms[0] if disease_terms else disease_id
    primary_place = places[0] if places else ""
    exact_crossref = " ".join(part for part in (primary_disease, primary_place) if part)
    disease_crossref = primary_disease
    disease_expression = " OR ".join(_quoted(term) for term in disease_terms)
    place_expression = " OR ".join(_quoted(place) for place in places)
    exact_europe_pmc = (
        f"({disease_expression}) AND ({place_expression})"
        if place_expression
        else f"({disease_expression})"
    )
    return {
        "schema_version": "literature_gap_query.v1",
        "disease_id": disease_id,
        "disease_terms": disease_terms,
        "geography_terms": places,
        "lookback_days": lookback_days,
        "crossref": {"exact": exact_crossref, "disease_context": disease_crossref},
        "europe_pmc": {"exact": exact_europe_pmc, "disease_context": f"({disease_expression})"},
    }


def _priority(signal: dict[str, Any], gap: dict[str, Any]) -> float:
    risk_score = signal.get("risk", {}).get("score")
    risk_component = float(risk_score) * 0.55 if risk_score is not None else 35.0
    official_component = 18.0 if signal.get("kind") == "official_event" else 8.0
    coverage_component = 18.0 if gap.get("gap_type") == "catalogue_coverage_gap" else 10.0
    geography_component = min(12.0, 4.0 * len(gap.get("geographies") or []))
    return round(min(100.0, risk_component + official_component + coverage_component + geography_component), 1)


class LiteratureGapService:
    async def _catalogues(self) -> tuple[list[dict[str, Any]], list[dict[str, str]], dict[str, dict[str, Any]]]:
        cfg = get_config().literature
        alias_payload = _load_json(ROOT / cfg.disease_aliases_path)
        aliases_by_id = alias_payload.get("aliases") or {}
        taxonomy = _load_json(ROOT / cfg.taxonomy_path)
        async with get_db() as db:
            diseases = (
                await db.execute(select(StandardDisease).where(StandardDisease.is_active.is_(True)))
            ).scalars().all()
            countries = (
                await db.execute(select(Country).where(Country.is_active.is_(True)))
            ).scalars().all()
        disease_catalogue = [
            {
                "disease_id": row.disease_id,
                "name_en": row.standard_name_en,
                "name_zh": row.standard_name_zh,
                "slug": (row.metadata_ or {}).get("slug") or _slug(row.standard_name_en),
                "aliases": [
                    *[str(item) for item in aliases_by_id.get(row.disease_id, [])],
                    *[str(item) for item in (row.metadata_ or {}).get("aliases", [])],
                ],
            }
            for row in diseases
        ]
        country_catalogue = _global_country_catalogue(countries, taxonomy)
        return disease_catalogue, country_catalogue, {
            item["disease_id"]: item for item in disease_catalogue
        }

    @staticmethod
    async def _published_projection(db: AsyncSession) -> list[dict[str, Any]]:
        articles = (
            await db.execute(
                select(LiteratureArticle).where(
                    LiteratureArticle.publication_status == "published",
                    LiteratureArticle.integrity_status.notin_(("retracted", "expression_of_concern")),
                )
            )
        ).scalars().all()
        if not articles:
            return []
        article_ids = [article.article_id for article in articles]
        disease_links = (
            await db.execute(
                select(LiteratureDiseaseLink).where(LiteratureDiseaseLink.article_id.in_(article_ids))
            )
        ).scalars().all()
        country_links = (
            await db.execute(
                select(LiteratureCountryLink).where(LiteratureCountryLink.article_id.in_(article_ids))
            )
        ).scalars().all()
        diseases: dict[str, list[dict[str, Any]]] = {article_id: [] for article_id in article_ids}
        countries: dict[str, list[dict[str, Any]]] = {article_id: [] for article_id in article_ids}
        for link in disease_links:
            diseases[link.article_id].append({
                "disease_id": link.disease_id,
                "confidence": link.confidence,
            })
        for link in country_links:
            countries[link.article_id].append({
                "code": link.country_code,
                "confidence": link.confidence,
            })
        return [
            {
                "article_id": article.article_id,
                "slug": article.slug,
                "title": article.title,
                "journal": article.journal,
                "study_type": article.study_type,
                "published_at": article.published_at.isoformat() if article.published_at else None,
                "diseases": diseases[article.article_id],
                "countries": countries[article.article_id],
            }
            for article in articles
        ]

    async def refresh_from_snapshot(self) -> dict[str, Any]:
        """Reconcile durable gap state with the latest eligible snapshot."""
        snapshot = await latest_report_v3()
        if not snapshot:
            return {"available": False, "gaps_created": 0, "gaps_updated": 0, "gaps_inactivated": 0}
        disease_catalogue, _, diseases_by_id = await self._catalogues()
        del disease_catalogue
        async with get_db() as db:
            articles = await self._published_projection(db)
            decisions = (
                await db.execute(
                    select(LiteratureSignalArticleLink).where(
                        LiteratureSignalArticleLink.status.in_(("confirmed", "rejected"))
                    )
                )
            ).scalars().all()
            projection = build_surveillance_evidence(
                articles,
                snapshot,
                diseases_by_id=diseases_by_id,
                relation_decisions=[
                    {
                        "signal_id": link.signal_id,
                        "article_id": link.article_id,
                        "relation_level": link.relation_level,
                        "status": link.status,
                    }
                    for link in decisions
                ],
            )
            existing = {
                row.gap_id: row
                for row in (await db.execute(select(LiteratureEvidenceGap))).scalars().all()
            }
            aliases_by_id = {
                item["disease_id"]: item.get("aliases") or []
                for item in diseases_by_id.values()
            }
            signal_by_id = {item["signal_id"]: item for item in projection["signals"]}
            detected_at = _parse_datetime(snapshot.get("generated_at")) or _utc_now()
            cfg = get_config().literature
            active_gap_ids: set[str] = set()
            created = updated = 0
            for item in projection["evidence_gaps"]:
                gap_id = _gap_id(item["signal_id"], item["disease_id"])
                active_gap_ids.add(gap_id)
                signal = signal_by_id[item["signal_id"]]
                country_names = [place["name_en"] for place in item.get("geographies") or []]
                country_codes = [place["code"] for place in item.get("geographies") or []]
                plan = build_gap_query_plan(
                    disease_id=item["disease_id"],
                    disease_name=item["disease_name_en"],
                    aliases=aliases_by_id.get(item["disease_id"], []),
                    country_names=country_names,
                    lookback_days=cfg.gap_discovery_lookback_days,
                )
                row = existing.get(gap_id)
                if row is None:
                    row = LiteratureEvidenceGap(
                        gap_id=gap_id,
                        signal_id=item["signal_id"],
                        snapshot_id=snapshot.get("snapshot_id"),
                        signal_kind=signal["kind"],
                        signal_section=signal["section"],
                        disease_id=item["disease_id"],
                        disease_name=item["disease_name_en"],
                        country_codes=country_codes,
                        country_names=country_names,
                        gap_type=item["gap_type"],
                        status="open",
                        priority_score=_priority(signal, item),
                        query_plan=plan,
                        latest_metrics={
                            "context_article_count": item.get("context_article_count", 0),
                            "risk": signal.get("risk") or {},
                            "data_through": signal.get("data_through"),
                        },
                        source_snapshot_at=detected_at,
                        first_detected_at=detected_at,
                        last_detected_at=detected_at,
                        next_search_at=_utc_now(),
                        metadata_={"visibility": projection["visibility"]},
                    )
                    db.add(row)
                    existing[gap_id] = row
                    created += 1
                else:
                    row.snapshot_id = snapshot.get("snapshot_id")
                    row.signal_kind = signal["kind"]
                    row.signal_section = signal["section"]
                    row.disease_name = item["disease_name_en"]
                    row.country_codes = country_codes
                    row.country_names = country_names
                    row.gap_type = item["gap_type"]
                    row.priority_score = _priority(signal, item)
                    row.query_plan = plan
                    row.source_snapshot_at = detected_at
                    row.last_detected_at = detected_at
                    row.latest_metrics = {
                        **(row.latest_metrics or {}),
                        "context_article_count": item.get("context_article_count", 0),
                        "risk": signal.get("risk") or {},
                        "data_through": signal.get("data_through"),
                    }
                    row.metadata_ = {**(row.metadata_ or {}), "visibility": projection["visibility"]}
                    if row.status in {"covered", "inactive"}:
                        row.status = "open"
                        row.resolved_at = None
                        row.resolution_note = None
                        row.next_search_at = _utc_now()
                    updated += 1
            exact_signal_ids = {
                signal["signal_id"]
                for signal in projection["signals"]
                if signal["exact_article_count"] > 0
            }
            inactivated = 0
            for gap_id, row in existing.items():
                if row.signal_id in exact_signal_ids and row.status != "dismissed":
                    row.status = "covered"
                    row.resolved_at = _utc_now()
                    row.resolution_note = "Published exact disease-and-geography evidence is available."
                elif gap_id not in active_gap_ids and row.status in ACTIVE_GAP_STATUSES:
                    row.status = "inactive"
                    row.resolved_at = _utc_now()
                    row.resolution_note = "Signal is not active in the latest eligible Situation Room snapshot."
                    inactivated += 1
            await db.commit()
        return {
            "available": True,
            "snapshot_id": snapshot.get("snapshot_id"),
            "visibility": projection["visibility"],
            "active_signals": projection["metrics"]["active_signals"],
            "active_gaps": len(active_gap_ids),
            "gaps_created": created,
            "gaps_updated": updated,
            "gaps_inactivated": inactivated,
        }

    async def execute(
        self,
        task: Task | None = None,
        *,
        gap_ids: list[str] | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        cfg = get_config().literature
        if not cfg.gap_discovery_enabled:
            raise ValueError("Literature evidence-gap discovery is disabled")
        refresh = await self.refresh_from_snapshot()
        now = _utc_now()
        requested_gap_ids = (
            [str(item) for item in ((task.input_data or {}).get("gap_ids") or [])]
            if task
            else [str(item) for item in gap_ids or []]
        )
        max_gaps = int(
            ((task.input_data or {}).get("limit") if task else limit)
            or cfg.gap_discovery_max_gaps_per_run
        )
        async with get_db() as db:
            if requested_gap_ids:
                query = select(LiteratureEvidenceGap).where(
                    LiteratureEvidenceGap.gap_id.in_(requested_gap_ids),
                    LiteratureEvidenceGap.status.notin_(("dismissed", "covered", "inactive")),
                )
            else:
                query = select(LiteratureEvidenceGap).where(
                    LiteratureEvidenceGap.status.in_(("open", "review", "no_results", "error")),
                    or_(LiteratureEvidenceGap.next_search_at.is_(None), LiteratureEvidenceGap.next_search_at <= now),
                )
            gaps = (
                await db.execute(
                    query.order_by(LiteratureEvidenceGap.priority_score.desc(), LiteratureEvidenceGap.last_detected_at.desc())
                    .limit(max_gaps)
                )
            ).scalars().all()
        run_uuid = str(uuid.uuid4())
        await self._create_run(run_uuid, gaps, now)
        disease_catalogue, country_catalogue, _ = await self._catalogues()
        taxonomy = _load_json(ROOT / cfg.taxonomy_path)
        totals = {
            "gaps_selected": len(gaps),
            "gaps_with_candidates": 0,
            "gaps_without_results": 0,
            "fetched": 0,
            "normalized": 0,
            "inserted": 0,
            "updated": 0,
            "exact_candidates": 0,
            "context_candidates": 0,
            "weak_candidates": 0,
            "errors": 0,
        }
        try:
            for index, gap in enumerate(gaps):
                try:
                    result = await self._discover_gap(
                        gap.gap_id,
                        disease_catalogue=disease_catalogue,
                        country_catalogue=country_catalogue,
                        taxonomy=taxonomy,
                        now=now,
                    )
                    for key, value in result.items():
                        if key in totals:
                            totals[key] += int(value)
                    totals["gaps_with_candidates" if result["candidate_links"] else "gaps_without_results"] += 1
                except Exception as exc:
                    totals["errors"] += 1
                    await self._mark_gap_error(gap.gap_id, exc)
                if task:
                    await task_manager.update_task_progress(
                        task.task_uuid,
                        min(98, int(100 * (index + 1) / max(1, len(gaps)))),
                    )
            limit_result = await self.enforce_candidate_limits()
            totals["deprioritized_candidates"] = limit_result["deprioritized"]
            automation = None
            if cfg.autopilot_enabled:
                from src.services.literature_automation_service import literature_automation_service

                automation = await literature_automation_service.reconcile()
                totals["autopilot_changed"] = int(automation.get("changed") or 0)
            await self._finish_run(run_uuid, "completed", totals)
            if task:
                await task_manager.update_task_progress(task.task_uuid, 100)
            return {"run_uuid": run_uuid, "refresh": refresh, **totals, "automation": automation}
        except Exception as exc:
            await self._finish_run(run_uuid, "failed", totals, error=str(exc))
            raise

    async def _discover_gap(
        self,
        gap_id: str,
        *,
        disease_catalogue: list[dict[str, Any]],
        country_catalogue: list[dict[str, str]],
        taxonomy: dict[str, Any],
        now: datetime,
    ) -> dict[str, int]:
        cfg = get_config().literature
        async with get_db() as db:
            gap = (
                await db.execute(
                    select(LiteratureEvidenceGap).where(LiteratureEvidenceGap.gap_id == gap_id).with_for_update()
                )
            ).scalar_one()
            gap.status = "searching"
            gap.error = None
            plan = dict(gap.query_plan or {})
            await db.commit()
        since = now - timedelta(days=cfg.gap_discovery_lookback_days)
        record_limit = cfg.gap_discovery_records_per_gap
        crossref = CrossrefClient(
            mailto=cfg.contact_email,
            timeout_seconds=cfg.request_timeout_seconds,
            retries=cfg.max_retries,
        )
        europe_pmc = EuropePmcClient(
            timeout_seconds=cfg.request_timeout_seconds,
            retries=cfg.max_retries,
        )
        exact_crossref, exact_epmc = await asyncio.gather(
            crossref.search_works(
                query=str(plan.get("crossref", {}).get("exact") or gap.disease_name),
                since=since,
                until=now,
                max_records=record_limit,
            ),
            europe_pmc.search_recent(
                query=str(plan.get("europe_pmc", {}).get("exact") or _quoted(gap.disease_name)),
                since=since,
                until=now,
                max_records=record_limit,
            ) if cfg.europe_pmc_enabled else asyncio.sleep(0, result=[]),
        )
        raw_count = len(exact_crossref) + len(exact_epmc)
        context_crossref: list[dict[str, Any]] = []
        context_epmc: list[dict[str, Any]] = []
        if raw_count < 3:
            fallback_limit = max(5, record_limit // 3)
            context_crossref, context_epmc = await asyncio.gather(
                crossref.search_works(
                    query=str(plan.get("crossref", {}).get("disease_context") or gap.disease_name),
                    since=since,
                    until=now,
                    max_records=fallback_limit,
                ),
                europe_pmc.search_recent(
                    query=str(plan.get("europe_pmc", {}).get("disease_context") or _quoted(gap.disease_name)),
                    since=since,
                    until=now,
                    max_records=fallback_limit,
                ) if cfg.europe_pmc_enabled else asyncio.sleep(0, result=[]),
            )
            raw_count += len(context_crossref) + len(context_epmc)

        candidates: dict[str, Any] = {}
        for raw in [*exact_crossref, *context_crossref]:
            candidate = normalize_crossref(raw)
            if candidate:
                candidates[candidate.doi or candidate.article_id] = candidate
        for raw in [*exact_epmc, *context_epmc]:
            candidate = normalize_europe_pmc(raw)
            if not candidate:
                continue
            key = candidate.doi or candidate.article_id
            if key in candidates:
                apply_europe_pmc(candidates[key], raw)
            else:
                candidates[key] = candidate

        target_countries = set(gap.country_codes or [])
        inserted = updated = exact = context = weak = candidate_links = 0
        async with get_db() as db:
            repository = LiteratureRepository(db)
            ranked_candidates: list[dict[str, Any]] = []
            for candidate in candidates.values():
                classification = classify_candidate(
                    candidate,
                    diseases=disease_catalogue,
                    countries=country_catalogue,
                    taxonomy=taxonomy,
                    now=now,
                    auto_publish_min_score=cfg.auto_publish_min_score,
                )
                disease_match = next(
                    (match for match in classification.diseases if match.key == gap.disease_id),
                    None,
                )
                if disease_match is None or disease_match.confidence < 0.62:
                    continue
                country_matches = [
                    match
                    for match in classification.countries
                    if match.key in target_countries
                ]
                country_confidence = max((match.confidence for match in country_matches), default=0.0)
                if disease_match.confidence >= 0.78 and country_confidence >= 0.78:
                    relation_level = "exact_disease_geography"
                    confidence = min(disease_match.confidence, country_confidence)
                elif disease_match.confidence >= 0.78:
                    relation_level = "disease_context"
                    confidence = disease_match.confidence
                else:
                    relation_level = "candidate"
                    confidence = disease_match.confidence
                apply_surveillance_relation(classification, relation_level)
                ranked_candidates.append({
                    "candidate": candidate,
                    "classification": classification,
                    "disease_match": disease_match,
                    "country_confidence": country_confidence,
                    "relation_level": relation_level,
                    "confidence": confidence,
                })
            relation_rank = {
                "exact_disease_geography": 3,
                "disease_context": 2,
                "candidate": 1,
            }
            ranked_candidates.sort(
                key=lambda item: (
                    relation_rank[item["relation_level"]],
                    item["confidence"],
                    item["classification"].discovery_score,
                    item["candidate"].published_at or datetime.min.replace(tzinfo=timezone.utc),
                ),
                reverse=True,
            )
            for item in ranked_candidates[: cfg.gap_discovery_candidate_limit]:
                candidate = item["candidate"]
                classification = item["classification"]
                disease_match = item["disease_match"]
                country_confidence = item["country_confidence"]
                relation_level = item["relation_level"]
                confidence = item["confidence"]
                exact += int(relation_level == "exact_disease_geography")
                context += int(relation_level == "disease_context")
                weak += int(relation_level == "candidate")
                classification.publication_status = "review"
                was_inserted = await repository.upsert(
                    candidate,
                    classification,
                    discovery_context={
                        "gap_id": gap.gap_id,
                        "signal_id": gap.signal_id,
                        "source": "gap_discovery",
                        "discovered_at": now.isoformat(),
                        "relation_level": relation_level,
                    },
                    new_publication_status="review",
                )
                inserted += int(was_inserted)
                updated += int(not was_inserted)
                article = (
                    await db.execute(
                        select(LiteratureArticle).where(LiteratureArticle.article_id == candidate.article_id)
                    )
                ).scalar_one()
                link = (
                    await db.execute(
                        select(LiteratureSignalArticleLink).where(
                            LiteratureSignalArticleLink.signal_id == gap.signal_id,
                            LiteratureSignalArticleLink.article_id == article.article_id,
                        )
                    )
                ).scalar_one_or_none()
                if link is None:
                    link = LiteratureSignalArticleLink(
                        gap_id=gap.gap_id,
                        signal_id=gap.signal_id,
                        article_id=article.article_id,
                        relation_level=relation_level,
                        status="review",
                        confidence=confidence,
                        source="gap_discovery",
                        match_reasons=[],
                        metadata_={},
                    )
                    db.add(link)
                elif link.status in {"review", "deprioritized"} or (
                    link.status in {"confirmed", "rejected"}
                    and link.reviewed_by == "research-radar-autopilot"
                ):
                    link.gap_id = gap.gap_id
                    link.relation_level = relation_level
                    link.confidence = confidence
                    link.status = "review"
                    link.reviewed_at = None
                    link.reviewed_by = None
                    link.review_note = None
                if link.status == "review":
                    link.match_reasons = [
                        f"disease classifier {disease_match.confidence:.2f}",
                        *(
                            [f"signal geography classifier {country_confidence:.2f}"]
                            if country_confidence
                            else ["signal geography not confirmed"]
                        ),
                        f"publication within {cfg.gap_discovery_lookback_days} days",
                    ]
                candidate_links += 1
            gap = (
                await db.execute(
                    select(LiteratureEvidenceGap).where(LiteratureEvidenceGap.gap_id == gap_id).with_for_update()
                )
            ).scalar_one()
            gap.status = "review" if candidate_links else "no_results"
            gap.last_searched_at = now
            gap.next_search_at = now + timedelta(hours=cfg.gap_discovery_retry_hours)
            gap.latest_metrics = {
                **(gap.latest_metrics or {}),
                "fetched": raw_count,
                "normalized": len(candidates),
                "eligible_before_limit": len(ranked_candidates),
                "candidate_limit": cfg.gap_discovery_candidate_limit,
                "candidate_links": candidate_links,
                "exact_candidates": exact,
                "context_candidates": context,
                "weak_candidates": weak,
                "last_run_at": now.isoformat(),
            }
            await db.commit()
        return {
            "fetched": raw_count,
            "normalized": len(candidates),
            "inserted": inserted,
            "updated": updated,
            "exact_candidates": exact,
            "context_candidates": context,
            "weak_candidates": weak,
            "candidate_links": candidate_links,
        }

    async def enforce_candidate_limits(self) -> dict[str, int]:
        """Keep review queues bounded without treating overflow as rejection."""
        limit = get_config().literature.gap_discovery_candidate_limit
        relation_rank = {
            "exact_disease_geography": 3,
            "disease_context": 2,
            "candidate": 1,
        }
        deprioritized = reactivated = 0
        async with get_db() as db:
            rows = (
                await db.execute(
                    select(LiteratureSignalArticleLink, LiteratureArticle)
                    .join(
                        LiteratureArticle,
                        LiteratureArticle.article_id == LiteratureSignalArticleLink.article_id,
                    )
                    .where(
                        LiteratureSignalArticleLink.gap_id.is_not(None),
                        LiteratureSignalArticleLink.status.in_(("review", "deprioritized")),
                    )
                )
            ).all()
            by_gap: dict[str, list[tuple[LiteratureSignalArticleLink, LiteratureArticle]]] = defaultdict(list)
            for link, article in rows:
                if link.gap_id:
                    by_gap[link.gap_id].append((link, article))
            for gap_id, candidates in by_gap.items():
                candidates.sort(
                    key=lambda pair: (
                        relation_rank.get(pair[0].relation_level, 0),
                        pair[0].confidence,
                        pair[1].discovery_score,
                        pair[1].published_at or datetime.min.replace(tzinfo=timezone.utc),
                    ),
                    reverse=True,
                )
                for index, (link, _) in enumerate(candidates):
                    desired = "review" if index < limit else "deprioritized"
                    if link.status == desired:
                        continue
                    reactivated += int(desired == "review")
                    deprioritized += int(desired == "deprioritized")
                    link.status = desired
                    link.metadata_ = {
                        **(link.metadata_ or {}),
                        "queue_ranking": index + 1,
                        "queue_limit": limit,
                        "queue_policy": "relation-confidence-discovery-recency",
                    }
                gap = (
                    await db.execute(
                        select(LiteratureEvidenceGap).where(
                            LiteratureEvidenceGap.gap_id == gap_id
                        )
                    )
                ).scalar_one_or_none()
                if gap:
                    gap.latest_metrics = {
                        **(gap.latest_metrics or {}),
                        "review_queue_limit": limit,
                        "eligible_candidate_links": len(candidates),
                        "deprioritized_candidates": max(0, len(candidates) - limit),
                    }
            await db.commit()
        return {"deprioritized": deprioritized, "reactivated": reactivated}

    async def review_link(
        self,
        link_id: int,
        *,
        status: str,
        relation_level: str | None,
        reviewer: str,
        note: str | None,
    ) -> dict[str, Any]:
        if status not in {"confirmed", "rejected"}:
            raise ValueError("Unsupported evidence-link review status")
        if relation_level is not None and relation_level not in RELATION_LEVELS:
            raise ValueError("Unsupported evidence-link relation level")
        async with get_db() as db:
            link = (
                await db.execute(
                    select(LiteratureSignalArticleLink).where(
                        LiteratureSignalArticleLink.id == link_id
                    ).with_for_update()
                )
            ).scalar_one_or_none()
            if link is None:
                raise LookupError("Evidence link not found")
            article = (
                await db.execute(
                    select(LiteratureArticle).where(LiteratureArticle.article_id == link.article_id)
                )
            ).scalar_one()
            link.status = status
            if relation_level is not None:
                link.relation_level = relation_level
            link.reviewed_at = _utc_now()
            link.reviewed_by = reviewer
            link.review_note = note
            if link.gap_id:
                gap = (
                    await db.execute(
                        select(LiteratureEvidenceGap).where(
                            LiteratureEvidenceGap.gap_id == link.gap_id
                        ).with_for_update()
                    )
                ).scalar_one_or_none()
                if gap:
                    is_public_exact = (
                        status == "confirmed"
                        and link.relation_level == "exact_disease_geography"
                        and article.publication_status == "published"
                    )
                    gap.status = "covered" if is_public_exact else "review"
                    gap.resolved_at = _utc_now() if is_public_exact else None
                    gap.resolution_note = (
                        "Editor-confirmed exact relationship is publicly available."
                        if is_public_exact
                        else None
                    )
            await db.commit()
            return self._link_payload(link, article)

    async def update_gap(self, gap_id: str, *, status: str, note: str | None) -> dict[str, Any]:
        if status not in {"open", "dismissed"}:
            raise ValueError("Unsupported gap status update")
        async with get_db() as db:
            gap = (
                await db.execute(
                    select(LiteratureEvidenceGap).where(
                        LiteratureEvidenceGap.gap_id == gap_id
                    ).with_for_update()
                )
            ).scalar_one_or_none()
            if gap is None:
                raise LookupError("Evidence gap not found")
            gap.status = status
            gap.resolution_note = note
            gap.resolved_at = _utc_now() if status == "dismissed" else None
            if status == "open":
                gap.next_search_at = _utc_now()
            await db.commit()
            return self._gap_payload(gap, [])

    async def count_gaps(self, *, status: str | None = None) -> int:
        async with get_db() as db:
            query = select(func.count()).select_from(LiteratureEvidenceGap)
            if status:
                query = query.where(LiteratureEvidenceGap.status == status)
            return int((await db.execute(query)).scalar_one() or 0)

    async def list_gaps(self, *, status: str | None = None, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        candidate_limit = get_config().literature.gap_discovery_candidate_limit
        async with get_db() as db:
            query = select(LiteratureEvidenceGap).order_by(
                LiteratureEvidenceGap.priority_score.desc(),
                LiteratureEvidenceGap.last_detected_at.desc(),
            )
            if status:
                query = query.where(LiteratureEvidenceGap.status == status)
            gaps = (await db.execute(query.offset(offset).limit(limit))).scalars().all()
            if not gaps:
                return []
            gap_ids = [gap.gap_id for gap in gaps]
            links = (
                await db.execute(
                    select(LiteratureSignalArticleLink, LiteratureArticle)
                    .join(LiteratureArticle, LiteratureArticle.article_id == LiteratureSignalArticleLink.article_id)
                    .where(LiteratureSignalArticleLink.gap_id.in_(gap_ids))
                    .where(LiteratureSignalArticleLink.status != "deprioritized")
                    .order_by(LiteratureSignalArticleLink.confidence.desc())
                )
            ).all()
            links_by_gap: dict[str, list[dict[str, Any]]] = {gap_id: [] for gap_id in gap_ids}
            for link, article in links:
                if link.gap_id:
                    links_by_gap[link.gap_id].append(self._link_payload(link, article))
            return [
                self._gap_payload(gap, links_by_gap[gap.gap_id][:candidate_limit])
                for gap in gaps
            ]

    @staticmethod
    def _link_payload(link: LiteratureSignalArticleLink, article: LiteratureArticle) -> dict[str, Any]:
        return {
            "id": link.id,
            "gap_id": link.gap_id,
            "signal_id": link.signal_id,
            "article_id": link.article_id,
            "article_slug": article.slug,
            "article_title": article.title,
            "journal": article.journal,
            "published_at": article.published_at,
            "publication_status": article.publication_status,
            "integrity_status": article.integrity_status,
            "relation_level": link.relation_level,
            "status": link.status,
            "confidence": link.confidence,
            "source": link.source,
            "match_reasons": link.match_reasons or [],
            "reviewed_at": link.reviewed_at,
            "reviewed_by": link.reviewed_by,
            "review_note": link.review_note,
        }

    @staticmethod
    def _gap_payload(gap: LiteratureEvidenceGap, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "gap_id": gap.gap_id,
            "signal_id": gap.signal_id,
            "snapshot_id": gap.snapshot_id,
            "signal_kind": gap.signal_kind,
            "signal_section": gap.signal_section,
            "disease_id": gap.disease_id,
            "disease_name": gap.disease_name,
            "country_codes": gap.country_codes or [],
            "country_names": gap.country_names or [],
            "gap_type": gap.gap_type,
            "status": gap.status,
            "priority_score": gap.priority_score,
            "query_plan": gap.query_plan or {},
            "latest_metrics": gap.latest_metrics or {},
            "source_snapshot_at": gap.source_snapshot_at,
            "first_detected_at": gap.first_detected_at,
            "last_detected_at": gap.last_detected_at,
            "last_searched_at": gap.last_searched_at,
            "next_search_at": gap.next_search_at,
            "resolved_at": gap.resolved_at,
            "resolution_note": gap.resolution_note,
            "error": gap.error,
            "candidates": candidates,
        }

    async def _mark_gap_error(self, gap_id: str, exc: Exception) -> None:
        cfg = get_config().literature
        async with get_db() as db:
            gap = (
                await db.execute(select(LiteratureEvidenceGap).where(LiteratureEvidenceGap.gap_id == gap_id))
            ).scalar_one_or_none()
            if gap:
                gap.status = "error"
                gap.error = str(exc)[:4000]
                gap.last_searched_at = _utc_now()
                gap.next_search_at = _utc_now() + timedelta(hours=cfg.gap_discovery_retry_hours)
                await db.commit()

    async def _create_run(self, run_uuid: str, gaps: list[LiteratureEvidenceGap], now: datetime) -> None:
        async with get_db() as db:
            db.add(LiteratureIngestRun(
                run_uuid=run_uuid,
                source="evidence-gap-discovery",
                status="running",
                started_at=now,
                from_indexed_at=None,
                through_indexed_at=now,
                checkpoint={"strategy": "signal-evidence-gap", "gap_ids": [gap.gap_id for gap in gaps]},
                counts={},
            ))
            await db.commit()

    async def _finish_run(
        self,
        run_uuid: str,
        status: str,
        counts: dict[str, int],
        *,
        error: str | None = None,
    ) -> None:
        async with get_db() as db:
            run = (
                await db.execute(select(LiteratureIngestRun).where(LiteratureIngestRun.run_uuid == run_uuid))
            ).scalar_one()
            run.status = status
            run.completed_at = _utc_now()
            run.counts = counts
            run.error = error
            await db.commit()


literature_gap_service = LiteratureGapService()


__all__ = [
    "LiteratureGapService",
    "build_gap_query_plan",
    "literature_gap_service",
]
