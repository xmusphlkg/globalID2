"""Incremental storage maintenance for bounded Research Radar metadata."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Load

from src.core.database import get_db
from src.domain import LiteratureArticle

from .payloads import SOURCE_PAYLOAD_SCHEMA_VERSION, compact_source_payload


def _payload_bytes(value: Any) -> int:
    return len(
        json.dumps(
            value if isinstance(value, dict) else {},
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    )


async def compact_source_payload_batch(
    *,
    batch_size: int = 1_000,
    dry_run: bool = False,
) -> dict[str, int]:
    if not 1 <= int(batch_size) <= 10_000:
        raise ValueError("batch_size must be between 1 and 10000")
    async with get_db() as db:
        statement = (
            select(LiteratureArticle)
            .options(
                Load(LiteratureArticle).load_only(
                    LiteratureArticle.id,
                    LiteratureArticle.article_id,
                    LiteratureArticle.source_payload,
                    LiteratureArticle.source_payload_version,
                    LiteratureArticle.source_payload_compacted_at,
                )
            )
            .where(
                LiteratureArticle.source_payload_version
                < SOURCE_PAYLOAD_SCHEMA_VERSION
            )
            .order_by(LiteratureArticle.id)
            .limit(int(batch_size))
        )
        # Lock each selected row before reading its JSON so a concurrent ingest
        # cannot be overwritten by a compaction based on stale provider data.
        if not dry_run:
            statement = statement.with_for_update(skip_locked=True)
        articles = (await db.execute(statement)).scalars().all()
        before_bytes = 0
        after_bytes = 0
        changed = 0
        compacted_at = datetime.now(timezone.utc)
        for article in articles:
            before_bytes += _payload_bytes(article.source_payload)
            compacted = compact_source_payload(article.source_payload)
            after_bytes += _payload_bytes(compacted)
            changed += int(
                compacted != (article.source_payload or {})
                or article.source_payload_version != SOURCE_PAYLOAD_SCHEMA_VERSION
            )
            if dry_run:
                continue
            article.source_payload = compacted
            article.source_payload_version = SOURCE_PAYLOAD_SCHEMA_VERSION
            article.source_payload_compacted_at = compacted_at
        if not dry_run:
            await db.commit()
    return {
        "scanned": len(articles),
        "compacted": changed,
        "before_bytes": before_bytes,
        "after_bytes": after_bytes,
        "saved_bytes": max(0, before_bytes - after_bytes),
        "remaining_hint": int(len(articles) == int(batch_size)),
    }


__all__ = ["compact_source_payload_batch"]
