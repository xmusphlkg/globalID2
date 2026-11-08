"""A failed or cancelled enrichment must drain all article work first."""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import TimeoutError as PoolTimeout

from src.literature import enrichment


@pytest.fixture
def pipeline_setup(monkeypatch):
    config = SimpleNamespace(
        ai_enrichment_enabled=True,
        ai_enrichment_languages=["en"],
        ai_enrichment_batch_size=2,
        ai_model_request_timeout_seconds=1,
        autopilot_enabled=False,
    )
    pipeline = enrichment.LiteratureEnrichmentPipeline(config)
    articles = [SimpleNamespace(article_id="first"), SimpleNamespace(article_id="second")]
    context = {
        article.article_id: {
            "canonical_en": None,
            "diseases": [],
            "countries": [],
            "topics": [],
            "existing_languages": [],
        }
        for article in articles
    }

    async def load_articles(**_kwargs):
        return articles, context

    async def no_route_preferences():
        return []

    async def no_skip(*_args, **_kwargs):
        return False

    async def permits():
        return 2

    async def not_cancelled(_task_uuid):
        return False

    monkeypatch.setattr(pipeline, "_load_articles", load_articles)
    monkeypatch.setattr(pipeline, "_load_route_shard_preferences", no_route_preferences)
    monkeypatch.setattr(pipeline, "_should_skip", no_skip)
    monkeypatch.setattr(enrichment, "live_model_permits", permits)
    monkeypatch.setattr(enrichment.task_manager, "is_cancel_requested", not_cancelled)
    return pipeline, SimpleNamespace(task_uuid="test-enrichment", input_data={})


@pytest.mark.parametrize("failure_stage", ["progress", "failure_record", "failure_record_and_progress"])
async def test_article_failure_cancels_and_drains_siblings(monkeypatch, pipeline_setup, failure_stage):
    pipeline, task = pipeline_setup
    second_started = asyncio.Event()
    sibling_drained = asyncio.Event()
    release_sibling = asyncio.Event()
    writes = []
    pool_error = PoolTimeout("connection pool exhausted")

    class Generator:
        async def generate(self, *, article, **_kwargs):
            if article.article_id == "first":
                await second_started.wait()
                if failure_stage in {"failure_record", "failure_record_and_progress"}:
                    raise RuntimeError("synthetic model failure")
                return SimpleNamespace(fields={})
            second_started.set()
            try:
                await release_sibling.wait()
                return SimpleNamespace(fields={})
            finally:
                # Cleanup must finish before execute propagates the failure.
                await asyncio.sleep(0.01)
                sibling_drained.set()

    async def store(article_id, **_kwargs):
        writes.append(article_id)

    async def store_failure(*_args, **_kwargs):
        raise pool_error

    progress_calls = 0

    async def progress(_task_uuid, _percent):
        nonlocal progress_calls
        progress_calls += 1
        if failure_stage == "progress" and progress_calls == 1:
            raise pool_error
        if failure_stage == "failure_record_and_progress" and progress_calls == 1:
            raise RuntimeError("secondary progress error")

    monkeypatch.setattr(enrichment, "LiteratureSummaryGenerator", Generator)
    monkeypatch.setattr(pipeline, "_store", store)
    monkeypatch.setattr(pipeline, "_store_failure", store_failure)
    monkeypatch.setattr(enrichment.task_manager, "update_task_progress", progress)

    with pytest.raises(PoolTimeout) as raised:
        await asyncio.wait_for(pipeline.execute(task), timeout=2)

    assert raised.value is pool_error
    assert sibling_drained.is_set()
    release_sibling.set()
    await asyncio.sleep(0)
    assert "second" not in writes


async def test_parent_cancellation_drains_all_article_tasks(monkeypatch, pipeline_setup):
    pipeline, task = pipeline_setup
    started = set()
    drained = set()
    all_started = asyncio.Event()

    class Generator:
        async def generate(self, *, article, **_kwargs):
            started.add(article.article_id)
            if len(started) == 2:
                all_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.01)
                drained.add(article.article_id)

    async def progress(*_args):
        pass

    monkeypatch.setattr(enrichment, "LiteratureSummaryGenerator", Generator)
    monkeypatch.setattr(enrichment.task_manager, "update_task_progress", progress)
    parent = asyncio.create_task(pipeline.execute(task))
    await asyncio.wait_for(all_started.wait(), timeout=2)
    parent.cancel()
    with pytest.raises(asyncio.CancelledError):
        await parent
    assert drained == started == {"first", "second"}
