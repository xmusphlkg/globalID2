"""Bounded repair of missing review abstracts from indexed metadata sources."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, exists, func, or_, select

from src.core.config import get_config
from src.core.database import get_db
from src.domain import LiteratureArticle, LiteratureDiseaseLink, LiteratureStatusEvent
from src.literature.clients import EuropePmcClient, PubMedClient
from src.literature.normalization import compact_text
from src.literature.payloads import (
    SOURCE_PAYLOAD_SCHEMA_VERSION,
    compact_source_payload,
)

SPECIALTY_JOURNALS = (
    "the lancet infectious diseases",
    "the lancet microbe",
    "clinical infectious diseases",
    "journal of infection",
    "bmc infectious diseases",
    "clinical microbiology and infection",
    "emerging infectious diseases",
    "epidemiology and infection",
    "emerging microbes & infections",
    "international journal of infectious diseases",
    "infectious diseases of poverty",
    "plos neglected tropical diseases",
    "vaccine",
    "npj vaccines",
    "eurosurveillance",
)

MINIMUM = 80  # The article governance evidence threshold, not the summary threshold.


def eligible_query(retry_hours: int):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=retry_hours)).isoformat()
    checked = LiteratureArticle.metadata_["abstract_repair"]["checked_at"].as_string()
    return (
        select(LiteratureArticle.id, LiteratureArticle.pmid, LiteratureArticle.doi)
        .where(
            LiteratureArticle.publication_status == "review",
            LiteratureArticle.integrity_status == "current",
            LiteratureArticle.peer_review_status == "peer_reviewed",
            func.coalesce(
                LiteratureArticle.metadata_["editorial_locked"].as_boolean(), False
            ).is_(False),
            func.length(func.trim(func.coalesce(LiteratureArticle.abstract_text, "")))
            < MINIMUM,
            or_(
                func.length(func.trim(func.coalesce(LiteratureArticle.pmid, ""))) > 0,
                func.length(func.trim(func.coalesce(LiteratureArticle.doi, ""))) > 0,
            ),
            or_(checked.is_(None), checked < cutoff),
        )
        .order_by(
            # Never-checked rows move ahead of expired retries, preventing starvation.
            case((checked.is_(None), 0), else_=1),
            case(
                (
                    func.lower(func.coalesce(LiteratureArticle.journal, "")).in_(
                        SPECIALTY_JOURNALS
                    ),
                    0,
                ),
                else_=1,
            ),
            case(
                (
                    exists(
                        select(1).where(
                            LiteratureDiseaseLink.article_id
                            == LiteratureArticle.article_id
                        )
                    ),
                    0,
                ),
                else_=1,
            ),
            LiteratureArticle.published_at.desc().nullslast(),
            LiteratureArticle.id.asc(),
        )
    )


async def repair_abstracts(
    *, limit: int = 200, retry_hours: int = 168, apply: bool = False
) -> dict:
    if limit < 1 or retry_hours < 1:
        raise ValueError("limit and retry_hours must be positive")
    cfg = get_config().literature
    async with get_db() as db:
        rows = (await db.execute(eligible_query(retry_hours).limit(limit))).all()
    stats = {
        "examined": len(rows),
        "pubmed_usable": 0,
        "europe_pmc_usable": 0,
        "filled": 0,
        "would_fill": 0,
        "still_missing": 0,
    }
    if not rows:
        return stats
    pmids = list(
        dict.fromkeys(
            str(r.pmid).strip() for r in rows if r.pmid and str(r.pmid).strip()
        )
    )
    pubmed = PubMedClient(
        contact_email=cfg.contact_email,
        api_key=cfg.pubmed_api_key,
        tool=cfg.pubmed_tool,
        timeout_seconds=cfg.request_timeout_seconds,
        retries=cfg.max_retries,
        min_interval_seconds=cfg.pubmed_min_interval_seconds,
    )
    found = await pubmed.fetch_abstracts(pmids, batch_size=100) if pmids else {}
    dois = list(
        dict.fromkeys(
            str(r.doi).strip().lower()
            for r in rows
            if r.doi
            and str(r.doi).strip()
            and len(
                compact_text(
                    (found.get(str(r.pmid or "").strip()) or {}).get("abstractText")
                )
            )
            < MINIMUM
        )
    )
    europe = EuropePmcClient(
        timeout_seconds=cfg.request_timeout_seconds, retries=cfg.max_retries
    )
    epmc = {}
    for offset in range(0, len(dois), 20):
        if offset:
            await asyncio.sleep(0.35)
        epmc.update(
            await europe.enrich_by_dois(dois[offset : offset + 20], batch_size=20)
        )
    choices = {}
    for row in rows:
        payload = found.get(str(row.pmid or "").strip()) or {}
        source = "pubmed_efetch"
        if len(compact_text(payload.get("abstractText"))) < MINIMUM:
            payload = epmc.get(str(row.doi or "").strip().lower()) or {}
            source = "europe_pmc"
        if len(compact_text(payload.get("abstractText"))) >= MINIMUM:
            choices[row.id] = (source, payload)
            stats[
                "pubmed_usable" if source == "pubmed_efetch" else "europe_pmc_usable"
            ] += 1
    if not apply:
        stats["would_fill"] = len(choices)
        stats["still_missing"] = len(rows) - len(choices)
        return stats
    # Recheck under row locks after network I/O: never overwrite a concurrent edit.
    async with get_db() as db:
        articles = (
            await db.execute(
                eligible_query(retry_hours)
                .order_by(None)
                .where(LiteratureArticle.id.in_([r.id for r in rows]))
                .with_for_update(skip_locked=True)
            )
        ).all()
        ids = [row.id for row in articles]
        locked = (
            (
                await db.execute(
                    select(LiteratureArticle)
                    .where(LiteratureArticle.id.in_(ids))
                    .order_by(LiteratureArticle.id)
                )
            )
            .scalars()
            .all()
        )
        now = datetime.now(timezone.utc)
        for article in locked:
            metadata = dict(article.metadata_ or {})
            choice = choices.get(article.id)
            if choice:
                source, payload = choice
                abstract = compact_text(payload.get("abstractText"))
                article.abstract_text = abstract
                article.abstract_license = article.abstract_license or (
                    "PubMed abstract metadata"
                    if source == "pubmed_efetch"
                    else "Europe PMC abstract metadata"
                )
                article.source_payload = compact_source_payload(
                    {**dict(article.source_payload or {}), source: payload}
                )
                article.source_payload_version = SOURCE_PAYLOAD_SCHEMA_VERSION
                article.source_payload_compacted_at = now
                if article.pmid:
                    article.source_urls = {
                        **dict(article.source_urls or {}),
                        "pubmed": f"https://pubmed.ncbi.nlm.nih.gov/{article.pmid}/",
                    }
                autopilot = metadata.get("autopilot") or {}
                autopilot = autopilot if isinstance(autopilot, dict) else {}
                reasons = autopilot.get("reasons") or []
                if (
                    autopilot.get("decision") == "defer"
                    and reasons
                    and reasons[0] == "awaiting abstract context"
                ):
                    metadata.pop("autopilot", None)
                metadata["abstract_repair"] = {
                    "source": source,
                    "filled_at": now.isoformat(),
                    "abstract_characters": len(abstract),
                }
                db.add(
                    LiteratureStatusEvent(
                        article_id=article.article_id,
                        event_type="abstract_filled",
                        previous_status="review",
                        current_status="review",
                        source="literature-abstract-repair",
                        effective_at=now,
                        metadata_={
                            "source": source,
                            "abstract_characters": len(abstract),
                        },
                    )
                )
                stats["filled"] += 1
            else:
                metadata["abstract_repair"] = {"checked_at": now.isoformat()}
                stats["still_missing"] += 1
            article.metadata_ = metadata
    return stats
