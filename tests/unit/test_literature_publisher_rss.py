from datetime import datetime, timezone
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.core.config import LiteratureSettings
from src.domain import (
    Base,
    Country,
    LiteratureArticle,
    LiteratureCountryLink,
    LiteratureDiseaseLink,
    LiteratureIngestRun,
    LiteratureStatusEvent,
    LiteratureTopicLink,
    StandardDisease,
)
from src.literature.clients.crossref import CrossrefIncrementalResult
from src.literature.clients.rss import PublisherRssClient
from src.literature.clients.rss import RssIncrementalResult
from src.literature.normalization import normalize_crossref, normalize_publisher_rss
from src.literature.pipeline import LiteraturePipeline, _deduplicate_candidates


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


def _feed(feed_id: str, host: str) -> dict:
    return {
        "feed_id": feed_id,
        "publisher": "Trusted Publisher",
        "journal": "Trusted Journal",
        "issn": ["1234-5678"],
        "url": f"https://{host}/online-first.xml",
        "allowed_hosts": [host],
        "enabled": True,
    }


def _rss_body(items: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>Online First</title>{items}</channel></rss>
    """.encode()


def _item(doi: str, title: str, date: str, *, private_body: str = "not-for-ingestion") -> str:
    return f"""
      <item>
        <title>{title}</title>
        <guid>https://doi.org/{doi}</guid>
        <link>https://publisher.example/articles/{doi.rsplit('/', 1)[-1]}</link>
        <pubDate>{date}</pubDate>
        <description>{private_body}</description>
      </item>
    """


@pytest.mark.asyncio
async def test_fake_feeds_use_conditional_requests_and_deduplicate_same_batch():
    body = _rss_body(_item("10.1000/online-first", "Dengue Online First", "Sun, 16 Aug 2026 10:00:00 GMT"))
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers.get("If-None-Match") == '"feed-v1"':
            return httpx.Response(304, headers={"ETag": '"feed-v1"'}, request=request)
        return httpx.Response(
            200,
            content=body,
            headers={"Content-Type": "application/rss+xml", "ETag": '"feed-v1"'},
            request=request,
        )

    client = PublisherRssClient(
        user_agent="Research Radar tests",
        retries=1,
        transport=httpx.MockTransport(handler),
    )
    feeds = [_feed("trusted-one", "feed-one.example"), _feed("trusted-two", "feed-two.example")]
    first = await client.fetch_incremental(feeds=feeds, checkpoint=None, max_records=10, now=NOW)

    assert len(first.records) == 1
    assert first.records[0]["doi"] == "10.1000/online-first"
    assert first.records[0]["feed_origins"] == ["trusted-one", "trusted-two"]
    assert first.checkpoint["duplicates"] == 1
    assert all(state["etag"] == '"feed-v1"' for state in first.checkpoint["feeds"].values())

    second = await client.fetch_incremental(
        feeds=feeds,
        checkpoint=first.checkpoint,
        max_records=10,
        now=NOW,
    )
    assert second.records == []
    assert second.checkpoint["feeds_not_modified"] == 2
    assert [request.headers.get("If-None-Match") for request in requests[-2:]] == ['"feed-v1"', '"feed-v1"']


@pytest.mark.asyncio
async def test_truncated_feed_withholds_validator_then_recovers_remaining_entry():
    body = _rss_body(
        _item("10.1000/first", "First Online Article", "Sat, 15 Aug 2026 10:00:00 GMT")
        + _item("10.1000/second", "Second Online Article", "Sun, 16 Aug 2026 10:00:00 GMT")
    )
    request_validators: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        validator = request.headers.get("If-None-Match")
        request_validators.append(validator)
        if validator == '"two-entries"':
            return httpx.Response(304, headers={"ETag": '"two-entries"'}, request=request)
        return httpx.Response(200, content=body, headers={"ETag": '"two-entries"'}, request=request)

    client = PublisherRssClient(
        user_agent="Research Radar tests",
        retries=1,
        transport=httpx.MockTransport(handler),
    )
    feeds = [_feed("recovery-feed", "recovery.example")]
    first = await client.fetch_incremental(feeds=feeds, checkpoint=None, max_records=1, now=NOW)
    assert [record["doi"] for record in first.records] == ["10.1000/first"]
    assert first.checkpoint["truncated"] is True
    assert "etag" not in first.checkpoint["feeds"]["recovery-feed"]
    assert first.checkpoint["feeds"]["recovery-feed"]["unseen_remaining"] == 1

    second = await client.fetch_incremental(feeds=feeds, checkpoint=first.checkpoint, max_records=1, now=NOW)
    assert [record["doi"] for record in second.records] == ["10.1000/second"]
    assert second.checkpoint["truncated"] is False
    assert second.checkpoint["feeds"]["recovery-feed"]["etag"] == '"two-entries"'

    third = await client.fetch_incremental(feeds=feeds, checkpoint=second.checkpoint, max_records=1, now=NOW)
    assert third.records == []
    assert third.checkpoint["feeds_not_modified"] == 1
    assert request_validators == [None, None, '"two-entries"']


@pytest.mark.asyncio
async def test_feed_failure_preserves_committed_validator_for_next_recovery_poll():
    body = _rss_body(_item("10.1000/recovered", "Recovered Online Article", "Sun, 16 Aug 2026 10:00:00 GMT"))
    calls = 0
    validators: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        validators.append(request.headers.get("If-None-Match"))
        if calls == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(200, content=body, headers={"ETag": '"recovered-v2"'}, request=request)

    client = PublisherRssClient(
        user_agent="Research Radar tests",
        retries=1,
        transport=httpx.MockTransport(handler),
    )
    feeds = [_feed("failure-recovery", "failure.example")]
    checkpoint = {
        "feeds": {
            "failure-recovery": {
                "url": "https://failure.example/online-first.xml",
                "etag": '"committed-v1"',
                "seen_ids": [],
            }
        },
        "seen_record_ids": [],
    }
    failed = await client.fetch_incremental(feeds=feeds, checkpoint=checkpoint, max_records=10, now=NOW)
    assert failed.records == []
    assert failed.checkpoint["feed_errors"][0]["feed_id"] == "failure-recovery"
    assert failed.checkpoint["feeds"]["failure-recovery"]["etag"] == '"committed-v1"'

    recovered = await client.fetch_incremental(
        feeds=feeds,
        checkpoint=failed.checkpoint,
        max_records=10,
        now=NOW,
    )
    assert [record["doi"] for record in recovered.records] == ["10.1000/recovered"]
    assert recovered.checkpoint["feeds"]["failure-recovery"]["etag"] == '"recovered-v2"'
    assert validators == ['"committed-v1"', '"committed-v1"']


def test_rss_normalization_ignores_body_text_and_merges_with_crossref_by_doi():
    rss_candidate = normalize_publisher_rss({
        "feed_id": "trusted-one",
        "feed_url": "https://feed-one.example/online-first.xml",
        "entry_id": "doi:10.1000/shared",
        "guid": "https://doi.org/10.1000/shared",
        "title": "RSS Online First title",
        "link": "https://publisher.example/articles/shared",
        "doi": "10.1000/shared",
        "published_at": "2026-08-16T10:00:00+00:00",
        "retrieved_at": NOW.isoformat(),
        "journal": "Trusted Journal",
        "issn": ["1234-5678"],
        "publisher": "Trusted Publisher",
        "description": "must never be retained",
        "content": "must never be retained either",
    })
    crossref_candidate = normalize_crossref({
        "DOI": "10.1000/shared",
        "title": ["Canonical Crossref title"],
        "container-title": ["Trusted Journal"],
        "published-online": {"date-parts": [[2026, 8, 16]]},
        "indexed": {"date-time": "2026-08-17T00:00:00Z"},
        "abstract": "Licensed metadata abstract retained internally.",
    })
    assert rss_candidate is not None and crossref_candidate is not None
    assert rss_candidate.abstract_text is None
    assert "description" not in rss_candidate.source_payload["rss"]
    assert "content" not in rss_candidate.source_payload["rss"]

    merged, duplicate_count = _deduplicate_candidates([crossref_candidate, rss_candidate])
    assert duplicate_count == 1
    assert len(merged) == 1
    assert merged[0].title == "Canonical Crossref title"
    assert merged[0].abstract_text == "Licensed metadata abstract retained internally."
    assert merged[0].source_payload["rss"]["feed_id"] == "trusted-one"


@pytest.mark.asyncio
async def test_untrusted_feed_host_is_rejected_before_transport_call():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"<rss/>", request=request)

    client = PublisherRssClient(
        user_agent="Research Radar tests",
        retries=1,
        transport=httpx.MockTransport(handler),
    )
    feed = _feed("invalid-host", "allowed.example")
    feed["url"] = "https://attacker.example/feed.xml"
    with pytest.raises(ValueError, match="allowed_hosts"):
        await client.fetch_incremental(feeds=[feed], checkpoint=None, max_records=10, now=NOW)
    assert calls == 0


@pytest.mark.asyncio
async def test_feed_redirect_cannot_downgrade_https_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "allowed.example":
            return httpx.Response(
                301,
                headers={"Location": "http://feed.example/online-first.xml"},
                request=request,
            )
        return httpx.Response(200, content=b"<rss/>", request=request)

    client = PublisherRssClient(
        user_agent="Research Radar tests",
        retries=1,
        transport=httpx.MockTransport(handler),
    )
    feed = _feed("https-downgrade", "allowed.example")
    feed["allowed_hosts"].append("feed.example")
    result = await client.fetch_incremental(feeds=[feed], checkpoint=None, max_records=10, now=NOW)
    assert result.records == []
    assert result.checkpoint["feed_errors"] == [{
        "feed_id": "https-downgrade",
        "error": "Publisher feed 'https-downgrade' redirected outside HTTPS",
    }]


def test_configured_publisher_feeds_are_a_small_https_host_whitelist():
    payload = json.loads((ROOT / "configs/literature/publisher_feeds.json").read_text(encoding="utf-8"))
    feeds = payload["feeds"]
    assert payload["schema_version"] == 1
    assert 1 <= len(feeds) <= 5
    assert len({feed["feed_id"] for feed in feeds}) == len(feeds)
    assert all(feed["url"].startswith("https://") for feed in feeds)
    assert all(feed["allowed_hosts"] for feed in feeds)
    assert all(feed["journal"] and feed["issn"] for feed in feeds)
    assert all(feed.get("enabled", True) or feed.get("disabled_reason") for feed in feeds)


class _AsyncSessionAdapter:
    def __init__(self, session: Session, engine) -> None:
        self._session = session
        self.bind = engine

    async def execute(self, statement):
        return self._session.execute(statement)

    async def commit(self) -> None:
        self._session.commit()

    async def flush(self) -> None:
        self._session.flush()

    def add(self, value) -> None:
        self._session.add(value)

    def add_all(self, values) -> None:
        self._session.add_all(values)


class _DatabaseContext:
    def __init__(self, session_factory, engine) -> None:
        self.session_factory = session_factory
        self.engine = engine
        self.session = None

    async def __aenter__(self):
        self.session = self.session_factory()
        return _AsyncSessionAdapter(self.session, self.engine)

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
        self.engine = engine
        self.session_factory = sessionmaker(engine, expire_on_commit=False)

    def __call__(self):
        return _DatabaseContext(self.session_factory, self.engine)


class _PipelineCrossrefClient:
    def __init__(self, **_kwargs) -> None:
        pass

    async def fetch_incremental(self, **kwargs) -> CrossrefIncrementalResult:
        until = kwargs["until"]
        return CrossrefIncrementalResult(
            records=[{
                "DOI": "10.1000/shared-pipeline",
                "title": ["Dengue surveillance in Japan from Crossref"],
                "container-title": ["Trusted Journal"],
                "published-online": {"date-parts": [[2026, 8, 16]]},
                "indexed": {"date-time": "2026-08-17T00:00:00Z"},
                "abstract": "Crossref metadata abstract about dengue surveillance in Japan.",
            }],
            checkpoint={
                "strategy": "test",
                "through_indexed_at": until.isoformat(),
                "next_from_indexed_at": None,
                "resume_after": None,
                "truncated": False,
                "records_seen": 1,
                "records_returned": 1,
            },
        )


class _PipelineRssClient:
    def __init__(self, **_kwargs) -> None:
        pass

    async def fetch_incremental(self, **_kwargs) -> RssIncrementalResult:
        common = {
            "feed_id": "pipeline-feed",
            "feed_url": "https://pipeline.example/feed.xml",
            "retrieved_at": NOW.isoformat(),
            "journal": "Trusted Journal",
            "issn": ["1234-5678"],
            "publisher": "Trusted Publisher",
            "feed_origins": ["pipeline-feed"],
        }
        return RssIncrementalResult(
            records=[
                {
                    **common,
                    "entry_id": "doi:10.1000/shared-pipeline",
                    "guid": "https://doi.org/10.1000/shared-pipeline",
                    "title": "RSS duplicate title",
                    "link": "https://pipeline.example/shared",
                    "doi": "10.1000/shared-pipeline",
                    "published_at": "2026-08-16T00:00:00+00:00",
                },
                {
                    **common,
                    "entry_id": "guid:rss-only",
                    "guid": "rss-only",
                    "title": "Dengue surveillance in Japan Online First",
                    "link": "https://pipeline.example/rss-only",
                    "doi": None,
                    "published_at": "2026-08-17T00:00:00+00:00",
                },
            ],
            checkpoint={
                "strategy": "conditional-get-stable-id-v1",
                "feeds": {},
                "feed_errors": [],
                "records_seen": 2,
                "records_returned": 2,
                "feeds_modified": 1,
                "feeds_not_modified": 0,
                "truncated": False,
            },
        )


@pytest.mark.asyncio
async def test_pipeline_routes_rss_through_shared_dedup_classification_and_review_gate(monkeypatch, tmp_path):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=[
        Country.__table__,
        StandardDisease.__table__,
        LiteratureArticle.__table__,
        LiteratureDiseaseLink.__table__,
        LiteratureCountryLink.__table__,
        LiteratureTopicLink.__table__,
        LiteratureStatusEvent.__table__,
        LiteratureIngestRun.__table__,
    ])
    with Session(engine) as session:
        session.add(Country(code="JP", name="Japan", name_en="Japan", is_active=True))
        session.add(StandardDisease(
            disease_id="D021",
            standard_name_en="Dengue",
            standard_name_zh="登革热",
            is_active=True,
        ))
        session.commit()

    def write_json(name: str, payload: dict) -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    config = LiteratureSettings(
        _env_file=None,
        journals_path=write_json("journals.json", {"journals": [{"name": "Trusted", "issn": "1234-5678"}]}),
        taxonomy_path=write_json("taxonomy.json", {
            "topics": {"Surveillance": ["surveillance"]},
            "study_types": {},
        }),
        disease_aliases_path=write_json("aliases.json", {"aliases": {"D021": ["Dengue"]}}),
        publisher_rss_feeds_path=write_json("feeds.json", {"schema_version": 1, "feeds": [_feed("pipeline-feed", "pipeline.example")]}),
        publisher_rss_enabled=True,
        controlled_discovery_enabled=False,
        official_guidance_enabled=False,
        europe_pmc_enabled=False,
        openalex_enabled=False,
        unpaywall_enabled=False,
        autopilot_enabled=False,
        max_retries=1,
    )
    database = _DatabaseFactory(engine)
    monkeypatch.setattr("src.literature.pipeline.get_db", database)
    monkeypatch.setattr("src.literature.pipeline.CrossrefClient", _PipelineCrossrefClient)
    monkeypatch.setattr("src.literature.pipeline.PublisherRssClient", _PipelineRssClient)

    output = await LiteraturePipeline(config).execute()
    assert output["fetched"] == 3
    assert output["normalized"] == 2
    assert output["same_batch_duplicates"] == 1
    assert output["inserted"] == 2
    assert output["publisher_rss_fetched"] == 2

    with Session(engine) as session:
        articles = session.execute(select(LiteratureArticle).order_by(LiteratureArticle.doi)).scalars().all()
        events = session.execute(select(LiteratureStatusEvent).order_by(LiteratureStatusEvent.id)).scalars().all()
        assert len(articles) == 2
        rss_only = next(article for article in articles if article.doi is None)
        shared = next(article for article in articles if article.doi is not None)
        assert rss_only.abstract_text is None
        assert rss_only.publication_status == "review"
        assert shared.title == "Dengue surveillance in Japan from Crossref"
        assert shared.source_payload["rss"]["feed_id"] == "pipeline-feed"
        assert {event.source for event in events} == {"crossref+publisher-rss", "publisher-rss"}
        run = session.execute(select(LiteratureIngestRun)).scalar_one()
        assert run.checkpoint["rss"]["strategy"] == "conditional-get-stable-id-v1"
    engine.dispose()
