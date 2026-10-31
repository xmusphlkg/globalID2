"""Per-source literature and situation collection controls."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.literature.collection_sources import (
    assemble_checkpoint,
    normalize_literature_sources,
    run_covers,
    run_label,
    source_plan,
)
from src.services.situation_notices.source_control import (
    normalize_situation_sources,
    overlaps,
)


def test_situation_source_names_reject_unknown_ids() -> None:
    assert normalize_situation_sources(None) == []
    assert normalize_situation_sources(["who_don", "who_don", "ecdc_cdtr"]) == ["who_don", "ecdc_cdtr"]
    with pytest.raises(ValueError, match="Unknown situation sources: missing-feed"):
        normalize_situation_sources(["who_don", "missing-feed"])


def test_situation_overlap_treats_an_empty_selection_as_every_feed() -> None:
    all_feeds = SimpleNamespace(input_data={})
    one_feed = SimpleNamespace(input_data={"sources": ["who_don"]})
    other_feed = SimpleNamespace(input_data={"sources": ["ecdc_cdtr"]})
    assert overlaps(all_feeds, ["who_don"]) is True
    assert overlaps(one_feed, []) is True
    assert overlaps(one_feed, ["who_don"]) is True
    assert overlaps(one_feed, ["ecdc_cdtr"]) is False
    assert overlaps(other_feed, ["who_don"]) is False


def test_literature_source_names_reject_unknown_and_disabled_providers() -> None:
    config = SimpleNamespace(pubmed_enabled=False, publisher_rss_enabled=True)
    assert normalize_literature_sources([], config) == []
    assert normalize_literature_sources(["crossref", "publisher-rss"], config) == ["crossref", "publisher-rss"]
    with pytest.raises(ValueError, match="Unknown literature sources: europe-pmc"):
        normalize_literature_sources(["europe-pmc"], config)
    with pytest.raises(ValueError, match="Literature sources are not enabled: pubmed"):
        normalize_literature_sources(["pubmed"], config)


def test_literature_plan_and_run_label_keep_a_subset_from_claiming_crossref() -> None:
    config = SimpleNamespace(publisher_rss_enabled=True, pubmed_enabled=True)
    assert source_plan(None, config)["crossref"] is True
    assert source_plan(None, config)["publisher-rss"] is True
    selected = ["publisher-rss"]
    plan = source_plan(selected, config)
    assert plan["publisher-rss"] is True
    assert plan["crossref"] is False
    assert plan["pubmed"] is False
    label = run_label("crossref+pubmed", selected)
    assert label == "crossref+publisher-rss"
    run = SimpleNamespace(source=label, checkpoint={"selected_sources": selected})
    assert run_covers(run, "publisher-rss") is True
    assert run_covers(run, "crossref") is False


def test_subset_checkpoint_keeps_crossref_and_untouched_nested_cursors() -> None:
    previous = {
        "strategy": "index-date-kway-stable-boundary-v3",
        "through_indexed_at": "2026-08-13T00:00:00+00:00",
        "rss": {"cursor": "old"},
        "springer_nature": {"cursor": "keep"},
    }
    carried = assemble_checkpoint(
        selected=["publisher-rss"],
        crossref_planned=False,
        crossref_ran=False,
        source_checkpoint={"strategy": "pubmed-fallback-no-crossref-advance-v1", "through_indexed_at": "2026-01-01T00:00:00+00:00"},
        previous=previous,
        nested={"rss": {"cursor": "new"}},
    )
    assert carried["through_indexed_at"] == "2026-08-13T00:00:00+00:00"
    assert carried["springer_nature"] == {"cursor": "keep"}
    assert carried["rss"] == {"cursor": "new"}
    assert carried["selected_sources"] == ["publisher-rss"]
    assert carried["strategy"] == "index-date-kway-stable-boundary-v3"

    advanced = assemble_checkpoint(
        selected=["crossref", "publisher-rss"],
        crossref_planned=True,
        crossref_ran=True,
        source_checkpoint={"strategy": "index-date-kway-stable-boundary-v3", "through_indexed_at": "2026-09-01T00:00:00+00:00"},
        previous=previous,
        nested={"rss": {"cursor": "new"}},
    )
    assert advanced["through_indexed_at"] == "2026-09-01T00:00:00+00:00"
    assert advanced["springer_nature"] == {"cursor": "keep"}
    assert advanced["selected_sources"] == ["crossref", "publisher-rss"]


@pytest.mark.asyncio
async def test_publisher_rss_run_does_not_call_crossref_or_rewind_its_cursor(monkeypatch, tmp_path) -> None:
    from src.literature.pipeline import LiteraturePipeline

    journals = tmp_path / "journals.json"
    journals.write_text(json.dumps({"journals": [{"name": "Trusted", "issn": "1234-5678"}]}), encoding="utf-8")
    taxonomy = tmp_path / "taxonomy.json"
    taxonomy.write_text(json.dumps({"topics": {}, "study_types": {}}), encoding="utf-8")
    feeds = tmp_path / "feeds.json"
    feeds.write_text(json.dumps({"schema_version": 1, "feeds": []}), encoding="utf-8")
    config = SimpleNamespace(
        journals_path=journals,
        taxonomy_path=taxonomy,
        publisher_rss_feeds_path=feeds,
        publisher_rss_enabled=True,
        contact_email="tests@example.org",
        request_timeout_seconds=5,
        max_retries=1,
        publisher_rss_max_feed_bytes=1000,
        publisher_rss_seen_id_limit=10,
        max_publisher_rss_records=10,
        publisher_rss_concurrency=1,
        auto_publish_min_score=0.72,
        autopilot_enabled=False,
    )
    calls = {"crossref": 0}
    watermark = datetime(2026, 8, 13, tzinfo=timezone.utc)
    finished: list[tuple] = []

    class GuardCrossref:
        def __init__(self, **_kwargs) -> None:
            pass

        async def fetch_incremental(self, **_kwargs):
            calls["crossref"] += 1
            raise AssertionError("Crossref must stay idle during a publisher RSS run")

    class RssClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def fetch_incremental(self, **_kwargs):
            return SimpleNamespace(records=[], checkpoint={"strategy": "conditional-get-stable-id-v1", "cursor": "new"})

    class Stages:
        task_uuid = str(uuid4())
        attempt = 1

        async def transition(self, *_args, **_kwargs) -> None:
            return None

        async def finish(self, *_args, **_kwargs) -> None:
            return None

        async def mark_dirty(self, *_args, **_kwargs) -> None:
            return None

    async def start(*_args, **_kwargs):
        return Stages()

    pipeline = LiteraturePipeline(config)

    async def resolve_start(_now, _task):
        return datetime(2026, 1, 1, tzinfo=timezone.utc), None

    async def finish_run(_run_uuid, status, **kwargs):
        finished.append((status, kwargs))

    async def load_checkpoint():
        return {
            "through_indexed_at": watermark,
            "checkpoint": {
                "strategy": "index-date-kway-stable-boundary-v3",
                "through_indexed_at": watermark.isoformat(),
                "rss": {"cursor": "old"},
                "springer_nature": {"cursor": "keep"},
            },
        }

    monkeypatch.setattr("src.literature.pipeline.CrossrefClient", GuardCrossref)
    monkeypatch.setattr("src.literature.pipeline.PublisherRssClient", RssClient)
    monkeypatch.setattr("src.services.ingestion.stages.StageRun.start", start)
    monkeypatch.setattr("src.literature.pipeline.task_manager.update_task_progress", AsyncMock())
    monkeypatch.setattr("src.literature.pipeline.task_manager.is_cancel_requested", AsyncMock(return_value=False))
    monkeypatch.setattr("src.literature.source_log.log_literature_source", AsyncMock())
    monkeypatch.setattr(pipeline, "_resolve_start", resolve_start)
    monkeypatch.setattr(pipeline, "_create_run", AsyncMock())
    monkeypatch.setattr(pipeline, "_finish_run", finish_run)
    monkeypatch.setattr(pipeline, "_supersede_earlier_running_runs", AsyncMock())
    monkeypatch.setattr(pipeline, "_classification_catalogues", AsyncMock(return_value=([], [])))
    monkeypatch.setattr(pipeline, "_enrich_candidates", AsyncMock(return_value={
        "europe_pmc_enriched": 0,
        "unpaywall_enriched": 0,
        "openalex_enriched": 0,
        "europe_pmc_errors": 0,
        "unpaywall_errors": 0,
        "openalex_errors": 0,
        "enrichment_errors": 0,
        "enrichment_failed_providers": [],
        "enrichment_degraded_review": 0,
    }))
    monkeypatch.setattr(pipeline, "_load_latest_completed_checkpoint", load_checkpoint)

    task = SimpleNamespace(task_uuid=str(uuid4()), input_data={"sources": ["publisher-rss"]})
    result = await pipeline.execute(task)

    assert calls["crossref"] == 0
    assert result["through_indexed_at"] == watermark.isoformat()
    assert finished[0][0] == "completed"
    checkpoint = finished[0][1]["checkpoint"]
    assert checkpoint["selected_sources"] == ["publisher-rss"]
    assert checkpoint["through_indexed_at"] == watermark.isoformat()
    assert checkpoint["springer_nature"] == {"cursor": "keep"}
    assert checkpoint["rss"]["cursor"] == "new"
    assert finished[0][1]["source"] is None
    assert finished[0][1]["through_indexed_at"] == watermark
