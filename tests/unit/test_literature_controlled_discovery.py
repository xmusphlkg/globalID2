import json
from datetime import datetime, timezone
from pathlib import Path

from src.literature.controlled_discovery import (
    CHECKPOINT_SCHEMA,
    build_controlled_query_batches,
    fetch_controlled_discovery,
    select_controlled_query_batches,
)
from src.literature.pipeline import _deduplicate_candidates
from src.literature.types import ArticleCandidate


ROOT = Path(__file__).resolve().parents[2]
TAXONOMY = json.loads(
    (ROOT / "configs/literature/taxonomy.json").read_text(encoding="utf-8")
)
DISEASES = [
    {
        "disease_id": "D021",
        "name_en": "Dengue",
        "name_zh": "登革热",
        "aliases": ["DENV", "dengue fever"],
    },
    {
        "disease_id": "D028",
        "name_en": "Pertussis",
        "name_zh": "百日咳",
        "aliases": ["Bordetella pertussis", "whooping cough"],
    },
]
NOW = datetime(2026, 8, 17, tzinfo=timezone.utc)


def test_controlled_query_gold_set_meets_recall_and_strategy_gate():
    batches = build_controlled_query_batches(DISEASES, TAXONOMY)
    searchable = "\n".join(
        " ".join((*(batch.terms), batch.crossref_query or "", batch.europe_pmc_query or ""))
        for batch in batches
    ).casefold()
    gold_terms = {
        "dengue",
        "denv",
        "pertussis",
        "bordetella pertussis",
        "candida auris",
        "vaccine safety",
        "adverse event following immunization",
        "antimicrobial resistance",
        "drug resistance, microbial",
        "vaccines",
    }
    recall = sum(term in searchable for term in gold_terms) / len(gold_terms)
    categories = {category for batch in batches for category in batch.categories}

    assert recall >= 0.90
    assert {"disease", "pathogen", "mesh", "vaccine", "amr"} <= categories
    assert all(len(batch.terms) <= 8 for batch in batches)
    assert all("MESH:" in (batch.europe_pmc_query or "") for batch in batches)
    assert "Candida" not in next(
        batch.terms for batch in batches if batch.query_id == "pathogen:candida-auris"
    )


def test_controlled_query_checkpoint_rotates_retries_and_resets_on_plan_change():
    batches = build_controlled_query_batches(DISEASES, TAXONOMY)
    first, first_checkpoint = select_controlled_query_batches(
        batches,
        None,
        max_queries=3,
    )
    second, second_checkpoint = select_controlled_query_batches(
        batches,
        first_checkpoint,
        max_queries=3,
    )

    assert first_checkpoint["schema_version"] == CHECKPOINT_SCHEMA
    assert {batch.query_id for batch in first}.isdisjoint(batch.query_id for batch in second)
    assert second_checkpoint["start_offset"] == first_checkpoint["next_offset"]

    retry_checkpoint = {
        **second_checkpoint,
        "retry_query_ids": [first[0].query_id],
    }
    retried, _ = select_controlled_query_batches(batches, retry_checkpoint, max_queries=1)
    assert [batch.query_id for batch in retried] == [first[0].query_id]

    changed = build_controlled_query_batches(
        [*DISEASES, {"disease_id": "D031", "name_en": "Test fever", "aliases": []}],
        TAXONOMY,
    )
    reset, reset_checkpoint = select_controlled_query_batches(
        changed,
        second_checkpoint,
        max_queries=1,
    )
    assert reset_checkpoint["start_offset"] == 0
    assert reset[0].query_id == changed[0].query_id


class _BoundedCrossref:
    def __init__(self) -> None:
        self.calls = []

    async def search_works(self, **kwargs):
        self.calls.append(kwargs)
        return [
            {"DOI": f"10.1000/crossref-{len(self.calls)}-{index}"}
            for index in range(kwargs["max_records"] + 2)
        ]


class _BoundedEuropePmc:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = []
        self.fail = fail

    async def search_recent(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("temporary Europe PMC failure")
        return [
            {"doi": f"10.1000/epmc-{len(self.calls)}-{index}"}
            for index in range(kwargs["max_records"] + 2)
        ]


async def test_controlled_discovery_enforces_network_and_record_caps_with_provenance():
    batches = build_controlled_query_batches(DISEASES, TAXONOMY)
    crossref = _BoundedCrossref()
    europe_pmc = _BoundedEuropePmc()

    result = await fetch_controlled_discovery(
        crossref=crossref,
        europe_pmc=europe_pmc,
        batches=batches,
        checkpoint=None,
        since=NOW,
        until=NOW,
        max_queries=2,
        records_per_query=3,
        max_records=7,
        concurrency=4,
    )

    assert result.checkpoint["records_requested"] == 7
    assert result.checkpoint["records_returned"] == 7
    assert result.checkpoint["network_calls"] == 4
    assert len(crossref.calls) + len(europe_pmc.calls) == 4
    assert all(call["max_records"] <= 3 for call in [*crossref.calls, *europe_pmc.calls])
    assert all(
        record["_research_radar_discovery"]["query_id"]
        for record in [*result.crossref_records, *result.europe_pmc_records]
    )


async def test_controlled_discovery_provider_failures_are_checkpointed_for_retry():
    batches = build_controlled_query_batches(DISEASES, TAXONOMY)
    result = await fetch_controlled_discovery(
        crossref=_BoundedCrossref(),
        europe_pmc=_BoundedEuropePmc(fail=True),
        batches=batches,
        checkpoint=None,
        since=NOW,
        until=NOW,
        max_queries=1,
        records_per_query=2,
        max_records=4,
    )

    query_id = result.checkpoint["selected_query_ids"][0]
    assert result.checkpoint["retry_query_ids"] == [query_id]
    assert result.checkpoint["query_errors"] == [{
        "query_id": query_id,
        "provider": "europe_pmc",
        "error": "temporary Europe PMC failure",
    }]
    retried, _ = select_controlled_query_batches(
        batches,
        result.checkpoint,
        max_queries=1,
    )
    assert [batch.query_id for batch in retried] == [query_id]


def test_same_doi_dedup_retains_all_controlled_query_origins():
    def candidate(query_id: str) -> ArticleCandidate:
        return ArticleCandidate(
            article_id=f"lit-{query_id}",
            slug=query_id,
            title="Dengue vaccine safety",
            doi="10.1000/shared",
            source_payload={
                "_research_radar_discovery": {
                    "provider": "crossref",
                    "query_id": query_id,
                    "terms": [query_id],
                },
            },
        )

    deduplicated, duplicate_count = _deduplicate_candidates([
        candidate("disease:D021"),
        candidate("vaccine:controlled"),
    ])

    assert duplicate_count == 1
    assert [
        origin["query_id"]
        for origin in deduplicated[0].source_payload["controlled_discovery_origins"]
    ] == ["disease:D021", "vaccine:controlled"]
