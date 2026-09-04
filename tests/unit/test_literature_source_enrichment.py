from collections import Counter
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.domain import Base, LiteratureArticle, LiteratureIngestRun, LiteratureStatusEvent
from src.literature.clients.crossref import CrossrefClient
from src.literature.clients.openalex import OpenAlexClient
from src.literature.clients.unpaywall import UnpaywallClient
from src.literature.normalization import (
    apply_europe_pmc,
    apply_openalex,
    apply_unpaywall,
    normalize_oa_url,
)
from src.literature.pipeline import LiteraturePipeline
from src.literature.repository import LiteratureRepository
from src.literature.types import ArticleCandidate, Classification


def _candidate(**overrides) -> ArticleCandidate:
    values = {
        "article_id": "lit_test",
        "slug": "test-article",
        "title": "Test article",
        "doi": "10.1000/test",
    }
    values.update(overrides)
    return ArticleCandidate(**values)


class _SameTimestampCrossrefClient(CrossrefClient):
    def __init__(self) -> None:
        super().__init__(mailto="tests@example.org")
        indexed = {"date-time": "2026-08-13T00:00:00Z"}
        self.records = {
            "1111-1111": [
                {"DOI": f"10.1000/a{index}", "indexed": indexed}
                for index in range(1, 5)
            ],
            "2222-2222": [
                {"DOI": f"10.1000/b{index}", "indexed": indexed}
                for index in range(1, 5)
            ],
        }

    async def get_json(self, _client, path, *, params):
        issn = path.split("/")[2]
        records = self.records[issn]
        offset = 0 if params["cursor"] == "*" else int(params["cursor"])
        items = records[offset : offset + int(params["rows"])]
        return {
            "message": {
                "items": items,
                "next-cursor": str(offset + len(items)),
                "total-results": len(records),
            }
        }


async def test_crossref_stable_boundary_drains_same_timestamp_records_across_runs():
    client = _SameTimestampCrossrefClient()
    journals = [{"issn": "1111-1111"}, {"issn": "2222-2222"}]
    boundary = datetime(2026, 8, 13, tzinfo=timezone.utc)
    resume_after = None
    batches = []
    checkpoints = []

    for _ in range(3):
        result = await client.fetch_incremental(
            journals=journals,
            since=boundary,
            until=boundary,
            max_records=3,
            concurrency=2,
            resume_after=resume_after,
        )
        batches.append([item["DOI"] for item in result.records])
        checkpoints.append(result.checkpoint)
        resume_after = result.checkpoint["resume_after"]

    flattened = [doi for batch in batches for doi in batch]
    assert [len(batch) for batch in batches] == [3, 3, 2]
    assert flattened == [
        "10.1000/a1",
        "10.1000/a2",
        "10.1000/a3",
        "10.1000/a4",
        "10.1000/b1",
        "10.1000/b2",
        "10.1000/b3",
        "10.1000/b4",
    ]
    assert len(set(flattened)) == 8
    assert checkpoints[0]["resume_after"] == {
        "indexed_at": "2026-08-13T00:00:00+00:00",
        "record_ids": ["doi:10.1000/a1", "doi:10.1000/a2", "doi:10.1000/a3"],
    }
    assert len(checkpoints[1]["resume_after"]["record_ids"]) == 6
    assert checkpoints[2]["truncated"] is False
    assert checkpoints[2]["resume_after"] is None


class _HighVolumeCrossrefClient(CrossrefClient):
    def __init__(self, journal_count: int = 31, records_per_journal: int = 100) -> None:
        super().__init__(mailto="tests@example.org")
        self.base = datetime(2026, 8, 1, tzinfo=timezone.utc)
        self.requests: list[tuple[str, int]] = []
        self.records: dict[str, list[dict[str, object]]] = {}
        for journal_index in range(journal_count):
            issn = f"{journal_index:04d}-{journal_index:04d}"
            self.records[issn] = [
                {
                    "DOI": f"10.1000/j{journal_index:02d}-{record_index:03d}",
                    "indexed": {
                        "date-time": (
                            self.base
                            + timedelta(minutes=record_index * journal_count + journal_index)
                        ).isoformat().replace("+00:00", "Z")
                    },
                }
                for record_index in range(records_per_journal)
            ]

    async def get_json(self, _client, path, *, params):
        issn = path.split("/")[2]
        from_value = params["filter"].split("from-index-date:", 1)[1].split(",", 1)[0]
        from_indexed_at = datetime.strptime(from_value, "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=timezone.utc
        )
        eligible = [
            item
            for item in self.records[issn]
            if datetime.fromisoformat(
                str(item["indexed"]["date-time"]).replace("Z", "+00:00")
            )
            >= from_indexed_at
        ]
        offset = 0 if params["cursor"] == "*" else int(params["cursor"])
        rows = int(params["rows"])
        page = eligible[offset : offset + rows]
        self.requests.append((issn, rows))
        return {
            "message": {
                "items": page,
                "next-cursor": str(offset + len(page)),
                "total-results": len(eligible),
            }
        }


async def test_crossref_31_journal_merge_is_bounded_fair_and_resumable():
    client = _HighVolumeCrossrefClient()
    journals = [{"issn": issn} for issn in client.records]
    until = client.base + timedelta(days=3)

    first = await client.fetch_incremental(
        journals=journals,
        since=client.base,
        until=until,
        max_records=300,
        concurrency=4,
    )
    second = await client.fetch_incremental(
        journals=journals,
        since=datetime.fromisoformat(first.checkpoint["next_from_indexed_at"]),
        until=until,
        max_records=300,
        concurrency=4,
        resume_after=first.checkpoint["resume_after"],
    )

    combined = [*first.records, *second.records]
    expected = sorted(
        (item for records in client.records.values() for item in records),
        key=lambda item: (
            datetime.fromisoformat(str(item["indexed"]["date-time"]).replace("Z", "+00:00")),
            str(item["DOI"]),
        ),
    )[:600]
    assert [item["DOI"] for item in combined] == [item["DOI"] for item in expected]
    assert len({item["DOI"] for item in combined}) == 600
    first_journal_counts = Counter(str(item["DOI"]).split("/j", 1)[1][:2] for item in first.records)
    assert set(first_journal_counts.values()) <= {9, 10}
    assert len(first_journal_counts) == 31

    # One proportional page per journal is enough to select the global 300;
    # the old max-per-journal approach would have materialized about 3,100.
    assert first.checkpoint["page_size"] == 10
    assert first.checkpoint["pages_fetched"] <= 2 * len(journals)
    assert first.checkpoint["records_prefetched"] <= 300 + 10 * len(journals)
    assert first.checkpoint["lookahead_records"] <= 10 * len(journals)
    assert first.checkpoint["fetch_efficiency_ratio"] == pytest.approx(
        300 / first.checkpoint["records_prefetched"]
    )
    assert first.checkpoint["catch_up_required"] is True
    assert first.checkpoint["remaining_index_span_seconds"] > 0
    assert max(rows for _issn, rows in client.requests) == 10


class _FakeOpenAlexClient(OpenAlexClient):
    def __init__(self) -> None:
        super().__init__(mailto="tests@example.org", api_key="openalex-test-key")
        self.requests = []

    async def get_json(self, _client, path, *, params):
        self.requests.append((path, params))
        dois = params["filter"].removeprefix("doi:").split("|")
        return {
            "results": [
                {
                    "id": f"https://openalex.org/W{index + 1}",
                    "doi": doi,
                    "open_access": {"is_oa": True, "oa_url": f"https://oa.example.org/{index + 1}"},
                }
                for index, doi in enumerate(dois)
            ]
        }


async def test_openalex_client_batches_deduplicates_and_uses_reduced_field_selection():
    client = _FakeOpenAlexClient()
    result = await client.enrich_by_dois(
        ["10.1000/ONE", "https://doi.org/10.1000/two", "10.1000/one"],
        batch_size=1,
        concurrency=2,
        min_interval_seconds=0,
    )

    assert set(result) == {"10.1000/one", "10.1000/two"}
    assert len(client.requests) == 2
    assert all(params["per_page"] == 1 for _, params in client.requests)
    assert all(params["api_key"] == "openalex-test-key" for _, params in client.requests)
    assert all(
        params["select"] == (
            "id,doi,ids,open_access,best_oa_location,"
            "primary_topic,topics,keywords,concepts,authorships,"
            "cited_by_count,referenced_works_count,referenced_works,related_works"
        )
        for _, params in client.requests
    )


class _FakeUnpaywallClient(UnpaywallClient):
    def __init__(self) -> None:
        super().__init__(email="tests@example.org")
        self.requests = []

    async def get_json(self, _client, path, *, params):
        self.requests.append((path, params))
        doi = unquote(path.removeprefix("/v2/"))
        return {"doi": doi, "is_oa": False, "best_oa_location": None}


async def test_unpaywall_client_uses_one_bounded_lookup_per_unique_doi():
    client = _FakeUnpaywallClient()
    result = await client.enrich_by_dois(
        ["10.1000/ONE", "10.1000/two", "https://doi.org/10.1000/one"],
        concurrency=2,
        min_interval_seconds=0,
    )

    assert set(result) == {"10.1000/one", "10.1000/two"}
    assert len(client.requests) == 2
    assert all(params == {"email": "tests@example.org"} for _, params in client.requests)
    assert all("%2F" in path for path, _ in client.requests)


def test_enrichers_preserve_crossref_oa_evidence_and_still_add_openalex_id():
    candidate = _candidate(
        open_access_status="open",
        open_access_url="https://publisher.example.org/article/fulltext",
        license_url="https://creativecommons.org/licenses/by/4.0/",
    )
    apply_unpaywall(candidate, {"is_oa": False, "best_oa_location": None})
    apply_openalex(
        candidate,
        {
            "id": "https://openalex.org/W123456",
            "open_access": {"is_oa": True, "oa_url": "https://repository.example.org/copy.pdf"},
        },
    )

    assert candidate.openalex_id == "W123456"
    assert candidate.open_access_status == "open"
    assert candidate.open_access_url == "https://publisher.example.org/article/fulltext"
    assert candidate.license_url == "https://creativecommons.org/licenses/by/4.0/"
    assert candidate.source_urls["openalex"] == "https://openalex.org/W123456"


def test_openalex_persistence_is_bounded_to_graph_and_audit_metadata():
    candidate = _candidate()
    apply_openalex(candidate, {
        "id": "https://openalex.org/W123456",
        "doi": "https://doi.org/10.1000/test",
        "open_access": {"is_oa": True, "oa_status": "green", "oa_url": "https://oa.example.org/work"},
        "authorships": [
            {
                "author": {"id": "https://openalex.org/A11", "display_name": "Not persisted"},
                "countries": ["US"],
                "institutions": [{
                    "id": "https://openalex.org/I22",
                    "display_name": "Example School of Public Health",
                    "country_code": "GB",
                    "type": "education",
                    "ror": "must-not-be-persisted",
                }],
            }
        ],
        "cited_by_count": 17,
        "referenced_works_count": 2,
        "referenced_works": ["https://openalex.org/W7", "https://openalex.org/W8"],
        "related_works": ["https://openalex.org/W9"],
        "topics": [{"id": "https://openalex.org/T1", "display_name": "Infectious disease", "score": 0.91}],
        "abstract_inverted_index": {"forbidden": [1, 2, 3]},
        "counts_by_year": [{"year": 2026, "cited_by_count": 17}],
    })

    stored = candidate.source_payload["openalex"]
    assert stored["institutions"] == [{
        "id": "I22",
        "display_name": "Example School of Public Health",
        "country_code": "GB",
        "type": "education",
    }]
    assert stored["authorships"] == [{
        "author_id": "A11",
        "country_codes": ["GB", "US"],
        "institution_ids": ["I22"],
    }]
    assert stored["author_countries"] == ["GB", "US"]
    assert stored["cited_by_count"] == 17
    assert stored["referenced_works"] == ["W7", "W8"]
    assert stored["related_works"] == ["W9"]
    assert "abstract_inverted_index" not in stored
    assert "counts_by_year" not in stored
    assert "ror" not in stored["institutions"][0]


def test_unpaywall_supplies_a_legal_oa_url_and_rejects_unsafe_candidates():
    candidate = _candidate()
    apply_unpaywall(
        candidate,
        {
            "is_oa": True,
            "best_oa_location": {
                "url_for_pdf": "javascript:alert(1)",
                "url_for_landing_page": "https://repository.example.org/record/123",
            },
        },
    )

    assert candidate.open_access_status == "open"
    assert candidate.open_access_url == "https://repository.example.org/record/123"
    assert normalize_oa_url("http://127.0.0.1/private") is None
    assert normalize_oa_url("https://user:password@example.org/article") is None


def test_europe_pmc_oa_url_has_priority_over_enhancement_sources():
    candidate = _candidate()
    apply_europe_pmc(
        candidate,
        {"pmid": "123", "pmcid": "PMC123", "isOpenAccess": "Y"},
    )
    apply_unpaywall(
        candidate,
        {
            "is_oa": True,
            "best_oa_location": {"url": "https://unpaywall.example.org/article"},
        },
    )
    apply_openalex(
        candidate,
        {
            "id": "W999",
            "open_access": {"is_oa": True, "oa_url": "https://openalex.example.org/article"},
        },
    )

    assert candidate.open_access_url == "https://europepmc.org/articles/PMC123"
    assert candidate.source_urls["pmc"] == "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/"


class _ScalarResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _ResolveDbContext:
    def __init__(self, latest) -> None:
        self.latest = latest

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        return False

    async def execute(self, _statement):
        return _ScalarResult(self.latest)


async def test_pipeline_restores_stable_boundary_from_completed_checkpoint(monkeypatch):
    resume_after = {
        "indexed_at": "2026-08-13T00:00:00+00:00",
        "record_ids": ["doi:10.1000/a1", "doi:10.1000/a2"],
    }
    latest = SimpleNamespace(
        through_indexed_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
        checkpoint={
            "truncated": True,
            "next_from_indexed_at": "2026-08-13T00:00:00+00:00",
            "resume_after": resume_after,
        },
    )
    monkeypatch.setattr(
        "src.literature.pipeline.get_db",
        lambda: _ResolveDbContext(latest),
    )
    pipeline = LiteraturePipeline(SimpleNamespace(index_overlap_days=2, initial_lookback_days=14))

    since, restored = await pipeline._resolve_start(
        datetime(2026, 8, 14, tzinfo=timezone.utc),
        None,
    )

    assert since == datetime(2026, 8, 13, tzinfo=timezone.utc)
    assert restored == resume_after


async def test_pipeline_does_not_reopen_legacy_overlap_after_catching_up(monkeypatch):
    watermark = datetime(2026, 8, 13, 12, 30, tzinfo=timezone.utc)
    latest = SimpleNamespace(
        through_indexed_at=watermark,
        checkpoint={"strategy": "index-date-kway-stable-boundary-v3", "truncated": False},
    )
    monkeypatch.setattr(
        "src.literature.pipeline.get_db",
        lambda: _ResolveDbContext(latest),
    )
    # A legacy deployment can still have the former two-day value in its
    # environment. The stable cursor must not reopen that high-volume window.
    pipeline = LiteraturePipeline(SimpleNamespace(index_overlap_days=2, initial_lookback_days=14))

    since, restored = await pipeline._resolve_start(
        datetime(2026, 8, 14, tzinfo=timezone.utc),
        None,
    )

    assert since == watermark
    assert restored is None


class _SqliteResolveDbContext:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.session = None

    async def __aenter__(self):
        self.session = Session(self.engine)
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback):
        assert self.session is not None
        self.session.close()
        return False

    async def execute(self, statement):
        assert self.session is not None
        return self.session.execute(statement)


async def test_pipeline_checkpoint_lookup_ignores_newer_autopilot_and_keeps_stable_tie_order(
    monkeypatch,
):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[LiteratureIngestRun.__table__])
    tied_at = datetime(2026, 8, 13, 2, tzinfo=timezone.utc)
    boundary = datetime(2026, 8, 13, tzinfo=timezone.utc)
    resume_after = {
        "indexed_at": boundary.isoformat(),
        "record_ids": ["doi:10.1000/stable-boundary"],
    }
    with Session(engine) as session:
        session.add_all([
            LiteratureIngestRun(
                run_uuid="00000000-0000-0000-0000-000000000001",
                source="crossref+europe-pmc",
                status="completed",
                started_at=tied_at,
                completed_at=tied_at,
                from_indexed_at=boundary,
                through_indexed_at=boundary,
                checkpoint={
                    "strategy": "index-date",
                    "controlled_discovery": {"selected": "older-tie"},
                },
                counts={},
            ),
            LiteratureIngestRun(
                run_uuid="00000000-0000-0000-0000-000000000002",
                source="crossref+europe-pmc+controlled-query",
                status="completed",
                started_at=tied_at,
                completed_at=tied_at,
                from_indexed_at=boundary,
                through_indexed_at=boundary,
                checkpoint={
                    "strategy": "index-date",
                    "truncated": True,
                    "next_from_indexed_at": boundary.isoformat(),
                    "resume_after": resume_after,
                    "controlled_discovery": {"selected": "newer-id-tie"},
                },
                counts={},
            ),
            LiteratureIngestRun(
                run_uuid="00000000-0000-0000-0000-000000000003",
                source="research-radar-autopilot",
                status="completed",
                started_at=tied_at + timedelta(hours=2),
                completed_at=tied_at + timedelta(hours=2),
                from_indexed_at=tied_at + timedelta(hours=2),
                through_indexed_at=tied_at + timedelta(hours=2),
                checkpoint={
                    "strategy": "autopilot-policy",
                    "controlled_discovery": {"selected": "must-be-ignored"},
                },
                counts={},
            ),
        ])
        session.commit()

    monkeypatch.setattr(
        "src.literature.pipeline.get_db",
        lambda: _SqliteResolveDbContext(engine),
    )
    pipeline = LiteraturePipeline(SimpleNamespace(index_overlap_days=2, initial_lookback_days=14))

    since, restored = await pipeline._resolve_start(
        datetime(2026, 8, 14, tzinfo=timezone.utc),
        None,
    )
    nested = await pipeline._resolve_nested_checkpoint("controlled_discovery")

    assert since == boundary
    assert restored == resume_after
    assert nested == {"selected": "newer-id-tie"}
    engine.dispose()


async def test_pipeline_newer_truncated_run_wins_over_older_higher_watermark(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[LiteratureIngestRun.__table__])
    older_completed_at = datetime(2026, 8, 17, 4, tzinfo=timezone.utc)
    newer_completed_at = older_completed_at + timedelta(minutes=30)
    older_higher_watermark = datetime(2026, 8, 17, tzinfo=timezone.utc)
    newer_lower_boundary = datetime(2026, 8, 15, 4, 32, 27, tzinfo=timezone.utc)
    newer_resume_after = {
        "indexed_at": newer_lower_boundary.isoformat(),
        "record_ids": ["doi:10.1000/newer-truncated-boundary"],
    }
    with Session(engine) as session:
        session.add_all([
            LiteratureIngestRun(
                run_uuid="00000000-0000-0000-0000-000000000011",
                source="crossref+europe-pmc",
                status="completed",
                started_at=older_completed_at - timedelta(minutes=5),
                completed_at=older_completed_at,
                from_indexed_at=older_higher_watermark - timedelta(days=2),
                through_indexed_at=older_higher_watermark,
                checkpoint={"strategy": "index-date", "truncated": False},
                counts={},
            ),
            LiteratureIngestRun(
                run_uuid="00000000-0000-0000-0000-000000000012",
                source="crossref+europe-pmc+controlled-query",
                status="completed",
                started_at=newer_completed_at - timedelta(minutes=5),
                completed_at=newer_completed_at,
                from_indexed_at=newer_lower_boundary,
                through_indexed_at=newer_lower_boundary,
                checkpoint={
                    "strategy": "index-date",
                    "truncated": True,
                    "next_from_indexed_at": newer_lower_boundary.isoformat(),
                    "resume_after": newer_resume_after,
                },
                counts={},
            ),
        ])
        session.commit()

    monkeypatch.setattr(
        "src.literature.pipeline.get_db",
        lambda: _SqliteResolveDbContext(engine),
    )
    pipeline = LiteraturePipeline(SimpleNamespace(index_overlap_days=2, initial_lookback_days=14))

    since, restored = await pipeline._resolve_start(
        datetime(2026, 8, 18, tzinfo=timezone.utc),
        None,
    )

    assert since == newer_lower_boundary
    assert restored == newer_resume_after
    engine.dispose()


class _FindDb:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(str(statement))
        return _ScalarResult(self.responses.pop(0))


async def test_repository_identifier_lookup_order_is_stable_and_avoids_fuzzy_title_merges():
    existing = SimpleNamespace(article_id="lit_existing", slug="existing")
    db = _FindDb([None, None, None, existing])
    candidate = _candidate(pmid="123", pmcid="PMC123", openalex_id="W123")

    found = await LiteratureRepository(db)._find(candidate)

    assert found is existing
    assert candidate.article_id == "lit_existing"
    assert candidate.slug == "existing"
    assert [
        "literature_articles.doi" in db.statements[0],
        "literature_articles.pmid" in db.statements[1],
        "literature_articles.pmcid" in db.statements[2],
        "literature_articles.openalex_id" in db.statements[3],
    ] == [True, True, True, True]


async def test_repository_falls_back_to_deterministic_article_id_without_title_matching():
    existing = SimpleNamespace(article_id="lit_test", slug="existing")
    db = _FindDb([existing])
    candidate = _candidate(doi=None)

    found = await LiteratureRepository(db)._find(candidate)

    assert found is existing
    assert len(db.statements) == 1
    assert "literature_articles.article_id" in db.statements[0]


async def test_repository_maps_crossref_version_dois_to_existing_article_ids_reciprocally():
    peer_reviewed = SimpleNamespace(
        article_id="lit_peer",
        doi="10.1000/peer",
        metadata_={},
    )
    preprint = SimpleNamespace(
        article_id="lit_preprint",
        doi="10.1101/preprint",
        metadata_={},
    )
    db = _FindDb([peer_reviewed])

    mapped = await LiteratureRepository(db)._map_version_relations(preprint, [{
        "relation_type": "preprint_to_peer_reviewed",
        "preprint_doi": "10.1101/PREPRINT",
        "peer_reviewed_doi": "10.1000/PEER",
        "source": "crossref",
    }])

    assert mapped[0]["preprint_article_id"] == "lit_preprint"
    assert mapped[0]["peer_reviewed_article_id"] == "lit_peer"
    assert peer_reviewed.metadata_["version_relations"] == mapped
    assert "literature_articles.doi" in db.statements[0]


class _SyncSessionAdapter:
    """Exercise repository SQL against SQLite while preserving its async API."""

    def __init__(self, session: Session) -> None:
        self.session = session

    async def execute(self, statement):
        return self.session.execute(statement)


@pytest.mark.parametrize(
    ("identifier", "value"),
    [
        ("doi", "10.1000/integration"),
        ("pmid", "987654"),
        ("pmcid", "PMC987654"),
        ("openalex_id", "W987654"),
    ],
)
async def test_repository_deduplicates_each_stable_identifier_with_real_sql(identifier, value):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    LiteratureArticle.__table__.create(engine)
    try:
        with Session(engine) as session:
            session.add(
                LiteratureArticle(
                    article_id="lit_database_existing",
                    slug="database-existing",
                    title="Database existing article",
                    doi="10.1000/integration",
                    pmid="987654",
                    pmcid="PMC987654",
                    openalex_id="W987654",
                )
            )
            session.commit()
            identifiers = {"doi": None, "pmid": None, "pmcid": None, "openalex_id": None}
            identifiers[identifier] = value
            candidate = _candidate(
                article_id=f"lit_candidate_{identifier}",
                slug=f"candidate-{identifier}",
                **identifiers,
            )

            found = await LiteratureRepository(_SyncSessionAdapter(session))._find(candidate)

            assert found is not None
            assert found.article_id == "lit_database_existing"
            assert candidate.article_id == "lit_database_existing"
            assert candidate.slug == "database-existing"
    finally:
        engine.dispose()


class _UpsertDb:
    def __init__(self, article) -> None:
        self.article = article
        self.execute_count = 0
        self.added = []

    async def execute(self, _statement):
        self.execute_count += 1
        return _ScalarResult(self.article if self.execute_count == 1 else None)

    async def flush(self):
        return None

    def add(self, value):
        self.added.append(value)

    def add_all(self, _values):
        return None


async def test_repository_persists_openalex_id_and_does_not_downgrade_existing_open_access():
    article = SimpleNamespace(
        article_id="lit_existing",
        slug="existing",
        doi="10.1000/test",
        pmid=None,
        pmcid=None,
        openalex_id=None,
        abstract_license=None,
        source_urls={"doi": "https://doi.org/10.1000/test"},
        open_access_status="open",
        open_access_url="https://repository.example.org/existing",
        license_url=None,
        integrity_status="current",
        source_payload={"europe_pmc": {"isOpenAccess": "Y"}},
        metadata_={},
        publication_status="review",
    )
    candidate = _candidate(openalex_id="W123", open_access_status="unknown")
    repository = LiteratureRepository(_UpsertDb(article))

    inserted = await repository.upsert(candidate, Classification())

    assert inserted is False
    assert article.openalex_id == "W123"
    assert article.open_access_status == "open"
    assert article.open_access_url == "https://repository.example.org/existing"
    assert article.source_payload["europe_pmc"] == {"isOpenAccess": "Y"}
    assert article.metadata_["classification_version"] == 5
    assert article.metadata_["classification_evidence"] == {
        "diseases": {},
        "countries": {},
        "topics": {},
        "pathogens": {},
        "pathogen_types": {},
        "populations": {},
        "research_domain": {"value": "not_determined", "matched_terms": []},
    }
    assert article.metadata_["discovery_score_evidence"] == {
        "surveillance_relation_level": None,
        "surveillance_relation_score": 0.0,
        "components": {},
    }


async def test_repository_refreshes_stale_autopilot_exclusion_when_new_classification_reopens_review():
    article = SimpleNamespace(
        article_id="lit_existing",
        slug="existing",
        doi="10.1000/test",
        pmid=None,
        pmcid=None,
        openalex_id=None,
        abstract_license=None,
        source_urls={},
        open_access_status="unknown",
        open_access_url=None,
        license_url=None,
        integrity_status="current",
        source_payload={},
        metadata_={
            "autopilot": {
                "policy_version": "research-radar-autopilot.v1",
                "decision": "exclude",
                "decided_at": "2026-08-16T00:00:00+00:00",
                "actor": "research-radar-autopilot",
                "reasons": ["old classification score was below the exclusion gate"],
            },
        },
        publication_status="excluded",
    )

    db = _UpsertDb(article)
    await LiteratureRepository(db).upsert(
        _candidate(),
        Classification(publication_status="review"),
    )

    audit = article.metadata_["autopilot"]
    assert article.publication_status == "review"
    assert audit["decision"] == "hold"
    assert audit["actor"] == "literature-classifier"
    assert audit["reopened_from"] == "exclude"
    assert "reopened" in audit["reasons"][0]
    assert audit["decided_at"] != "2026-08-16T00:00:00+00:00"
    event = next(value for value in db.added if isinstance(value, LiteratureStatusEvent))
    assert event.previous_status == "excluded"
    assert event.current_status == "review"
    assert event.source == "literature-classifier"


async def test_degraded_enrichment_preserves_existing_editorial_publication_but_not_integrity_exclusion():
    def existing_article():
        return SimpleNamespace(
            article_id="lit_existing",
            slug="existing",
            doi="10.1000/test",
            pmid=None,
            pmcid=None,
            openalex_id=None,
            abstract_license=None,
            source_urls={},
            open_access_status="unknown",
            open_access_url=None,
            license_url=None,
            integrity_status="current",
            source_payload={},
            metadata_={},
            publication_status="published",
        )

    article = existing_article()
    await LiteratureRepository(_UpsertDb(article)).upsert(
        _candidate(),
        Classification(publication_status="review"),
        preserve_existing_publication_status=True,
    )
    assert article.publication_status == "published"

    classifier_excluded = existing_article()
    await LiteratureRepository(_UpsertDb(classifier_excluded)).upsert(
        _candidate(integrity_status="current"),
        Classification(publication_status="excluded"),
        preserve_existing_publication_status=True,
    )
    assert classifier_excluded.integrity_status == "current"
    assert classifier_excluded.publication_status == "excluded"

    integrity_article = existing_article()
    await LiteratureRepository(_UpsertDb(integrity_article)).upsert(
        _candidate(integrity_status="retracted"),
        Classification(publication_status="excluded"),
        preserve_existing_publication_status=True,
    )
    assert integrity_article.publication_status == "excluded"


async def test_pipeline_applies_core_then_unpaywall_then_openalex(monkeypatch):
    calls = []

    class FakeEuropePmc:
        def __init__(self, **_kwargs):
            pass

        async def enrich_by_dois(self, dois):
            calls.append(("europe_pmc", dois))
            return {"10.1000/test": {"pmcid": "PMC999", "isOpenAccess": "Y"}}

    class FakeUnpaywall:
        def __init__(self, **_kwargs):
            pass

        async def enrich_by_dois(self, dois, **_kwargs):
            calls.append(("unpaywall", dois))
            return {
                "10.1000/test": {
                    "is_oa": True,
                    "best_oa_location": {"url": "https://unpaywall.example.org/article"},
                }
            }

    class FakeOpenAlex:
        def __init__(self, **_kwargs):
            pass

        async def enrich_by_dois(self, dois, **_kwargs):
            calls.append(("openalex", dois))
            return {
                "10.1000/test": {
                    "id": "https://openalex.org/W999",
                    "open_access": {"is_oa": True, "oa_url": "https://openalex.example.org/article"},
                }
            }

    monkeypatch.setattr("src.literature.pipeline.EuropePmcClient", FakeEuropePmc)
    monkeypatch.setattr("src.literature.pipeline.UnpaywallClient", FakeUnpaywall)
    monkeypatch.setattr("src.literature.pipeline.OpenAlexClient", FakeOpenAlex)
    config = SimpleNamespace(
        europe_pmc_enabled=True,
        max_europe_pmc_records=10,
        unpaywall_enabled=True,
        max_unpaywall_records=10,
        openalex_enabled=True,
        max_openalex_records=10,
        openalex_api_key="",
        openalex_batch_size=100,
        metadata_enrichment_concurrency=2,
        metadata_enrichment_min_interval_seconds=0,
        contact_email="tests@example.org",
        request_timeout_seconds=5,
        max_retries=1,
    )
    candidate = _candidate()

    counts = await LiteraturePipeline(config)._enrich_candidates([candidate])

    assert [name for name, _ in calls] == ["europe_pmc", "unpaywall", "openalex"]
    assert counts == {
        "europe_pmc_enriched": 1,
        "unpaywall_enriched": 1,
        "openalex_enriched": 1,
        "europe_pmc_errors": 0,
        "unpaywall_errors": 0,
        "openalex_errors": 0,
        "pubmed_abstract_enriched": 0,
        "pubmed_abstract_errors": 0,
        "enrichment_errors": 0,
        "enrichment_failed_providers": [],
        "enrichment_degraded_review": 0,
    }
    assert candidate.openalex_id == "W999"
    assert candidate.open_access_url == "https://europepmc.org/articles/PMC999"
