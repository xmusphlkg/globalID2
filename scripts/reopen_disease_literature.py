#!/usr/bin/env python3
"""Requeue historical exclusions with strong disease evidence, in bounded batches."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import func, select
from src.core.database import get_db
from src.domain import LiteratureArticle, LiteratureDiseaseLink, LiteratureStatusEvent


async def run(*, apply: bool, limit: int, after_id: int, journals: list[str], confidence: float,
              dois: list[str] | None = None):
    if limit < 1 or limit > 1000 or after_id < 0 or not 0 <= confidence <= 1:
        raise ValueError("invalid batch size, cursor or confidence")
    disease_confidence = (
        select(LiteratureDiseaseLink.article_id, func.max(LiteratureDiseaseLink.confidence).label("confidence"))
        .group_by(LiteratureDiseaseLink.article_id).subquery()
    )
    async with get_db() as db:
        statement = (
            select(LiteratureArticle, disease_confidence.c.confidence)
            .join(disease_confidence, disease_confidence.c.article_id == LiteratureArticle.article_id)
            .where(LiteratureArticle.id > after_id, LiteratureArticle.publication_status == "excluded",
                   LiteratureArticle.integrity_status == "current",
                   disease_confidence.c.confidence >= confidence)
            .order_by(LiteratureArticle.id).limit(limit)
        )
        if journals:
            statement = statement.where(LiteratureArticle.journal.in_(journals))
        if dois:
            statement = statement.where(LiteratureArticle.doi.in_([doi.strip().lower() for doi in dois]))
        if apply:
            statement = statement.with_for_update(of=LiteratureArticle)
        rows = (await db.execute(statement)).all()
        reopened = skipped_locked = 0
        examples = []
        for article, disease_score in rows:
            metadata = dict(article.metadata_ or {})
            if metadata.get("editorial_locked"):
                skipped_locked += 1
                continue
            if len(examples) < 10:
                examples.append({"article_id": article.article_id, "doi": article.doi,
                                 "title": article.title, "disease_confidence": disease_score})
            reopened += 1
            if not apply:
                continue
            old_automation = metadata.pop("autopilot", None)
            old_governance = metadata.pop("ai_content_governance", None)
            metadata["disease_recovery"] = {
                "version": "disease-recovery.v1", "reopened_at": datetime.now(timezone.utc).isoformat(),
                "disease_confidence": disease_score, "previous_autopilot": old_automation,
                "previous_ai_content_governance": old_governance,
            }
            article.metadata_ = metadata
            article.publication_status = "review"
            db.add(LiteratureStatusEvent(
                article_id=article.article_id, event_type="publication_status_changed",
                previous_status="excluded", current_status="review", source="disease-recovery.v1",
                effective_at=datetime.now(timezone.utc),
                metadata_={"reason": "strong disease link warrants renewed review", "disease_confidence": disease_score},
            ))
        return {"dry_run": not apply, "examined": len(rows), "reopened": reopened,
                "editorial_locked_skipped": skipped_locked,
                "next_after_id": int(rows[-1][0].id) if rows else after_id, "examples": examples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--after-id", type=int, default=0)
    parser.add_argument("--min-confidence", type=float, default=0.82)
    parser.add_argument("--journal", action="append", default=[])
    parser.add_argument("--doi", action="append", default=[])
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(apply=args.apply, limit=args.limit, after_id=args.after_id,
                                 journals=args.journal, confidence=args.min_confidence, dois=args.doi)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
