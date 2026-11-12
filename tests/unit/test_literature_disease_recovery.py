from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from scripts import reopen_disease_literature as recovery


@pytest.mark.asyncio
async def test_recovery_reopens_automatic_exclusion_and_preserves_editorial_lock(monkeypatch):
    automatic = SimpleNamespace(
        id=1, article_id="one", doi="10.1/one", title="Disease evidence",
        publication_status="excluded",
        metadata_={"autopilot": {"decision": "exclude"}, "ai_content_governance": {"decision": "exclude"}},
    )
    locked = SimpleNamespace(
        id=2, article_id="two", doi="10.1/two", title="Locked evidence",
        publication_status="excluded", metadata_={"editorial_locked": True},
    )

    class FakeDb:
        events = []

        async def execute(self, _statement):
            return SimpleNamespace(all=lambda: [(automatic, 0.9), (locked, 0.95)])

        def add(self, event):
            self.events.append(event)

    db = FakeDb()

    @asynccontextmanager
    async def fake_db():
        yield db

    monkeypatch.setattr(recovery, "get_db", fake_db)
    preview = await recovery.run(apply=False, limit=10, after_id=0, journals=[], confidence=0.82)
    assert preview["reopened"] == 1 and automatic.publication_status == "excluded"
    result = await recovery.run(apply=True, limit=10, after_id=0, journals=[], confidence=0.82, dois=["10.1/one"])
    assert result["reopened"] == 1 and result["editorial_locked_skipped"] == 1
    assert automatic.publication_status == "review" and locked.publication_status == "excluded"
    assert automatic.metadata_["disease_recovery"]["previous_autopilot"]["decision"] == "exclude"
    assert automatic.metadata_["disease_recovery"]["previous_ai_content_governance"]["decision"] == "exclude"
    assert len(db.events) == 1 and db.events[0].source == "disease-recovery.v1"


async def test_bulk_reopening_requires_explicit_dois():
    with pytest.raises(ValueError, match="individually confirmed"):
        await recovery.run(apply=True, limit=10, after_id=0, journals=[], confidence=0.8)
