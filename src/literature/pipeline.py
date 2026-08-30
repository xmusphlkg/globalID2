"""End-to-end incremental Research Radar synchronization pipeline."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import uuid
from typing import Any, Awaitable

import pycountry
from sqlalchemy import select

from src.core.database import get_db
from src.core.logging import get_logger
from src.core.task_manager import task_manager
from src.domain import Country, LiteratureIngestRun, StandardDisease, Task

from .classification import classify_candidate
from .clients import (
    BiorxivClient,
    CrossrefClient,
    ElsevierClient,
    EuropePmcClient,
    OfficialGuidanceOaiClient,
    OpenAlexClient,
    PublisherRssClient,
    SpringerNatureClient,
    UnpaywallClient,
)
from .controlled_discovery import build_controlled_query_batches, fetch_controlled_discovery
from .normalization import (
    apply_europe_pmc,
    apply_openalex,
    apply_unpaywall,
    normalize_crossref,
    normalize_biorxiv,
    normalize_elsevier,
    normalize_europe_pmc,
    normalize_official_guidance,
    normalize_publisher_rss,
    normalize_springer_nature,
)
from .repository import LiteratureRepository
from .types import ArticleCandidate, Classification


logger = get_logger(__name__)
ROOT = Path(__file__).resolve().parents[2]


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return payload


def _parse_checkpoint_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _candidate_key(candidate: ArticleCandidate) -> str:
    if candidate.doi:
        return f"doi:{candidate.doi.strip().lower()}"
    if candidate.pmid:
        return f"pmid:{candidate.pmid.strip()}"
    if candidate.pmcid:
        return f"pmcid:{candidate.pmcid.strip().upper()}"
    if candidate.openalex_id:
        return f"openalex:{candidate.openalex_id.strip().upper()}"
    return f"article:{candidate.article_id}"


def _merge_candidate(primary: ArticleCandidate, incoming: ArticleCandidate) -> None:
    """Fill source gaps without downgrading the earlier, higher-priority record."""
    for attribute in (
        "doi", "pmid", "pmcid", "openalex_id", "journal", "publisher",
        "study_type", "published_at", "indexed_at", "abstract_text",
        "abstract_license", "open_access_url", "license_url",
    ):
        if not getattr(primary, attribute) and getattr(incoming, attribute):
            setattr(primary, attribute, getattr(incoming, attribute))
    if not primary.issn and incoming.issn:
        primary.issn = list(incoming.issn)
    if not primary.authors and incoming.authors:
        primary.authors = list(incoming.authors)
    if primary.open_access_status == "unknown" and incoming.open_access_status != "unknown":
        primary.open_access_status = incoming.open_access_status
    # A dedicated preprint registry is authoritative for the review status of
    # its own DOI. Never let provider priority erase that safety signal.
    if (
        incoming.peer_review_status == "preprint"
        and primary.doi
        and primary.doi == incoming.doi
    ):
        primary.peer_review_status = "preprint"
        primary.article_type = "preprint"
        primary.study_type = "Preprint"
    existing_relations = {
        (
            relation.get("preprint_doi"),
            relation.get("peer_reviewed_doi"),
        )
        for relation in primary.version_relations
    }
    primary.version_relations.extend(
        relation
        for relation in incoming.version_relations
        if (relation.get("preprint_doi"), relation.get("peer_reviewed_doi"))
        not in existing_relations
    )
    discovery_origins: list[dict[str, Any]] = []
    for candidate in (primary, incoming):
        direct = candidate.source_payload.get("_research_radar_discovery")
        if isinstance(direct, dict):
            discovery_origins.append(direct)
        europe_pmc = candidate.source_payload.get("europe_pmc")
        if isinstance(europe_pmc, dict):
            nested = europe_pmc.get("_research_radar_discovery")
            if isinstance(nested, dict):
                discovery_origins.append(nested)
        discovery_origins.extend(
            origin
            for origin in candidate.source_payload.get("controlled_discovery_origins") or []
            if isinstance(origin, dict)
        )
    primary.source_urls = {**incoming.source_urls, **primary.source_urls}
    primary.source_payload = {**incoming.source_payload, **primary.source_payload}
    if discovery_origins:
        by_key = {
            (str(origin.get("provider") or ""), str(origin.get("query_id") or "")): origin
            for origin in discovery_origins
        }
        primary.source_payload["controlled_discovery_origins"] = [
            by_key[key] for key in sorted(by_key)
        ]


def _deduplicate_candidates(candidates: list[ArticleCandidate]) -> tuple[list[ArticleCandidate], int]:
    deduplicated: dict[str, ArticleCandidate] = {}
    duplicates = 0
    for candidate in candidates:
        key = _candidate_key(candidate)
        primary = deduplicated.get(key)
        if primary is None:
            deduplicated[key] = candidate
            continue
        duplicates += 1
        _merge_candidate(primary, candidate)
    return list(deduplicated.values()), duplicates


def _hold_degraded_enrichment_for_review(
    classification: Classification,
    *,
    enrichment_degraded: bool,
) -> bool:
    """Prevent an incompletely enriched candidate from auto-publishing."""
    if not enrichment_degraded or classification.publication_status != "published":
        return False
    classification.publication_status = "review"
    return True


def _hold_preprint_for_review(candidate: ArticleCandidate, classification: Classification) -> bool:
    """Hard gate: discovery can index preprints, but automation cannot publish them."""
    if candidate.peer_review_status != "preprint" or classification.publication_status != "published":
        return False
    classification.publication_status = "review"
    return True


async def _isolate_optional_source(provider: str, request: Awaitable[Any]) -> tuple[Any | None, str | None]:
    """Contain optional-source failures without logging URLs, queries, or credentials."""
    try:
        return await request, None
    except Exception as exc:
        error_type = type(exc).__name__ or "Exception"
        logger.warning(
            "Optional literature discovery failed provider={} error_type={}",
            provider,
            error_type,
        )
        return None, error_type


def _preserve_optional_checkpoint(result: Any | None, previous: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep the last committed cursor when an optional source is skipped or fails."""
    checkpoint = getattr(result, "checkpoint", None) if result is not None else None
    if isinstance(checkpoint, dict):
        return checkpoint
    return previous if isinstance(previous, dict) else None


def _global_country_catalogue(
    country_rows: list[Any],
    taxonomy: dict[str, Any],
) -> list[dict[str, str]]:
    """Merge local surveillance countries with the global ISO research vocabulary."""

    active_country_codes = {str(row.code).upper() for row in country_rows}
    countries_by_code = {
        str(row.code).upper(): {
            "code": str(row.code).upper(),
            "name": str(row.name),
            "name_en": str(row.name_en or row.name),
            "name_zh": str((row.metadata_ or {}).get("name_zh") or ""),
        }
        for row in country_rows
    }
    for iso_country in pycountry.countries:
        code = str(iso_country.alpha_2).upper()
        countries_by_code.setdefault(code, {
            "code": code,
            "name": str(getattr(iso_country, "name", code)),
            "name_en": str(getattr(iso_country, "common_name", getattr(iso_country, "name", code))),
            "name_zh": "",
        })
    for code, aliases in (taxonomy.get("country_aliases") or {}).items():
        normalized = str(code).upper()
        countries_by_code.setdefault(normalized, {
            "code": normalized,
            "name": normalized,
            "name_en": normalized,
            "name_zh": "",
        })
        if normalized not in active_country_codes and aliases:
            preferred_name = str(aliases[0]).strip()
            if preferred_name:
                countries_by_code[normalized]["name"] = preferred_name
                countries_by_code[normalized]["name_en"] = preferred_name
    return [countries_by_code[code] for code in sorted(countries_by_code)]


class LiteraturePipeline:
    def __init__(self, config: Any) -> None:
        self.config = config

    async def execute(self, task: Task | None = None) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        since, resume_after = await self._resolve_start(now, task)
        run_uuid = str(uuid.uuid4())
        await self._create_run(run_uuid, since, now, task=task)
        try:
            journals_payload = _load_json(ROOT / self.config.journals_path)
            journals = [item for item in journals_payload.get("journals") or [] if item.get("issn")]
            crossref = CrossrefClient(
                mailto=self.config.contact_email,
                timeout_seconds=self.config.request_timeout_seconds,
                retries=self.config.max_retries,
            )
            source_result = await crossref.fetch_incremental(
                journals=journals,
                since=since,
                until=now,
                max_records=self.config.max_records_per_run,
                concurrency=self.config.source_concurrency,
                resume_after=resume_after,
            )
            raw_records = source_result.records
            taxonomy = _load_json(ROOT / self.config.taxonomy_path)
            diseases, countries = await self._classification_catalogues()

            controlled_result = None
            controlled_crossref_records: list[dict[str, Any]] = []
            controlled_europe_pmc_records: list[dict[str, Any]] = []
            if getattr(self.config, "controlled_discovery_enabled", False):
                controlled_checkpoint = await self._resolve_nested_checkpoint("controlled_discovery")
                query_batches = build_controlled_query_batches(
                    diseases,
                    taxonomy,
                    max_terms_per_batch=getattr(
                        self.config,
                        "controlled_discovery_max_terms_per_query",
                        8,
                    ),
                )
                controlled_result = await fetch_controlled_discovery(
                    crossref=crossref,
                    europe_pmc=(
                        EuropePmcClient(
                            timeout_seconds=self.config.request_timeout_seconds,
                            retries=self.config.max_retries,
                        )
                        if self.config.europe_pmc_enabled
                        else None
                    ),
                    batches=query_batches,
                    checkpoint=controlled_checkpoint,
                    since=since,
                    until=now,
                    max_queries=getattr(self.config, "controlled_discovery_queries_per_run", 8),
                    records_per_query=getattr(
                        self.config,
                        "controlled_discovery_records_per_query",
                        15,
                    ),
                    max_records=getattr(
                        self.config,
                        "controlled_discovery_max_records_per_run",
                        120,
                    ),
                    concurrency=self.config.source_concurrency,
                )
                controlled_crossref_records = controlled_result.crossref_records
                controlled_europe_pmc_records = controlled_result.europe_pmc_records

            official_guidance_result = None
            official_guidance_records: list[dict[str, Any]] = []
            official_guidance_error: str | None = None
            official_guidance_checkpoint: dict[str, Any] | None = None
            if getattr(self.config, "official_guidance_enabled", False):
                official_guidance_checkpoint = await self._resolve_nested_checkpoint("official_guidance")
                try:
                    official_guidance_result = await OfficialGuidanceOaiClient(
                        endpoint=self.config.official_guidance_oai_endpoint,
                        contact_email=self.config.contact_email,
                        timeout_seconds=self.config.request_timeout_seconds,
                        retries=self.config.max_retries,
                    ).fetch_incremental(
                        since=since,
                        until=now,
                        max_records=self.config.max_official_guidance_records,
                        checkpoint=official_guidance_checkpoint,
                    )
                    official_guidance_records = official_guidance_result.records
                except Exception as exc:
                    official_guidance_error = f"{type(exc).__name__}: {exc}"[:500]
                    logger.warning("WHO IRIS guidance metadata discovery failed: {}", official_guidance_error)

            springer_result = None
            springer_records: list[dict[str, Any]] = []
            springer_error: str | None = None
            springer_skipped_credentials = 0
            springer_checkpoint: dict[str, Any] | None = None
            if getattr(self.config, "springer_nature_enabled", False):
                springer_checkpoint = await self._resolve_nested_checkpoint("springer_nature")
                if not str(getattr(self.config, "springer_nature_api_key", "") or "").strip():
                    springer_skipped_credentials = 1
                    logger.warning("Springer Nature discovery enabled but credential is not configured; skipping")
                else:
                    springer_result, springer_error = await _isolate_optional_source(
                        "springer-nature",
                        SpringerNatureClient(
                            api_key=self.config.springer_nature_api_key,
                            contact_email=self.config.contact_email,
                            timeout_seconds=self.config.request_timeout_seconds,
                            retries=self.config.max_retries,
                        ).search_recent(
                            query=getattr(self.config, "springer_nature_query", ""),
                            since=since,
                            until=now,
                            max_records=getattr(self.config, "max_springer_nature_records", 50),
                            checkpoint=springer_checkpoint,
                        ),
                    )
                    if springer_result is not None:
                        springer_records = springer_result.records

            elsevier_result = None
            elsevier_records: list[dict[str, Any]] = []
            elsevier_error: str | None = None
            elsevier_skipped_credentials = 0
            elsevier_checkpoint: dict[str, Any] | None = None
            if getattr(self.config, "elsevier_enabled", False):
                elsevier_checkpoint = await self._resolve_nested_checkpoint("elsevier")
                if not str(getattr(self.config, "elsevier_api_key", "") or "").strip():
                    elsevier_skipped_credentials = 1
                    logger.warning("Elsevier discovery enabled but credential is not configured; skipping")
                else:
                    elsevier_result, elsevier_error = await _isolate_optional_source(
                        "elsevier",
                        ElsevierClient(
                            api_key=self.config.elsevier_api_key,
                            institutional_token=getattr(self.config, "elsevier_institutional_token", ""),
                            contact_email=self.config.contact_email,
                            timeout_seconds=self.config.request_timeout_seconds,
                            retries=self.config.max_retries,
                        ).search_recent(
                            query=getattr(self.config, "elsevier_query", ""),
                            since=since,
                            until=now,
                            max_records=getattr(self.config, "max_elsevier_records", 50),
                            checkpoint=elsevier_checkpoint,
                        ),
                    )
                    if elsevier_result is not None:
                        elsevier_records = elsevier_result.records

            preprint_result = None
            preprint_records: list[dict[str, Any]] = []
            preprint_error: str | None = None
            preprint_checkpoint: dict[str, Any] | None = None
            if getattr(self.config, "preprint_discovery_enabled", False):
                preprint_checkpoint = await self._resolve_nested_checkpoint("preprints")
                preprint_result, preprint_error = await _isolate_optional_source(
                    "biorxiv-api",
                    BiorxivClient(
                    contact_email=self.config.contact_email,
                    timeout_seconds=self.config.request_timeout_seconds,
                    retries=self.config.max_retries,
                ).fetch_recent(
                    since=since,
                    until=now,
                    max_records=getattr(self.config, "max_preprint_records", 100),
                    checkpoint=preprint_checkpoint,
                ),
                )
                if preprint_result is not None:
                    preprint_records = preprint_result.records

            rss_result = None
            rss_records: list[dict[str, Any]] = []
            if getattr(self.config, "publisher_rss_enabled", False):
                rss_payload = _load_json(ROOT / self.config.publisher_rss_feeds_path)
                if rss_payload.get("schema_version") != 1:
                    raise ValueError("Unsupported publisher RSS whitelist schema_version")
                rss_checkpoint = await self._resolve_rss_checkpoint()
                rss_result = await PublisherRssClient(
                    user_agent=f"GIDS-Research-Radar/1.0 (mailto:{self.config.contact_email})",
                    timeout_seconds=self.config.request_timeout_seconds,
                    retries=self.config.max_retries,
                    max_feed_bytes=self.config.publisher_rss_max_feed_bytes,
                    seen_id_limit=self.config.publisher_rss_seen_id_limit,
                ).fetch_incremental(
                    feeds=[item for item in rss_payload.get("feeds") or [] if isinstance(item, dict)],
                    checkpoint=rss_checkpoint,
                    max_records=self.config.max_publisher_rss_records,
                    concurrency=self.config.publisher_rss_concurrency,
                    now=now,
                )
                rss_records = rss_result.records
            source_candidates = [
                *[candidate for item in raw_records if (candidate := normalize_crossref(item))],
                *[candidate for item in rss_records if (candidate := normalize_publisher_rss(item))],
                *[candidate for item in springer_records if (candidate := normalize_springer_nature(item))],
                *[candidate for item in elsevier_records if (candidate := normalize_elsevier(item))],
                *[candidate for item in preprint_records if (candidate := normalize_biorxiv(item))],
                *[
                    candidate
                    for item in controlled_crossref_records
                    if (candidate := normalize_crossref(item))
                ],
                *[
                    candidate
                    for item in controlled_europe_pmc_records
                    if (candidate := normalize_europe_pmc(item))
                ],
                *[
                    candidate
                    for item in official_guidance_records
                    if (candidate := normalize_official_guidance(item))
                ],
            ]
            candidates, same_batch_duplicates = _deduplicate_candidates(source_candidates)
            if task:
                await task_manager.update_task_progress(task.task_uuid, 35)
                if await task_manager.is_cancel_requested(task.task_uuid):
                    raise RuntimeError("Literature synchronization cancelled")

            enrichment_counts = await self._enrich_candidates(candidates)
            enrichment_degraded = bool(enrichment_counts["enrichment_errors"])
            if task:
                await task_manager.update_task_progress(task.task_uuid, 55)

            inserted = updated = excluded = published = preprints_held_for_review = 0
            async with get_db() as db:
                repository = LiteratureRepository(db)
                for index, candidate in enumerate(candidates):
                    classification = classify_candidate(
                        candidate,
                        diseases=diseases,
                        countries=countries,
                        taxonomy=taxonomy,
                        now=now,
                        auto_publish_min_score=self.config.auto_publish_min_score,
                    )
                    if _hold_degraded_enrichment_for_review(
                        classification,
                        enrichment_degraded=enrichment_degraded,
                    ):
                        enrichment_counts["enrichment_degraded_review"] += 1
                    preprints_held_for_review += int(
                        _hold_preprint_for_review(candidate, classification)
                    )
                    # Autopilot owns publication so that every automatic decision
                    # passes the complete metadata/integrity gate and is audited.
                    if self.config.autopilot_enabled and classification.publication_status == "published":
                        classification.publication_status = "review"
                    was_inserted = await repository.upsert(
                        candidate,
                        classification,
                        preserve_existing_publication_status=enrichment_degraded,
                    )
                    inserted += int(was_inserted)
                    updated += int(not was_inserted)
                    excluded += int(classification.publication_status == "excluded")
                    published += int(classification.publication_status == "published")
                    if task and index % 20 == 0:
                        await task_manager.update_task_progress(
                            task.task_uuid,
                            min(95, 55 + int(40 * (index + 1) / max(1, len(candidates)))),
                        )
                await db.commit()

            automation = None
            # A provider-wide failure means every candidate in this batch may be
            # missing metadata used by the deterministic publication gates.  Do
            # not let the global reconcile immediately undo the review hold; a
            # later healthy run can re-evaluate the same records normally.
            if self.config.autopilot_enabled and not enrichment_degraded:
                from src.services.literature_automation_service import literature_automation_service

                automation = await literature_automation_service.reconcile()

            counts = {
                "fetched": (
                    len(raw_records)
                    + len(rss_records)
                    + len(controlled_crossref_records)
                    + len(controlled_europe_pmc_records)
                    + len(official_guidance_records)
                    + len(springer_records)
                    + len(elsevier_records)
                    + len(preprint_records)
                ),
                "crossref_fetched": len(raw_records) + len(controlled_crossref_records),
                "publisher_rss_fetched": len(rss_records),
                "controlled_discovery_fetched": (
                    len(controlled_crossref_records) + len(controlled_europe_pmc_records)
                ),
                "controlled_discovery_crossref_fetched": len(controlled_crossref_records),
                "controlled_discovery_europe_pmc_fetched": len(controlled_europe_pmc_records),
                "official_guidance_fetched": len(official_guidance_records),
                "official_guidance_records_seen": int(
                    (official_guidance_result.checkpoint if official_guidance_result else {}).get("records_seen") or 0
                ),
                "official_guidance_truncated": int(bool(
                    (official_guidance_result.checkpoint if official_guidance_result else {}).get("truncated")
                )),
                "official_guidance_errors": int(official_guidance_error is not None),
                "springer_nature_fetched": len(springer_records),
                "springer_nature_errors": int(springer_error is not None),
                "springer_nature_skipped_credentials": springer_skipped_credentials,
                "elsevier_fetched": len(elsevier_records),
                "elsevier_errors": int(elsevier_error is not None),
                "elsevier_skipped_credentials": elsevier_skipped_credentials,
                "preprint_fetched": len(preprint_records),
                "preprint_source_errors": int(preprint_error is not None),
                "preprints_held_for_review": preprints_held_for_review,
                "controlled_discovery_queries": len(
                    (controlled_result.checkpoint if controlled_result else {}).get("selected_query_ids") or []
                ),
                "controlled_discovery_query_errors": len(
                    (controlled_result.checkpoint if controlled_result else {}).get("query_errors") or []
                ),
                "normalized": len(candidates),
                "same_batch_duplicates": same_batch_duplicates,
                "inserted": inserted,
                "updated": updated,
                "published": published,
                "requires_review": len(candidates) - excluded - published,
                "excluded": excluded,
                "autopilot_changed": int((automation or {}).get("changed") or 0),
                "autopilot_skipped_degraded_enrichment": int(
                    self.config.autopilot_enabled and enrichment_degraded
                ),
                "source_records_seen": int(source_result.checkpoint.get("records_seen") or len(raw_records)),
                "source_records_returned": int(source_result.checkpoint.get("records_returned") or len(raw_records)),
                "source_records_prefetched": int(
                    source_result.checkpoint.get("records_prefetched") or len(raw_records)
                ),
                "source_lookahead_records": int(
                    source_result.checkpoint.get("lookahead_records") or 0
                ),
                "source_pages_fetched": int(
                    source_result.checkpoint.get("pages_fetched") or 0
                ),
                "source_truncated": int(bool(source_result.checkpoint.get("truncated"))),
                "source_catch_up_required": int(
                    bool(source_result.checkpoint.get("catch_up_required"))
                ),
                "source_remaining_index_span_seconds": int(
                    source_result.checkpoint.get("remaining_index_span_seconds") or 0
                ),
                "publisher_rss_records_seen": int((rss_result.checkpoint if rss_result else {}).get("records_seen") or 0),
                "publisher_rss_feeds_modified": int((rss_result.checkpoint if rss_result else {}).get("feeds_modified") or 0),
                "publisher_rss_feeds_not_modified": int((rss_result.checkpoint if rss_result else {}).get("feeds_not_modified") or 0),
                "publisher_rss_feed_errors": len((rss_result.checkpoint if rss_result else {}).get("feed_errors") or []),
                "publisher_rss_truncated": int(bool((rss_result.checkpoint if rss_result else {}).get("truncated"))),
                **enrichment_counts,
            }
            through_indexed_at = _parse_checkpoint_datetime(source_result.checkpoint.get("through_indexed_at")) or now
            springer_next_checkpoint = _preserve_optional_checkpoint(
                springer_result,
                springer_checkpoint,
            )
            elsevier_next_checkpoint = _preserve_optional_checkpoint(
                elsevier_result,
                elsevier_checkpoint,
            )
            preprint_next_checkpoint = _preserve_optional_checkpoint(
                preprint_result,
                preprint_checkpoint,
            )
            checkpoint = {
                **source_result.checkpoint,
                **({"rss": rss_result.checkpoint} if rss_result else {}),
                **(
                    {"controlled_discovery": controlled_result.checkpoint}
                    if controlled_result
                    else {}
                ),
                **(
                    {"official_guidance": official_guidance_result.checkpoint}
                    if official_guidance_result
                    else ({"official_guidance": official_guidance_checkpoint} if official_guidance_checkpoint else {})
                ),
                **(
                    {"springer_nature": springer_next_checkpoint}
                    if springer_next_checkpoint
                    else {}
                ),
                **(
                    {"elsevier": elsevier_next_checkpoint}
                    if elsevier_next_checkpoint
                    else {}
                ),
                **(
                    {"preprints": preprint_next_checkpoint}
                    if preprint_next_checkpoint
                    else {}
                ),
            }
            await self._finish_run(
                run_uuid,
                "completed",
                counts=counts,
                checkpoint=checkpoint,
                through_indexed_at=through_indexed_at,
            )
            if task:
                await task_manager.update_task_progress(task.task_uuid, 100)
            return {
                "run_uuid": run_uuid,
                "from_indexed_at": since.isoformat(),
                "through_indexed_at": through_indexed_at.isoformat(),
                **counts,
                "automation": automation,
            }
        except Exception as exc:
            await self._finish_run(run_uuid, "failed", error=type(exc).__name__ or "Exception")
            raise

    async def _enrich_candidates(self, candidates: list[ArticleCandidate]) -> dict[str, Any]:
        """Apply optional enrichers independently without blocking core ingestion."""
        dois = list(dict.fromkeys(candidate.doi for candidate in candidates if candidate.doi))
        counts: dict[str, Any] = {
            "europe_pmc_enriched": 0,
            "unpaywall_enriched": 0,
            "openalex_enriched": 0,
            "europe_pmc_errors": 0,
            "unpaywall_errors": 0,
            "openalex_errors": 0,
            "enrichment_errors": 0,
            "enrichment_failed_providers": [],
            "enrichment_degraded_review": 0,
        }

        def record_failure(provider: str, exc: Exception) -> None:
            counts[f"{provider}_errors"] = 1
            counts["enrichment_errors"] += 1
            counts["enrichment_failed_providers"].append(provider.replace("_", "-"))
            logger.warning(
                "Literature metadata enrichment failed provider={} error_type={}",
                provider.replace("_", "-"),
                type(exc).__name__ or "Exception",
            )

        if self.config.europe_pmc_enabled:
            try:
                enrichment = await EuropePmcClient(
                    timeout_seconds=self.config.request_timeout_seconds,
                    retries=self.config.max_retries,
                ).enrich_by_dois(dois[: self.config.max_europe_pmc_records])
                counts["europe_pmc_enriched"] = len(enrichment)
                for candidate in candidates:
                    if candidate.doi and candidate.doi in enrichment:
                        apply_europe_pmc(candidate, enrichment[candidate.doi])
            except Exception as exc:
                record_failure("europe_pmc", exc)

        # Unpaywall is the dedicated legal-OA source, so it gets first chance
        # to fill OA gaps after the two core sources.
        if getattr(self.config, "unpaywall_enabled", False):
            try:
                enrichment = await UnpaywallClient(
                    email=self.config.contact_email,
                    timeout_seconds=self.config.request_timeout_seconds,
                    retries=self.config.max_retries,
                ).enrich_by_dois(
                    dois[: self.config.max_unpaywall_records],
                    concurrency=self.config.metadata_enrichment_concurrency,
                    min_interval_seconds=self.config.metadata_enrichment_min_interval_seconds,
                )
                counts["unpaywall_enriched"] = len(enrichment)
                for candidate in candidates:
                    if candidate.doi and candidate.doi in enrichment:
                        apply_unpaywall(candidate, enrichment[candidate.doi])
            except Exception as exc:
                record_failure("unpaywall", exc)

        if getattr(self.config, "openalex_enabled", False):
            try:
                enrichment = await OpenAlexClient(
                    mailto=self.config.contact_email,
                    api_key=self.config.openalex_api_key,
                    timeout_seconds=self.config.request_timeout_seconds,
                    retries=self.config.max_retries,
                ).enrich_by_dois(
                    dois[: self.config.max_openalex_records],
                    batch_size=self.config.openalex_batch_size,
                    concurrency=self.config.metadata_enrichment_concurrency,
                    min_interval_seconds=self.config.metadata_enrichment_min_interval_seconds,
                )
                counts["openalex_enriched"] = len(enrichment)
                for candidate in candidates:
                    if candidate.doi and candidate.doi in enrichment:
                        apply_openalex(candidate, enrichment[candidate.doi])
            except Exception as exc:
                record_failure("openalex", exc)

        return counts

    async def _resolve_start(
        self,
        now: datetime,
        task: Task | None,
    ) -> tuple[datetime, dict[str, Any] | None]:
        requested = (task.input_data or {}).get("since") if task else None
        if requested:
            parsed = datetime.fromisoformat(str(requested).replace("Z", "+00:00"))
            since = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            return since, None
        async with get_db() as db:
            latest = (
                await db.execute(
                    select(LiteratureIngestRun)
                    .where(
                        LiteratureIngestRun.status == "completed",
                        LiteratureIngestRun.source.like("crossref%"),
                    )
                    .order_by(
                        LiteratureIngestRun.completed_at.desc(),
                        LiteratureIngestRun.id.desc(),
                        LiteratureIngestRun.through_indexed_at.desc(),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
        if latest and latest.through_indexed_at:
            checkpoint = latest.checkpoint or {}
            if checkpoint.get("truncated"):
                next_from = _parse_checkpoint_datetime(checkpoint.get("next_from_indexed_at"))
                if next_from is not None:
                    resume_after = checkpoint.get("resume_after")
                    return next_from, resume_after if isinstance(resume_after, dict) else None
            value = latest.through_indexed_at
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            # Crossref's index date is the update watermark, and the inclusive
            # second-resolution request boundary already provides a minimal
            # replay. Reopening a multi-day overlap here can contain many times
            # the global cap and create a permanent duplicate catch-up loop.
            return value, None
        return now - timedelta(days=self.config.initial_lookback_days), None

    async def _resolve_since(self, now: datetime, task: Task | None) -> datetime:
        """Compatibility wrapper for callers that only need the start time."""
        since, _ = await self._resolve_start(now, task)
        return since

    async def _resolve_rss_checkpoint(self) -> dict[str, Any] | None:
        """Load the last committed conditional-feed state independently of Crossref."""
        return await self._resolve_nested_checkpoint("rss")

    async def _resolve_nested_checkpoint(self, key: str) -> dict[str, Any] | None:
        """Load a committed secondary-source checkpoint independently of Crossref."""
        async with get_db() as db:
            latest = (
                await db.execute(
                    select(LiteratureIngestRun)
                    .where(
                        LiteratureIngestRun.status == "completed",
                        LiteratureIngestRun.source.like("crossref%"),
                    )
                    .order_by(
                        LiteratureIngestRun.completed_at.desc(),
                        LiteratureIngestRun.id.desc(),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
        checkpoint = (latest.checkpoint or {}).get(key) if latest else None
        return checkpoint if isinstance(checkpoint, dict) else None

    async def _classification_catalogues(self) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        alias_payload = _load_json(ROOT / self.config.disease_aliases_path)
        aliases_by_id = alias_payload.get("aliases") or {}
        taxonomy = _load_json(ROOT / self.config.taxonomy_path)
        async with get_db() as db:
            disease_rows = (
                await db.execute(select(StandardDisease).where(StandardDisease.is_active.is_(True)))
            ).scalars().all()
            country_rows = (
                await db.execute(select(Country).where(Country.is_active.is_(True)))
            ).scalars().all()
        diseases = [
            {
                "disease_id": row.disease_id,
                "name_en": row.standard_name_en,
                "name_zh": row.standard_name_zh,
                "aliases": [
                    *[str(value) for value in aliases_by_id.get(row.disease_id, [])],
                    *[str(value) for value in (row.metadata_ or {}).get("aliases", [])],
                ],
            }
            for row in disease_rows
        ]
        # Research metadata is global even when the surveillance catalogue is
        # currently enabled for only a subset of countries.  Build a complete
        # ISO geography vocabulary so a DRC paper, for example, is not reduced
        # to disease-only context merely because CD has no local case series.
        # Explicit ambiguity-safe aliases remain part of the reviewed taxonomy;
        # `_country_terms` applies those aliases and short-code safeguards.
        countries = _global_country_catalogue(country_rows, taxonomy)
        return diseases, countries

    async def _create_run(
        self,
        run_uuid: str,
        since: datetime,
        through: datetime,
        *,
        task: Task | None = None,
    ) -> None:
        sources = ["crossref"]
        if self.config.europe_pmc_enabled:
            sources.append("europe-pmc")
        if getattr(self.config, "openalex_enabled", False):
            sources.append("openalex")
        if getattr(self.config, "unpaywall_enabled", False):
            sources.append("unpaywall")
        if getattr(self.config, "publisher_rss_enabled", False):
            sources.append("publisher-rss")
        if getattr(self.config, "springer_nature_enabled", False):
            sources.append("springer-nature")
        if getattr(self.config, "elsevier_enabled", False):
            sources.append("elsevier")
        if getattr(self.config, "preprint_discovery_enabled", False):
            sources.append("biorxiv-api")
        if getattr(self.config, "official_guidance_enabled", False):
            sources.append("who-iris-oai")
        if getattr(self.config, "controlled_discovery_enabled", False):
            sources.append("controlled-query")
        async with get_db() as db:
            db.add(LiteratureIngestRun(
                run_uuid=run_uuid,
                task_uuid=task.task_uuid if task is not None else None,
                source="+".join(sources),
                status="running",
                started_at=datetime.now(timezone.utc),
                from_indexed_at=since,
                through_indexed_at=through,
                checkpoint={
                    "strategy": "index-date",
                    "overlap_days": 0,
                    "configured_legacy_overlap_days": self.config.index_overlap_days,
                    "task_uuid": task.task_uuid if task is not None else None,
                },
                counts={},
            ))
            await db.commit()

    async def _finish_run(
        self,
        run_uuid: str,
        status: str,
        *,
        counts: dict[str, Any] | None = None,
        checkpoint: dict[str, Any] | None = None,
        through_indexed_at: datetime | None = None,
        error: str | None = None,
    ) -> None:
        async with get_db() as db:
            run = (
                await db.execute(select(LiteratureIngestRun).where(LiteratureIngestRun.run_uuid == run_uuid))
            ).scalar_one()
            run.status = status
            run.completed_at = datetime.now(timezone.utc)
            if through_indexed_at is not None:
                run.through_indexed_at = through_indexed_at
            if checkpoint is not None:
                # Preserve the immutable ownership marker when replacing the
                # provisional checkpoint with the provider cursor at finish.
                task_uuid = run.task_uuid or (run.checkpoint or {}).get("task_uuid")
                run.checkpoint = {
                    **checkpoint,
                    **({"task_uuid": task_uuid} if task_uuid else {}),
                }
            run.counts = counts or {}
            run.error = error
            await db.commit()


__all__ = ["LiteraturePipeline"]
