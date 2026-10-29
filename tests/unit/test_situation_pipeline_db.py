"""End-to-end Situation pipeline on in-memory SQLite: store, fetch, extract, group, export."""

from __future__ import annotations

import json
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

pytest.importorskip("aiosqlite")

from src.core import database as database_module  # noqa: E402
from src.domain import (  # noqa: E402
    Base,
    SituationAlert,
    SituationDocument,
    SituationExtraction,
    SituationNotice,
    SituationOutbreak,
    SituationSourceRun,
)
from src.services.situation_notices import export as export_module  # noqa: E402
from src.services.situation_notices.documents import DocumentUnavailable, FetchedDocument, HTML  # noqa: E402
from src.services.situation_notices.ai import Completion  # noqa: E402
from src.services.situation_notices.pipeline import (  # noqa: E402
    extract_pending,
    fetch_pending_documents,
    rebuild_outbreaks,
    store_notices,
)

TEXT = (
    "## Summary\n"
    "As of 20 October 2026, a total of 9 704 confirmed cases, including 4,687 deaths, "
    "have been reported in the Democratic Republic of the Congo.\n"
    "A total of 9 cases including 1 death has been reported in Kenya.\n"
    "On 20 October 2026, Nigeria confirmed an imported case in Abuja.\n"
    "SECRET-FULL-TEXT-MARKER should never be published.\n"
)
AS_OF = date(2026, 10, 28)

TABLES = [
    SituationSourceRun.__table__,
    SituationNotice.__table__,
    SituationDocument.__table__,
    SituationExtraction.__table__,
    SituationAlert.__table__,
    SituationOutbreak.__table__,
]


def _event(native_id: str, *, ref: dict | None, published: str = "2026-10-23") -> dict:
    return {
        "event_id": f"who_don:{native_id}",
        "source_id": "who_don",
        "native_id": native_id,
        "record_type": "outbreak_notice",
        "title_en": "Ebola disease caused by Bundibugyo virus - DRC, Kenya and Nigeria",
        "summary_en": "short",
        "url": f"https://www.who.int/emergencies/disease-outbreak-news/item/{native_id}",
        "language": "en",
        "published_date": published,
        "updated_at_source": published,
        "iso_week_key": "2026-W43",
        "document_ref": ref if ref is not None else {},
    }


def _run(*events: dict) -> dict:
    return {
        "events": list(events),
        "sources": [{"source_id": "who_don", "status": "ok", "event_count": len(events), "error": None}],
    }


def _alert(country: str, quote: str, cases: int | None, deaths: int | None) -> dict:
    return {
        "disease_id": "D050",
        "disease_label": "Bundibugyo virus disease",
        "country_code": country,
        "admin_area": None,
        "status": "ongoing",
        "cases": cases,
        "deaths": deaths,
        "count_basis": "confirmed",
        "as_of": "2026-10-20",
        "quote": quote,
        "summary_en": f"Alert for {country}.",
        "summary_zh": f"{country} 警报。",
        "summary_fr": f"Alerte {country}.",
    }


DRAFT = {
    "document_type": "event_report",
    "alerts": [
        _alert(
            "CD",
            "As of 20 October 2026, a total of 9 704 confirmed cases, including 4,687 deaths, "
            "have been reported in the Democratic Republic of the Congo.",
            9704,
            4687,
        ),
        _alert("KE", "A total of 9 cases including 1 death has been reported in Kenya.", 9, 1),
        _alert("NG", "On 20 October 2026, Nigeria confirmed an imported case in Abuja.", None, None),
    ],
}


class Scripted:
    """Author and reviewer replies from a per-role script; records the attachments it was handed."""

    def __init__(self, author: list, reviewer: list) -> None:
        self.script = {"author": list(author), "reviewer": list(reviewer)}
        self.single_model = False
        self.calls: list[tuple[str, tuple]] = []

    async def __call__(self, role, system, prompt, *, attachments=None):
        self.calls.append((role, tuple(attachments or ())))
        reply = self.script[role].pop(0)
        model = "model-a" if role == "author" else "model-b"
        return Completion(text=json.dumps(reply), model=model, input_mode="text")


@pytest_asyncio.fixture
async def db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=TABLES))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database_module, "_engine", engine)
    monkeypatch.setattr(database_module, "_session_maker", maker)
    yield maker
    await engine.dispose()


def _fetcher(calls: list):
    def fetch(ref):
        calls.append(ref)
        if ref.get("slug") == "gone":
            raise DocumentUnavailable("WHO no longer lists this item")
        if ref.get("slug") == "flaky":
            raise RuntimeError("connection reset")
        return FetchedDocument(HTML, TEXT)

    return fetch


async def test_don_report_becomes_one_outbreak_with_three_places(db, tmp_path):
    fetched: list = []
    stored = await store_notices(
        _run(_event("2026-DON622", ref={"kind": "who_don", "slug": "2026-DON622"})), run_id="r1"
    )
    assert stored == {"new": 1, "updated": 0, "unchanged": 0}

    assert await fetch_pending_documents(fetch=_fetcher(fetched)) == {"ready": 1, "unavailable": 0, "failed": 0}
    assert fetched == [{"kind": "who_don", "slug": "2026-DON622"}]

    completer = Scripted([DRAFT], [{"verdict": "accept", "score": 0.95, "issues": [], "corrected": None}])
    assert (await extract_pending(completer))["published"] == 1
    assert [role for role, _ in completer.calls] == ["author", "reviewer"]
    attachment = completer.calls[0][1][0]
    assert attachment.text == TEXT  # both models receive the full document

    assert await rebuild_outbreaks(as_of=AS_OF) == 1
    async with db() as session:
        alerts = (await session.execute(select(SituationAlert))).scalars().all()
        outbreak = (await session.execute(select(SituationOutbreak))).scalar_one()
        notice = (await session.execute(select(SituationNotice))).scalar_one()
    assert {a.country_code for a in alerts} == {"CD", "KE", "NG"}
    assert {a.outbreak_id for a in alerts} == {outbreak.outbreak_id}
    assert sorted(outbreak.country_codes) == ["CD", "KE", "NG"]
    assert notice.ai_status == "extracted"

    summary = await export_module.export_site(output_dir=tmp_path, as_of=AS_OF)
    assert (summary["alert_count"], summary["point_count"], summary["outbreak_count"]) == (3, 3, 1)
    map_json = json.loads((tmp_path / "map.json").read_text(encoding="utf-8"))
    by_country = {p["country_code"]: p for p in map_json["points"]}
    assert by_country["CD"]["cases"] == 9704 and by_country["CD"]["deaths"] == 4687
    assert by_country["KE"]["quote"].endswith("reported in Kenya.")  # who_don terms allow quotes
    assert by_country["NG"]["url"].startswith("https://www.who.int/")
    published = "".join(path.read_text(encoding="utf-8") for path in tmp_path.rglob("*.json"))
    assert "SECRET-FULL-TEXT-MARKER" not in published


async def test_unchanged_report_is_not_read_again(db):
    event = _event("2026-DON622", ref={"kind": "who_don", "slug": "2026-DON622"})
    await store_notices(_run(event), run_id="r1")
    await fetch_pending_documents(fetch=_fetcher([]))
    completer = Scripted([DRAFT], [{"verdict": "accept", "score": 1, "issues": [], "corrected": None}])
    await extract_pending(completer)

    assert await store_notices(_run(event), run_id="r2") == {"new": 0, "updated": 0, "unchanged": 1}
    assert await fetch_pending_documents(fetch=_fetcher([])) == {"ready": 0, "unavailable": 0, "failed": 0}
    assert (await extract_pending(Scripted([], [])))["published"] == 0


async def test_reviewer_rejection_publishes_no_alerts(db):
    await store_notices(_run(_event("2026-DON623", ref={"kind": "who_don", "slug": "2026-DON623"})), run_id="r1")
    await fetch_pending_documents(fetch=_fetcher([]))
    completer = Scripted([DRAFT], [{"verdict": "reject", "score": 0.1, "issues": ["not supported"], "corrected": None}])
    assert (await extract_pending(completer))["rejected"] == 1
    async with db() as session:
        assert (await session.execute(select(SituationAlert))).scalars().all() == []
        assert (await session.execute(select(SituationNotice.ai_status))).scalar_one() == "rejected"
    assert await rebuild_outbreaks(as_of=AS_OF) == 0


async def test_document_failures_are_classified(db):
    await store_notices(
        _run(
            _event("gone-1", ref={"kind": "who_don", "slug": "gone"}),
            _event("flaky-1", ref={"kind": "who_don", "slug": "flaky"}),
            _event("noref-1", ref=None),
        ),
        run_id="r1",
    )
    counts = await fetch_pending_documents(fetch=_fetcher([]))
    assert counts == {"ready": 0, "unavailable": 1, "failed": 1}
    async with db() as session:
        docs = {
            row.notice_id: row
            for row in (await session.execute(select(SituationDocument))).scalars().all()
        }
        notices = {
            row.notice_id: row.ai_status for row in (await session.execute(select(SituationNotice))).scalars().all()
        }
    assert docs["who_don:gone-1"].status == "unavailable"
    assert docs["who_don:flaky-1"].status == "failed"
    assert docs["who_don:flaky-1"].next_attempt_at is not None  # retried later with backoff
    assert docs["who_don:noref-1"].status == "unavailable"
    assert notices["who_don:gone-1"] == "unavailable" and notices["who_don:noref-1"] == "unavailable"
    assert notices["who_don:flaky-1"] == "pending"


async def test_no_model_route_defers_the_batch_without_spending_attempts(db):
    from src.ai.agents.base import NoModelAvailable

    await store_notices(
        _run(*(_event(n, ref={"kind": "who_don", "slug": n}) for n in ("2026-DON630", "2026-DON631"))), run_id="r1"
    )
    await fetch_pending_documents(fetch=_fetcher([]))

    calls = []

    async def exhausted(role, system, prompt, *, attachments=None):
        calls.append(role)
        raise NoModelAvailable("all routes cooling down")

    counts = await extract_pending(exhausted)
    assert counts["failed"] == 0 and counts["deferred"] == 1
    assert calls == ["author"]  # the batch stopped at the first report
    async with db() as session:
        rows = (await session.execute(select(SituationNotice))).scalars().all()
    assert {row.ai_status for row in rows} == {"pending"}
    assert {row.ai_attempts for row in rows} == {0}


async def test_failover_onto_one_model_is_retried_on_another_pair(db):
    from src.ai.peer_review_completer import ReviewRoutePool

    class Collapsed(Scripted):
        async def __call__(self, role, system, prompt, *, attachments=None):
            reply = await super().__call__(role, system, prompt, attachments=attachments)
            return Completion(text=reply.text, model="model-a", input_mode="text")

    accept = {"verdict": "accept", "score": 0.9, "issues": [], "corrected": None}

    class Pool(ReviewRoutePool):
        def __init__(self):
            super().__init__({"p:model-a": 1, "p:model-b": 1, "p:model-c": 1}, line="situation_notice", max_tokens=2400)
            self.avoided: list[set] = []
            self.pairs = [Collapsed([DRAFT], [accept]), Scripted([DRAFT], [accept])]

        def completer(self, avoid=()):
            self.avoided.append(set(avoid))
            return self.pairs.pop(0)

    await store_notices(_run(_event("2026-DON640", ref={"kind": "who_don", "slug": "2026-DON640"})), run_id="r1")
    await fetch_pending_documents(fetch=_fetcher([]))

    pool = Pool()
    counts = await extract_pending(pool)
    assert counts["published"] == 1 and counts["failed"] == 0
    assert pool.avoided == [set(), {"model-a"}]
