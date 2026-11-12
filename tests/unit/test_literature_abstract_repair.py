from contextlib import asynccontextmanager
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from src.literature import abstract_repair as repair


def test_eligibility_preserves_excluded_and_requires_short_unlocked_review():
    sql = str(repair.eligible_query(168).compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "'review'" in sql
    assert "'current'" in sql
    assert "'peer_reviewed'" in sql
    assert "editorial_locked" in sql
    assert "abstract_repair" in sql
    assert " < 80" in sql


async def test_repair_uses_doi_fallback_and_keeps_status(monkeypatch):
    article = SimpleNamespace(id=1, article_id="a", pmid=None, doi="10.1/a", abstract_text=None,
                              abstract_license=None, source_payload={}, source_urls={}, metadata_={"autopilot": {"decision": "defer", "reasons": ["awaiting abstract context"]}})
    row = SimpleNamespace(id=1, pmid=None, doi="10.1/a")

    class Result:
        def __init__(self, rows):
            self.rows = rows
        def all(self):
            return self.rows
        def scalars(self):
            return self
    class DB:
        def __init__(self):
            self.calls = 0
            self.events = []
        async def execute(self, query):
            self.calls += 1
            return Result([article] if self.calls == 3 else [row])
        def add(self, event):
            self.events.append(event)
    db = DB()
    @asynccontextmanager
    async def fake_db():
        yield db
    class Epmc:
        def __init__(self, **kwargs):
            pass
        async def enrich_by_dois(self, dois, **kwargs):
            return {"10.1/a": {"abstractText": "evidence " * 20, "pmid": "123"}}
    monkeypatch.setattr(repair, "get_db", fake_db)
    monkeypatch.setattr(repair, "EuropePmcClient", Epmc)
    stats = await repair.repair_abstracts(apply=True)
    assert stats["filled"] == 1 and stats["europe_pmc_usable"] == 1
    assert article.abstract_text == ("evidence " * 20).strip()
    assert article.pmid is None
    assert "autopilot" not in article.metadata_
    assert db.events[0].previous_status == db.events[0].current_status == "review"
    assert "abstractText" not in article.source_payload["europe_pmc"]


async def test_task_operation_runs_repair_with_requested_bounds(monkeypatch):
    from src.services.model_operations import execute_model_operation

    calls = []
    async def fake_repair(**kwargs):
        calls.append(kwargs)
        return {"filled": 3}
    monkeypatch.setattr(repair, "repair_abstracts", fake_repair)
    result = await execute_model_operation("literature_abstract_repair", {"limit": 25, "retry_hours": 24})
    assert result == {"filled": 3}
    assert calls == [{"limit": 25, "retry_hours": 24, "apply": True}]


def test_repair_prioritizes_unchecked_specialty_and_disease_links():
    sql = str(repair.eligible_query(168).compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    order = sql.split('ORDER BY', 1)[1]
    assert 'abstract_repair' in order
    assert 'clinical infectious diseases' in order
    assert 'literature_disease_links' in order
    assert 'published_at DESC' in order
    assert "coalesce(literature_articles.pmid, '')" in sql
