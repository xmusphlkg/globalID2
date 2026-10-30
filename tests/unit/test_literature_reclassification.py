from datetime import datetime
from types import SimpleNamespace

import pytest

from src.literature.reclassification import _automatic_relation, candidate_from_stored_article


def test_automatic_signal_relation_upgrades_when_reclassification_adds_signal_country():
    relation, confidence, reasons = _automatic_relation(
        disease_confidence=0.98,
        country_confidences={"CD": 0.94},
        signal_country_codes=["CD", "UG"],
        disease_min_confidence=0.82,
        exact_min_confidence=0.78,
        context_min_confidence=0.82,
    )

    assert relation == "exact_disease_geography"
    assert confidence == 0.94
    assert any("country:CD" in reason for reason in reasons)


def test_stored_article_candidate_preserves_source_evidence_and_normalizes_utc():
    article = SimpleNamespace(
        article_id="lit-1", slug="article", title="Ebola in DRC", doi="10.1/x",
        pmid="1", pmcid="PMC1", openalex_id="W1", journal="Journal", issn=["1234-5678"],
        publisher="Publisher", authors=[{"name": "A"}], article_type="journal-article",
        study_type="Commentary", published_at=datetime(2026, 8, 1), indexed_at=datetime(2026, 8, 2),
        abstract_text="Democratic Republic of the Congo", abstract_license=None,
        source_urls={"doi": "https://doi.org/10.1/x"}, open_access_status="open",
        open_access_url="https://example.org/open", license_url=None,
        peer_review_status="peer_reviewed", integrity_status="current",
        source_payload={"openalex": {"id": "W1"}},
    )

    candidate = candidate_from_stored_article(article)

    assert candidate.published_at.tzinfo is not None
    assert candidate.indexed_at.tzinfo is not None
    assert candidate.source_payload == {"openalex": {"id": "W1"}}


async def _maintenance_database():
    from contextlib import asynccontextmanager
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import StaticPool
    from src.domain import Base
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[
            table for name, table in Base.metadata.tables.items()
            if name.startswith("literature_") or name in {"tasks", "run_stages", "publish_dirty"}
        ]))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    @asynccontextmanager
    async def database():
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise
    return engine, database


@pytest.mark.parametrize("operation", ["classification", "signal_links"])
@pytest.mark.parametrize("fail_marker", [False, True])
async def test_reclassification_public_writes_and_marker_share_transaction(monkeypatch, operation, fail_marker):
    from unittest.mock import AsyncMock
    from sqlalchemy import select
    from src.core.config import LiteratureSettings
    from src.domain import LiteratureArticle, LiteratureEvidenceGap, LiteratureSignalArticleLink, LiteratureDiseaseLink, LiteratureCountryLink, LiteratureTopicLink, Task
    from src.domain.collection import PublishDirty, RunStage
    from src.literature import reclassification as service
    from src.literature.types import Classification, Match
    from src.services.ingestion import manual_runs
    engine, database = await _maintenance_database()
    async with database() as db:
        db.add(LiteratureArticle(id=1, article_id="article", slug="article", title="Disease evidence", publication_status="published", source_payload={}))
        db.add(LiteratureEvidenceGap(gap_id="gap", signal_id="signal", signal_kind="disease", signal_section="national", disease_id="D001", disease_name="Disease", country_codes=["JP"], country_names=["Japan"], gap_type="catalogue_coverage_gap", first_detected_at=datetime(2026, 10, 30), last_detected_at=datetime(2026, 10, 30), status="open", latest_metrics={}))
        db.add(LiteratureSignalArticleLink(id=1, gap_id="gap", signal_id="signal", article_id="article", relation_level="candidate", status="review", source="gap_discovery", confidence=0.1, metadata_={"autopilot":{"actor":"research-radar-autopilot"}}))
        db.add(LiteratureDiseaseLink(article_id="article", disease_id="D001", confidence=0.95, match_terms=[]))
        db.add(LiteratureCountryLink(article_id="article", country_code="JP", country_name="Japan", confidence=0.95))
    monkeypatch.setattr(service, "get_db", database)
    monkeypatch.setattr(service, "get_config", lambda: SimpleNamespace(literature=LiteratureSettings()))
    if operation == "classification":
        monkeypatch.setattr(service.LiteraturePipeline, "_classification_catalogues", AsyncMock(return_value=([], [])))
        monkeypatch.setattr(service, "_load_json", lambda path: {})
        monkeypatch.setattr(service, "classify_candidate", lambda *args, **kwargs: Classification(topics=[Match("outbreak", "Outbreak", 0.9)]))
        monkeypatch.setattr(service, "reconcile_automatic_signal_links", AsyncMock(return_value={}))
        run = service.reclassify_existing_literature
    else:
        run = service.reconcile_automatic_signal_links
    if fail_marker:
        async def fail(*args, **kwargs):
            raise RuntimeError("audit unavailable")
        monkeypatch.setattr(manual_runs, "record_manual_store", fail)
    try:
        if fail_marker:
            with pytest.raises(RuntimeError, match="audit unavailable"):
                await run()
        else:
            await run()
        async with database() as db:
            pending = list((await db.execute(select(PublishDirty))).scalars())
            tasks = list((await db.execute(select(Task))).scalars())
            stages = list((await db.execute(select(RunStage))).scalars())
            assert len(pending) == len(tasks) == (0 if fail_marker else 1)
            assert len(stages) == (0 if fail_marker else 3)
            if pending:
                assert pending[0].source_task_uuid == tasks[0].task_uuid
                assert pending[0].line == "literature" and pending[0].published_at is None
            if operation == "classification":
                topics = list((await db.execute(select(LiteratureTopicLink))).scalars())
                assert [topic.topic for topic in topics] == ([] if fail_marker else ["outbreak"])
            else:
                link = (await db.execute(select(LiteratureSignalArticleLink))).scalar_one()
                assert link.status == ("review" if fail_marker else "confirmed")
    finally:
        await engine.dispose()
