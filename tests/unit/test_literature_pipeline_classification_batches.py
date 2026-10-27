from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from src.literature import pipeline
from src.literature.types import ArticleCandidate, Classification


def _candidate(article_id: str, doi: str) -> ArticleCandidate:
    return ArticleCandidate(
        article_id=article_id,
        slug=article_id,
        title=article_id,
        doi=doi,
    )


async def test_classification_batches_keep_event_loop_responsive_and_stable(
    monkeypatch,
):
    candidates = [
        _candidate("article-c", "10.1000/c"),
        _candidate("article-a", "10.1000/a"),
        _candidate("article-b", "10.1000/b"),
    ]
    classified_ids: list[str] = []

    def classify(candidate, **_kwargs):
        time.sleep(0.02)
        classified_ids.append(candidate.article_id)
        return Classification()

    monkeypatch.setattr(pipeline, "classify_candidate", classify)
    batch_updates: list[tuple[int, int]] = []

    async def after_batch(completed: int, total: int) -> None:
        batch_updates.append((completed, total))

    operation = asyncio.create_task(
        pipeline._classify_candidates_in_batches(
            candidates,
            diseases=[],
            countries=[],
            taxonomy={},
            now=datetime.now(timezone.utc),
            auto_publish_min_score=0.8,
            batch_size=1,
            after_batch=after_batch,
        )
    )
    event_loop_ticks = 0
    while not operation.done():
        event_loop_ticks += 1
        await asyncio.sleep(0.001)

    results = await operation

    assert [candidate.article_id for candidate, _ in results] == [
        "article-a",
        "article-b",
        "article-c",
    ]
    assert classified_ids == ["article-a", "article-b", "article-c"]
    assert batch_updates == [(1, 3), (2, 3), (3, 3)]
    assert event_loop_ticks >= 5
