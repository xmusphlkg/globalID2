from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from scripts import rescreen_excluded_literature as rescreen


@pytest.mark.asyncio
async def test_batch_requeues_all_without_keyword_gate_and_preserves_locked_and_integrity(monkeypatch):
    def row(id, *, metadata=None, abstract=None, integrity="current"):
        return SimpleNamespace(id=id, article_id=f"a{id}", publication_status="excluded",
                               integrity_status=integrity, metadata_=metadata or {},
                               abstract_text=abstract, source_payload={})

    missing = row(1, metadata={"autopilot": {"decision": "exclude"}})
    contextual = row(2, abstract="No disease keyword in the title. " * 4)
    locked = row(3, metadata={"editorial_locked": True})
    retracted = row(4, integrity="retracted")

    class Database:
        def __init__(self):
            self.events = []

        async def execute(self, _query):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [missing, contextual, locked, retracted]))

        def add(self, event):
            self.events.append(event)

    db = Database()

    @asynccontextmanager
    async def fake_db():
        yield db

    monkeypatch.setattr(rescreen, "get_db", fake_db)
    counts = await rescreen.process_batch([1, 2, 3, 4], "run-1")
    assert counts == {"reopened": 2, "needs_evidence": 1, "contextual_review": 1,
                      "integrity_blocked": 1, "locked": 1, "changed_before_visit": 0, "pubmed_updated": 0}
    assert missing.publication_status == contextual.publication_status == "review"
    assert missing.metadata_["initial_screen"]["status"] == "needs_more_evidence"
    assert missing.metadata_["historical_rescreen"]["previous_autopilot"]["decision"] == "exclude"
    assert locked.publication_status == retracted.publication_status == "excluded"
    assert len(db.events) == 2
    # A replay after the DB commit must not duplicate events or erase decisions.
    replay = await rescreen.process_batch([1, 2, 3, 4], "run-1")
    assert replay == counts
    assert len(db.events) == 2


@pytest.mark.asyncio
async def test_pubmed_evidence_is_added_before_screening_and_replay_is_idempotent(monkeypatch):
    row = SimpleNamespace(
        id=1, article_id="a1", publication_status="excluded", integrity_status="current", metadata_={},
        abstract_text=None, abstract_license=None, pmid="123", source_urls={}, source_payload={},
        source_payload_version=0, source_payload_compacted_at=None,
    )

    class Database:
        def __init__(self):
            self.events = []

        async def execute(self, _query):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [row]))

        def add(self, event):
            self.events.append(event)

    db = Database()

    @asynccontextmanager
    async def fake_db():
        yield db

    monkeypatch.setattr(rescreen, "get_db", fake_db)
    payloads = {"123": {"abstractText": "The research describes disease burden and the results of a clinical study. " * 2}}
    counts = await rescreen.process_batch([1], "run-2", payloads)
    assert counts["pubmed_updated"] == 1 and counts["contextual_review"] == 1
    assert row.metadata_["initial_screen"]["status"] == "contextual_review"
    assert row.publication_status == "review" and len(db.events) == 1
    replay = await rescreen.process_batch([1], "run-2", payloads)
    assert replay == counts and len(db.events) == 1
