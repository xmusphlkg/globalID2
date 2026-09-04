#!/usr/bin/env python3
"""Audit or apply PubMed EFetch abstract backfill for stored literature."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import sys
from typing import Any, Iterator, TextIO

from sqlalchemy import func, or_, select


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.config import get_config  # noqa: E402
from src.core.database import get_db  # noqa: E402
from src.domain import LiteratureArticle  # noqa: E402
from src.literature.clients import PubMedClient  # noqa: E402
from src.literature.normalization import compact_text  # noqa: E402


APPLY_LOCK_PATH = ROOT / "data/cache/literature_pubmed_abstract_backfill.lock"
DEFAULT_STATUSES = ("published", "review")


class ConcurrentApplyError(RuntimeError):
    """Raised when a second PubMed abstract writer is already active."""


@contextmanager
def _exclusive_apply_lock(path: Path = APPLY_LOCK_PATH) -> Iterator[TextIO]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ConcurrentApplyError(
                "another PubMed abstract backfill --apply process is already running"
            ) from exc
        yield handle
    finally:
        handle.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill detailed PubMed EFetch abstracts for PMID-bearing Research Radar records. "
            "The default is dry-run; pass --apply to write."
        )
    )
    parser.add_argument("--apply", action="store_true", help="Persist abstract changes")
    parser.add_argument("--limit", type=int, default=200, help="Maximum records examined")
    parser.add_argument("--batch-size", type=int, default=100, help="PubMed EFetch batch size")
    parser.add_argument(
        "--min-abstract-characters",
        type=int,
        default=None,
        help="Only backfill records shorter than this length (defaults to configured AI minimum)",
    )
    parser.add_argument(
        "--status",
        action="append",
        choices=("published", "review", "excluded"),
        help="Publication status to include; repeatable. Defaults to published and review.",
    )
    return parser


def _abstract_projection(article: LiteratureArticle) -> dict[str, Any]:
    return {
        "abstract_text": article.abstract_text,
        "abstract_license": article.abstract_license,
        "source_urls": dict(article.source_urls or {}),
        "source_payload": dict(article.source_payload or {}),
    }


def _apply_pubmed_payload(article: LiteratureArticle, payload: dict[str, Any]) -> bool:
    abstract = compact_text(payload.get("abstractText"))
    if not abstract or len(abstract) <= len(article.abstract_text or ""):
        return False
    article.abstract_text = abstract
    if not article.abstract_license:
        article.abstract_license = "PubMed abstract metadata"
    article.source_urls = {
        **dict(article.source_urls or {}),
        "pubmed": f"https://pubmed.ncbi.nlm.nih.gov/{article.pmid}/",
    }
    article.source_payload = {
        **dict(article.source_payload or {}),
        "pubmed_efetch": payload,
    }
    return True


async def _main(args: argparse.Namespace) -> dict[str, Any]:
    cfg = get_config().literature
    minimum = args.min_abstract_characters or int(cfg.ai_min_abstract_characters)
    statuses = tuple(args.status or DEFAULT_STATUSES)
    started_at = datetime.now(timezone.utc)
    stats: dict[str, Any] = {
        "mode": "apply" if args.apply else "dry_run",
        "status": "running",
        "limit": args.limit,
        "batch_size": args.batch_size,
        "min_abstract_characters": minimum,
        "publication_statuses": list(statuses),
        "examined": 0,
        "requested_pmids": 0,
        "matched_pmids": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped_without_pubmed_abstract": 0,
        "sample_updated_article_ids": [],
        "started_at": started_at.isoformat(),
    }
    query = (
        select(LiteratureArticle.id, LiteratureArticle.article_id, LiteratureArticle.pmid)
        .where(
            LiteratureArticle.pmid.is_not(None),
            LiteratureArticle.pmid != "",
            LiteratureArticle.publication_status.in_(statuses),
            LiteratureArticle.integrity_status == "current",
            or_(
                LiteratureArticle.abstract_text.is_(None),
                func.length(LiteratureArticle.abstract_text) < minimum,
            ),
        )
        .order_by(LiteratureArticle.published_at.desc().nullslast(), LiteratureArticle.discovery_score.desc())
        .limit(args.limit)
    )
    async with get_db() as db:
        rows = list((await db.execute(query)).mappings().all())
    stats["examined"] = len(rows)
    pmids = list(dict.fromkeys(str(row["pmid"]).strip() for row in rows if str(row["pmid"]).strip()))
    stats["requested_pmids"] = len(pmids)
    if not pmids:
        stats["status"] = "completed"
        stats["completed_at"] = datetime.now(timezone.utc).isoformat()
        return stats

    client = PubMedClient(
        contact_email=cfg.contact_email,
        api_key=getattr(cfg, "pubmed_api_key", ""),
        tool=getattr(cfg, "pubmed_tool", "GIDSResearchRadar"),
        timeout_seconds=cfg.request_timeout_seconds,
        retries=cfg.max_retries,
        min_interval_seconds=getattr(cfg, "pubmed_min_interval_seconds", 0.34),
    )
    abstracts = await client.fetch_abstracts(pmids, batch_size=args.batch_size)
    stats["matched_pmids"] = len(abstracts)
    article_ids = [int(row["id"]) for row in rows]
    async with get_db() as db:
        articles = list(
            (
                await db.execute(
                    select(LiteratureArticle)
                    .where(LiteratureArticle.id.in_(article_ids))
                    .order_by(LiteratureArticle.id)
                )
            )
            .scalars()
            .all()
        )
        for article in articles:
            before = _abstract_projection(article)
            payload = abstracts.get(str(article.pmid or "").strip())
            if not payload:
                stats["skipped_without_pubmed_abstract"] += 1
                stats["unchanged"] += 1
                continue
            changed = _apply_pubmed_payload(article, payload)
            if changed and before != _abstract_projection(article):
                stats["updated"] += 1
                if len(stats["sample_updated_article_ids"]) < 20:
                    stats["sample_updated_article_ids"].append(article.article_id)
                if not args.apply:
                    article.abstract_text = before["abstract_text"]
                    article.abstract_license = before["abstract_license"]
                    article.source_urls = before["source_urls"]
                    article.source_payload = before["source_payload"]
            else:
                stats["unchanged"] += 1
        if not args.apply:
            await db.rollback()
    stats["status"] = "completed"
    stats["completed_at"] = datetime.now(timezone.utc).isoformat()
    return stats


if __name__ == "__main__":
    parser = _parser()
    arguments = parser.parse_args()
    if arguments.limit < 1:
        parser.error("--limit must be at least 1")
    if arguments.batch_size < 1 or arguments.batch_size > 500:
        parser.error("--batch-size must be between 1 and 500")
    if arguments.min_abstract_characters is not None and arguments.min_abstract_characters < 1:
        parser.error("--min-abstract-characters must be at least 1")
    try:
        if arguments.apply:
            with _exclusive_apply_lock():
                result = asyncio.run(_main(arguments))
        else:
            result = asyncio.run(_main(arguments))
    except ConcurrentApplyError as exc:
        parser.exit(2, f"PubMed abstract backfill refused: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
