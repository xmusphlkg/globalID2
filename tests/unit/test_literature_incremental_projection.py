from datetime import datetime, timedelta, timezone
import hashlib
import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.domain import (Base, LiteratureArticle, LiteratureCountryLink, LiteratureDiseaseLink,
                        LiteratureSignalArticleLink, LiteratureStatusEvent, LiteratureSummary,
                        LiteratureTopicLink)
from src.generation import site_data_literature as exporter
from src.generation.literature_projection_state import (
    bind_projection_state, canonical_bytes, load_projection_state, reuse_build_artifacts,
    write_projection_state,
)


async def test_incremental_matches_full_after_summary_withdrawal_and_clock_changes(tmp_path, monkeypatch):
    """An untouched article is reused, while removals and due articles agree with full SQL."""
    anchor = datetime(2026, 1, 10, tzinfo=timezone.utc)

    class Clock(datetime):
        value = anchor

        @classmethod
        def now(cls, tz=None):
            return cls.value

    monkeypatch.setattr(exporter, "datetime", Clock)
    monkeypatch.setattr(exporter, "has_table", AsyncMock(return_value=True))
    monkeypatch.setattr(exporter, "append_historical_seed_articles", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(exporter, "_load_title_disease_aliases", lambda: {})
    monkeypatch.setattr(exporter, "load_weekly_review_registry", lambda: {})
    monkeypatch.setattr(exporter, "load_weekly_ai_review_registry", lambda: {})
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'literature.sqlite'}")
    models = (LiteratureArticle, LiteratureCountryLink, LiteratureDiseaseLink, LiteratureSignalArticleLink,
              LiteratureStatusEvent, LiteratureSummary, LiteratureTopicLink)
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[model.__table__ for model in models]))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    diseases = {"D038": {"name_en": "Influenza", "name_zh": "流感", "slug": "influenza"}}

    def article(identifier, **kwargs):
        return LiteratureArticle(article_id=identifier, slug=identifier, title=f"Paper {identifier}",
            doi=f"10.1/{identifier}", published_at=anchor - timedelta(days=2), publication_status="published",
            peer_review_status="peer_reviewed", open_access_status="open", **kwargs)

    try:
        async with maker() as db:
            a, b, c = [article(identifier) for identifier in ("a", "b", "c")]
            future = article("future")
            future.published_at = anchor + timedelta(days=1)
            review = article("review")
            review.publication_status = "review"
            db.add_all([a, b, c, future, review])
            db.add(LiteratureDiseaseLink(article_id="b", disease_id="D038", confidence=0.9))
            db.add(LiteratureCountryLink(article_id="b", country_code="US", country_name="United States", confidence=0.9))
            for identifier in ("a", "b", "c", "future"):
                for language in ("en", "zh"):
                    db.add(LiteratureSummary(article_id=identifier, language=language, status="published",
                        research_question="question", main_findings="findings", public_health_relevance="relevance"))
            await db.commit()
        async with maker() as db:
            baseline = {}
            await exporter.collect_literature_export(db, diseases_by_id=diseases, projection_state=baseline)
        # Exercise the exact serialization boundary used by the persistent
        # private state, rather than relying on shared in-memory objects.
        baseline = json.loads(json.dumps(baseline))
        async with maker() as db:
            from sqlalchemy import select
            summary = (await db.execute(select(LiteratureSummary).where(
                LiteratureSummary.article_id == "a", LiteratureSummary.language == "en"))).scalar_one()
            summary.main_findings = "corrected findings"
            removed = (await db.execute(select(LiteratureArticle).where(LiteratureArticle.article_id == "c"))).scalar_one()
            removed.publication_status = "excluded"
            db.add(article("new"))  # Metadata-only publication still belongs in the full catalogue.
            await db.commit()
        Clock.value = anchor + timedelta(days=2)
        queries = []

        @event.listens_for(engine.sync_engine, "before_cursor_execute")
        def record(_connection, _cursor, statement, parameters, *_args):
            queries.append((statement, parameters))

        async with maker() as db:
            current = {}
            incremental = await exporter.collect_literature_export(db, diseases_by_id=diseases,
                surveillance_coverage={"D038": {"US"}},
                selected_article_ids={"a", "c", "new"}, previous_projection=baseline, projection_state=current)
        article_reads = [(sql, params) for sql, params in queries
                         if "FROM literature_articles" in sql and "literature_articles.source_payload" in sql]
        assert len(article_reads) == 1
        assert "literature_articles.article_id IN" in article_reads[0][0]
        assert "b" not in article_reads[0][1]
        assert "future" in article_reads[0][1]
        async with maker() as db:
            complete = await exporter.collect_literature_export(db, diseases_by_id=diseases,
                surveillance_coverage={"D038": {"US"}}, projection_state={})
        assert incremental == complete
        assert json.dumps(incremental, ensure_ascii=False) == json.dumps(complete, ensure_ascii=False)
        assert {row["article_id"] for row in current["articles"]} == {"a", "b", "new", "future"}
        assert {row["article_id"] for row in current["title_estimated_related_records"]} == {"review"}
        assert next(row for row in baseline["articles"] if row["article_id"] == "a")["summary"]["en"]["main_findings"] == "findings"
        assert "source_payload" not in json.dumps(current)
        assert "abstract_text" not in json.dumps(current)
    finally:
        await engine.dispose()


def _bound_snapshot(tmp_path):
    snapshot = tmp_path / "snapshot"
    public = snapshot / "public/research"
    public.mkdir(parents=True)
    files = {}
    for name in ("index.json", "catalogue.json"):
        (public / name).write_bytes(b"{}")
        digest = hashlib.sha256(b"{}").hexdigest()
        files[name] = {"url": f"objects/{digest}.json", "sha256": digest, "bytes": 2}
    manifest = {"schema_version": 1, "release_id": "literature-test", "files": files}
    projection = {"evaluated_at": "2026-01-01T00:00:00+00:00", "articles": [{"article_id": "a"}],
                  "title_estimated_related_records": [], "signal_decisions": []}
    write_projection_state(snapshot, projection, dependencies="dependency-v1")
    receipt = bind_projection_state(snapshot, manifest)
    return snapshot, receipt, projection, manifest


def test_only_acknowledged_unchanged_state_can_be_reused(tmp_path):
    snapshot, receipt, projection, manifest = _bound_snapshot(tmp_path)
    expected = hashlib.sha256(canonical_bytes(manifest)).hexdigest()
    assert load_projection_state(snapshot, expected_manifest_sha256=expected,
        dependencies="dependency-v1") == (projection, "incremental")
    assert load_projection_state(snapshot, expected_manifest_sha256="new-generation",
        dependencies="dependency-v1")[1] == "publication_baseline_changed"
    assert load_projection_state(snapshot, expected_manifest_sha256=expected,
        dependencies="dependency-v2")[1] == "projection_dependencies_changed"
    (snapshot / "projection-state.json").write_text("{}")
    assert load_projection_state(snapshot, expected_manifest_sha256=expected,
        dependencies="dependency-v1")[1] == "projection_state_changed"
    assert receipt["manifest_sha256"] == expected


def test_bind_rejects_different_public_candidate_and_reused_build_remains_immutable(tmp_path):
    from src.generation.site_data_writer import write_pretty_json
    snapshot, _, _, manifest = _bound_snapshot(tmp_path)
    (snapshot / "public/research/index.json").write_text('{"changed":true}')
    with pytest.raises(ValueError, match="published baseline"):
        bind_projection_state(snapshot, manifest)
    path = snapshot / "build/research/articles/a.json"
    write_pretty_json(path, {"title": "old"})
    target = tmp_path / "new"
    assert reuse_build_artifacts(snapshot, target) == 1
    write_pretty_json(target / "build/research/articles/a.json", {"title": "new"})
    assert json.loads(path.read_bytes()) == {"title": "old"}
