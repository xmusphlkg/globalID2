from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.domain import LiteratureArticle
from src.literature.metadata_backfill import backfill_existing_literature_metadata


class _AsyncSessionAdapter:
    def __init__(self, session: Session) -> None:
        self.session = session

    async def execute(self, statement):
        return self.session.execute(statement)

    async def commit(self) -> None:
        self.session.commit()


class _DatabaseContext:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory
        self.session = None

    async def __aenter__(self):
        self.session = self.session_factory()
        return _AsyncSessionAdapter(self.session)

    async def __aexit__(self, exc_type, _exc, _traceback):
        assert self.session is not None
        if exc_type is None:
            self.session.commit()
        else:
            self.session.rollback()
        self.session.close()
        return False


class _DatabaseFactory:
    def __init__(self, engine) -> None:
        self.session_factory = sessionmaker(engine, expire_on_commit=False)

    def __call__(self):
        return _DatabaseContext(self.session_factory)


class _FakeOpenAlex:
    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.fail_on_call = fail_on_call
        self.calls = []

    async def enrich_by_dois(self, dois, **kwargs):
        self.calls.append((list(dois), kwargs))
        if self.fail_on_call == len(self.calls):
            raise RuntimeError("temporary OpenAlex failure")
        return {
            doi: {
                "id": f"https://openalex.org/W{doi.rsplit('-', 1)[-1]}",
                "doi": f"https://doi.org/{doi}",
                "open_access": {"is_oa": True, "oa_url": f"https://oa.example.org/{doi.rsplit('-', 1)[-1]}"},
                "authorships": [{
                    "author": {"id": "https://openalex.org/A10"},
                    "countries": ["US"],
                    "institutions": [{
                        "id": "https://openalex.org/I20",
                        "display_name": "Test Institute",
                        "country_code": "US",
                    }],
                }],
                "cited_by_count": 5,
                "referenced_works": ["https://openalex.org/W30"],
                "related_works": ["https://openalex.org/W40"],
                "abstract_inverted_index": {"must": [0], "not": [1], "persist": [2]},
            }
            for doi in dois
        }


class _FakeUnpaywall:
    def __init__(self) -> None:
        self.calls = []

    async def enrich_by_dois(self, dois, **kwargs):
        self.calls.append((list(dois), kwargs))
        return {
            doi: {
                "doi": doi,
                "is_oa": True,
                "oa_status": "green",
                "best_oa_location": {
                    "url": f"https://repository.example.org/{doi.rsplit('-', 1)[-1]}",
                    "license": "cc-by",
                },
                "oa_locations": [{"url": "must-not-be-persisted"}] * 100,
            }
            for doi in dois
        }


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        contact_email="tests@example.org",
        openalex_api_key="",
        openalex_batch_size=100,
        request_timeout_seconds=5,
        max_retries=1,
        metadata_enrichment_concurrency=3,
        metadata_enrichment_min_interval_seconds=0.25,
    )


@pytest.fixture
def backfill_database():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    LiteratureArticle.__table__.create(engine)
    with Session(engine) as session:
        session.add_all([
            LiteratureArticle(
                article_id=f"lit_{index}",
                slug=f"article-{index}",
                title=f"Article {index}",
                doi=f"10.1000/article-{index}",
                publication_status=status,
                is_featured=index == 1,
                open_access_status="unknown",
                source_payload={"crossref": {"record": index}},
            )
            for index, status in ((1, "published"), (2, "review"), (3, "excluded"))
        ])
        session.commit()
    try:
        yield engine, _DatabaseFactory(engine)
    finally:
        engine.dispose()


async def test_backfill_defaults_to_no_database_or_checkpoint_writes(backfill_database, tmp_path):
    engine, database = backfill_database
    checkpoint = tmp_path / "dry-run.json"
    openalex = _FakeOpenAlex()
    unpaywall = _FakeUnpaywall()

    result = await backfill_existing_literature_metadata(
        batch_size=2,
        limit=2,
        checkpoint_path=checkpoint,
        config=_settings(),
        db_factory=database,
        openalex_client=openalex,
        unpaywall_client=unpaywall,
    )

    assert result["mode"] == "dry_run"
    assert result["status"] == "completed_at_limit"
    assert result["examined"] == 2
    assert result["updated"] == 2
    assert result["checkpoint_written"] is False
    assert not checkpoint.exists()
    assert openalex.calls[0][1] == {
        "batch_size": 2,
        "concurrency": 3,
        "min_interval_seconds": 0.25,
    }
    with Session(engine) as session:
        rows = session.execute(select(LiteratureArticle).order_by(LiteratureArticle.id)).scalars().all()
        assert all(article.openalex_id is None for article in rows)
        assert [article.publication_status for article in rows] == ["published", "review", "excluded"]
        assert rows[0].is_featured is True


async def test_apply_backfill_resumes_batches_and_preserves_editorial_state(backfill_database, tmp_path):
    engine, database = backfill_database
    checkpoint = tmp_path / "apply.json"
    openalex = _FakeOpenAlex()
    unpaywall = _FakeUnpaywall()

    first = await backfill_existing_literature_metadata(
        apply=True,
        batch_size=1,
        limit=1,
        checkpoint_path=checkpoint,
        config=_settings(),
        db_factory=database,
        openalex_client=openalex,
        unpaywall_client=unpaywall,
    )
    second = await backfill_existing_literature_metadata(
        apply=True,
        batch_size=1,
        checkpoint_path=checkpoint,
        config=_settings(),
        db_factory=database,
        openalex_client=openalex,
        unpaywall_client=unpaywall,
    )

    assert first["status"] == "completed_at_limit"
    assert second["resumed_from_database_id"] == first["last_database_id"]
    assert second["examined"] == 2
    assert second["status"] == "completed"
    assert checkpoint.exists()
    with Session(engine) as session:
        rows = session.execute(select(LiteratureArticle).order_by(LiteratureArticle.id)).scalars().all()
        assert [article.openalex_id for article in rows] == ["W1", "W2", "W3"]
        assert [article.publication_status for article in rows] == ["published", "review", "excluded"]
        assert [article.is_featured for article in rows] == [True, False, False]
        assert rows[0].source_payload["crossref"] == {"record": 1}
        assert rows[0].source_payload["openalex"]["institutions"][0]["id"] == "I20"
        assert rows[0].source_payload["openalex"]["author_countries"] == ["US"]
        assert rows[0].source_payload["openalex"]["cited_by_count"] == 5
        assert rows[0].source_payload["openalex"]["related_works"] == ["W40"]
        assert "abstract_inverted_index" not in rows[0].source_payload["openalex"]
        assert "oa_locations" not in rows[0].source_payload["unpaywall"]


async def test_provider_failure_does_not_advance_checkpoint_past_failed_batch(backfill_database, tmp_path):
    engine, database = backfill_database
    checkpoint = tmp_path / "failure.json"
    failing_openalex = _FakeOpenAlex(fail_on_call=2)
    unpaywall = _FakeUnpaywall()

    failed = await backfill_existing_literature_metadata(
        apply=True,
        batch_size=1,
        checkpoint_path=checkpoint,
        config=_settings(),
        db_factory=database,
        openalex_client=failing_openalex,
        unpaywall_client=unpaywall,
    )
    assert failed["status"] == "stopped_on_provider_error"
    assert failed["failure_count"] == 1
    assert failed["provider_stats"]["openalex"]["failed"] == 1
    assert failed["last_database_id"] == 1

    recovered = await backfill_existing_literature_metadata(
        apply=True,
        batch_size=2,
        checkpoint_path=checkpoint,
        config=_settings(),
        db_factory=database,
        openalex_client=_FakeOpenAlex(),
        unpaywall_client=_FakeUnpaywall(),
    )
    assert recovered["resumed_from_database_id"] == 1
    assert recovered["examined"] == 2
    assert recovered["status"] == "completed"
    with Session(engine) as session:
        rows = session.execute(select(LiteratureArticle).order_by(LiteratureArticle.id)).scalars().all()
        assert [article.openalex_id for article in rows] == ["W1", "W2", "W3"]
        assert [article.publication_status for article in rows] == ["published", "review", "excluded"]
