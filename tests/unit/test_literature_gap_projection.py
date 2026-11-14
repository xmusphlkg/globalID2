"""Projection association coverage beyond the asyncpg bind-parameter limit."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import insert
from sqlalchemy.dialects.postgresql import asyncpg
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.domain import (
    Base,
    LiteratureArticle,
    LiteratureCountryLink,
    LiteratureDiseaseLink,
    StandardDisease,
)
from src.services.literature_gap_service import LiteratureGapService


@pytest.fixture
async def database():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    tables = [model.__table__ for model in (
        StandardDisease, LiteratureArticle, LiteratureDiseaseLink, LiteratureCountryLink,
    )]
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as db:
            yield db
    finally:
        await engine.dispose()


@pytest.mark.parametrize("article_count", [0, 1, 10_000, 10_001, 32_768])
async def test_projection_keeps_all_associations_with_bounded_parameters(database, monkeypatch, article_count):
    db = database
    published_at = datetime(2026, 11, 14, tzinfo=timezone.utc)
    await db.execute(insert(StandardDisease), [
        {"disease_id": code, "standard_name_en": code} for code in ("D001", "D002")
    ])
    for start in range(0, article_count, 500):
        ids = [f"article-{index:05}" for index in range(start, min(start + 500, article_count))]
        await db.execute(insert(LiteratureArticle), [
            {
                "article_id": article_id, "slug": article_id, "title": f"Title {article_id}",
                "publication_status": "published", "journal": "Journal", "study_type": "cohort",
                "published_at": published_at,
            }
            for article_id in ids
        ])
        # The final article deliberately has no associations.
        linked_ids = [article_id for article_id in ids if article_id != f"article-{article_count - 1:05}"]
        if linked_ids:
            await db.execute(insert(LiteratureDiseaseLink), [
                {"article_id": article_id, "disease_id": "D001", "confidence": 0.8}
                for article_id in linked_ids
            ])
            await db.execute(insert(LiteratureCountryLink), [
                {"article_id": article_id, "country_code": "JP", "country_name": "Japan", "confidence": 0.9}
                for article_id in linked_ids
            ])
    if article_count > 1:
        await db.execute(insert(LiteratureDiseaseLink), [{
            "article_id": "article-00000", "disease_id": "D002", "confidence": 0.7,
        }])
        await db.execute(insert(LiteratureCountryLink), [{
            "article_id": "article-00000", "country_code": "US", "country_name": "United States", "confidence": 0.6,
        }])
    for article_id, publication_status, integrity_status in (
        ("draft", "review", "current"),
        ("retracted", "published", "retracted"),
        ("concern", "published", "expression_of_concern"),
    ):
        await db.execute(insert(LiteratureArticle), [{
            "article_id": article_id, "slug": article_id, "title": article_id,
            "publication_status": publication_status, "integrity_status": integrity_status,
        }])
        await db.execute(insert(LiteratureDiseaseLink), [{
            "article_id": article_id, "disease_id": "D001", "confidence": 1.0,
        }])
        await db.execute(insert(LiteratureCountryLink), [{
            "article_id": article_id, "country_code": "JP", "country_name": "Japan", "confidence": 1.0,
        }])
    await db.commit()

    parameter_counts = []
    execute = db.execute

    async def execute_with_postgres_limit(statement, *args, **kwargs):
        if statement.get_final_froms()[0].name == "literature_articles":
            # Source payloads/abstracts can dwarf the public metadata. Only
            # projection fields may be fetched, even for a very large catalogue.
            assert set(statement.selected_columns.keys()) == {
                "article_id", "slug", "title", "journal", "study_type", "published_at",
            }
        # Expand IN values exactly as the PostgreSQL driver will receive them;
        # an unexpanded IN expression misleadingly appears to have one parameter.
        compiled = statement.compile(dialect=asyncpg.dialect(), compile_kwargs={"render_postcompile": True})
        parameter_counts.append(len(compiled.params))
        assert len(compiled.params) <= 32_767
        return await execute(statement, *args, **kwargs)

    monkeypatch.setattr(db, "execute", execute_with_postgres_limit)
    projection = await LiteratureGapService._published_projection(db)
    by_id = {article["article_id"]: article for article in projection}
    assert len(projection) == len(by_id) == article_count
    assert not {"draft", "retracted", "concern"}.intersection(by_id)
    assert parameter_counts
    for index in range(article_count):
        article_id = f"article-{index:05}"
        article = by_id[article_id]
        assert article["slug"] == article_id
        assert article["title"] == f"Title {article_id}"
        assert article["journal"] == "Journal"
        assert article["study_type"] == "cohort"
        assert article["published_at"].startswith("2026-11-14T00:00:00")
        expected_diseases = [] if index == article_count - 1 else [("D001", 0.8)]
        expected_countries = [] if index == article_count - 1 else [("JP", 0.9)]
        if index == 0 and article_count > 1:
            expected_diseases.append(("D002", 0.7))
            expected_countries.append(("US", 0.6))
        assert sorted((link["disease_id"], link["confidence"]) for link in article["diseases"]) == expected_diseases
        assert sorted((link["code"], link["confidence"]) for link in article["countries"]) == expected_countries
