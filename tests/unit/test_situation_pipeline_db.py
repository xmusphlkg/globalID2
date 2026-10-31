"""Offline Situation pipeline with a single-connection SQLite pool."""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
async def db(monkeypatch, tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'situation.db'}",
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.5,
    )
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
    assert "quote" not in by_country["KE"]
    assert "summary_en" not in by_country["KE"]
    assert by_country["KE"]["country_name_en"]
    outbreak = json.loads((tmp_path / "outbreaks" / f"{by_country['KE']['outbreak_id']}.json").read_text(encoding="utf-8"))
    kenya = next(alert for alert in outbreak["alerts"] if alert["country_code"] == "KE")
    assert kenya["quote"].endswith("reported in Kenya.")  # who_don terms allow quotes
    assert kenya["url"].startswith("https://www.who.int/")
    congo = next(alert for alert in outbreak["alerts"] if alert["country_code"] == "CD")
    assert congo["cases"] == 9704 and congo["deaths"] == 4687
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
    assert all(row.ai_next_attempt_at is None for row in rows)


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


async def test_collection_runner_has_three_stages_and_dirty_without_export(db, monkeypatch):
    from uuid import uuid4
    from src.domain.collection import RunStage, PublishDirty, SourceArtifact
    from src.services.situation_notices import runner
    from src.services.ingestion.stages import StageRun
    async with database_module.get_engine().begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[RunStage.__table__, PublishDirty.__table__, SourceArtifact.__table__]))
    calls = []
    def collect(**_kwargs):
        return {"events": [], "sources": []}
    async def export_site(**_kwargs):
        calls.append("export")
        raise AssertionError("collection must not export")
    monkeypatch.setattr(runner, "collect", collect)
    monkeypatch.setattr(runner, "export_site", export_site)
    task_uuid = str(uuid4())
    await runner.run_situation(task_uuid=task_uuid, skip_ai=True)
    async with db() as session:
        stages = list((await session.execute(select(RunStage).where(RunStage.task_uuid == task_uuid))).scalars())
        dirty = (await session.execute(select(PublishDirty))).scalar_one()
    assert len(stages) == 3
    assert {stage.stage for stage in stages} == {"collect", "organize", "store"}
    assert all(stage.status == "completed" for stage in stages)
    assert (dirty.line, dirty.scope_key, dirty.source_task_uuid) == ("situation", "*", task_uuid)
    assert not calls


async def test_export_only_does_not_collect_or_rebuild(db, monkeypatch, tmp_path):
    from src.services.situation_notices import runner
    def forbidden(*_args, **_kwargs):
        raise AssertionError("export must only read stored data")
    async def export_site(**_kwargs):
        return {"alert_count": 0, "monitoring_products": 0}
    monkeypatch.setattr(runner, "collect", forbidden)
    monkeypatch.setattr(runner, "rebuild_outbreaks", forbidden)
    monkeypatch.setattr(runner, "export_site", export_site)
    assert "export" in await runner.export_situation(output_dir=tmp_path)


ACCEPT = {"verdict": "accept", "score": 0.95, "issues": [], "corrected": None}


class BlockedScripted(Scripted):
    def __init__(self, *, role="author", check_idle_pool=True):
        super().__init__([DRAFT], [ACCEPT])
        self.blocked_role = role
        self.check_idle_pool = check_idle_pool
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, role, system, prompt, *, attachments=None):
        # Both author and reviewer must run outside the pipeline's DB session.
        if self.check_idle_pool:
            assert database_module.get_engine().pool.checkedout() == 0
        if role == self.blocked_role:
            self.started.set()
            await self.release.wait()
        return await super().__call__(role, system, prompt, attachments=attachments)


async def _ready_reports(*names):
    await store_notices(
        _run(*(_event(name, ref={"kind": "who_don", "slug": name}) for name in names)), run_id="setup"
    )
    await fetch_pending_documents(fetch=_fetcher([]))


@pytest.mark.parametrize("blocked_role", ["author", "reviewer"])
async def test_wait_releases_connection_and_concurrent_retry_does_not_duplicate(db, blocked_role):
    await _ready_reports("2026-DON650")
    complete = BlockedScripted(role=blocked_role)
    task = asyncio.create_task(extract_pending(complete))
    try:
        await asyncio.wait_for(complete.started.wait(), timeout=2)
        assert database_module.get_engine().pool.checkedout() == 0
        # A second transaction can use the only connection while the model waits.
        async with db() as session:
            assert (await session.execute(select(SituationExtraction))).scalars().all() == []
            assert (await session.execute(select(SituationOutbreak))).scalars().all() == []
        assert not task.done()
        duplicate = Scripted([], [])
        assert (await extract_pending(duplicate))["published"] == 0
        assert duplicate.calls == []
    finally:
        complete.release.set()
        result = await asyncio.wait_for(task, timeout=3)
    assert result["published"] == 1
    assert (await extract_pending(Scripted([], [])))["published"] == 0
    async with db() as session:
        assert len((await session.execute(select(SituationExtraction))).scalars().all()) == 1
        assert len((await session.execute(select(SituationAlert))).scalars().all()) == 3
        notice = (await session.execute(select(SituationNotice))).scalar_one()
        assert notice.ai_attempts == 1
        assert notice.ai_next_attempt_at is None


async def test_simultaneous_batches_claim_a_report_only_once(db):
    await _ready_reports("2026-DON657")
    complete = BlockedScripted(check_idle_pool=False)
    tasks = [asyncio.create_task(extract_pending(complete)) for _ in range(2)]
    try:
        await asyncio.wait_for(complete.started.wait(), timeout=2)
        done, pending = await asyncio.wait(tasks, timeout=2, return_when=asyncio.FIRST_COMPLETED)
        assert len(done) == len(pending) == 1
        assert next(iter(done)).result()["published"] == 0
    finally:
        complete.release.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=3)
    assert sum(result["published"] for result in results) == 1
    assert [role for role, _ in complete.calls] == ["author", "reviewer"]


async def test_attachment_failure_is_isolated_to_one_report(db, monkeypatch):
    from src.services.situation_notices import pipeline

    await _ready_reports("2026-DON658", "2026-DON659")
    attachment = pipeline._attachment

    def missing_file(row):
        assert database_module.get_engine().pool.checkedout() == 0
        if row.native_id == "2026-DON658":
            raise FileNotFoundError("cached PDF missing")
        return attachment(row)

    monkeypatch.setattr(pipeline, "_attachment", missing_file)
    result = await extract_pending(Scripted([DRAFT], [ACCEPT]))
    assert result["failed"] == result["published"] == 1
    async with db() as session:
        failed = (await session.execute(select(SituationNotice).where(
            SituationNotice.native_id == "2026-DON658"
        ))).scalar_one()
        assert failed.ai_status == "failed"
        assert failed.ai_attempts == 1
        assert failed.ai_next_attempt_at is not None
        assert "FileNotFoundError" in failed.ai_last_error


@pytest.mark.parametrize("fails", [False, True])
async def test_changed_report_discards_stale_model_result_or_error(db, fails):
    await _ready_reports("2026-DON651")

    class ChangedWhileReading(BlockedScripted):
        async def __call__(self, *args, **kwargs):
            result = await super().__call__(*args, **kwargs)
            if fails:
                raise RuntimeError("old document failed")
            return result

    complete = ChangedWhileReading()
    task = asyncio.create_task(extract_pending(complete))
    try:
        await asyncio.wait_for(complete.started.wait(), timeout=2)
        changed = _event("2026-DON651", ref={"kind": "who_don", "slug": "2026-DON651"})
        changed["title_en"] += " (updated)"
        await store_notices(_run(changed), run_id="updated")
        await fetch_pending_documents(fetch=_fetcher([]))
    finally:
        complete.release.set()
        result = await asyncio.wait_for(task, timeout=3)
    assert result["deferred"] == 1
    assert result["failed"] == result["published"] == 0
    async with db() as session:
        assert (await session.execute(select(SituationExtraction))).scalars().all() == []
        notice = (await session.execute(select(SituationNotice))).scalar_one()
        assert notice.ai_status == "pending"
        assert notice.ai_attempts == 0
        assert notice.ai_next_attempt_at is None
    assert (await extract_pending(Scripted([DRAFT], [ACCEPT])))["published"] == 1


async def test_expired_claim_is_recovered_and_late_result_cannot_overwrite(db):
    from src.services.situation_notices.pipeline import utc_now

    await _ready_reports("2026-DON652")
    complete = BlockedScripted()
    task = asyncio.create_task(extract_pending(complete))
    try:
        await asyncio.wait_for(complete.started.wait(), timeout=2)
        async with database_module.get_db() as session:
            await session.execute(update(SituationNotice).values(ai_next_attempt_at=utc_now() - timedelta(seconds=1)))
        assert (await extract_pending(Scripted([DRAFT], [ACCEPT])))["published"] == 1
    finally:
        complete.release.set()
        result = await asyncio.wait_for(task, timeout=3)
    assert result["published"] == 0 and result["deferred"] == 1
    async with db() as session:
        assert len((await session.execute(select(SituationExtraction))).scalars().all()) == 1
        assert len((await session.execute(select(SituationAlert))).scalars().all()) == 3
        assert (await session.execute(select(SituationNotice.ai_attempts))).scalar_one() == 1


async def test_cancellation_releases_claim_without_spending_attempt(db):
    await _ready_reports("2026-DON653")
    complete = BlockedScripted()
    task = asyncio.create_task(extract_pending(complete))
    try:
        await asyncio.wait_for(complete.started.wait(), timeout=2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    async with db() as session:
        notice = (await session.execute(select(SituationNotice))).scalar_one()
        assert notice.ai_attempts == 0
        assert notice.ai_next_attempt_at is None
        assert notice.ai_status == "pending"
    assert (await extract_pending(Scripted([DRAFT], [ACCEPT])))["published"] == 1


async def test_long_model_wait_renews_claim_in_short_transactions(db, monkeypatch):
    from src.services.situation_notices import pipeline

    await _ready_reports("2026-DON654")
    monkeypatch.setattr(pipeline, "EXTRACTION_LEASE_SECONDS", 0.15)
    renewed = asyncio.Event()
    writes = []

    def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE situation_notices SET"):
            writes.append(statement)
            if len(writes) >= 2:
                renewed.set()

    engine = database_module.get_engine()
    sqlalchemy_event.listen(engine.sync_engine, "after_cursor_execute", after_cursor_execute)
    complete = BlockedScripted()
    task = asyncio.create_task(extract_pending(complete))
    try:
        await asyncio.wait_for(complete.started.wait(), timeout=2)
        async with db() as session:
            initial = (await session.execute(select(SituationNotice.ai_next_attempt_at))).scalar_one()
        await asyncio.wait_for(renewed.wait(), timeout=2)
        async with db() as session:
            current = (await session.execute(select(SituationNotice.ai_next_attempt_at))).scalar_one()
        assert current > initial
        assert not task.done()
        assert (await extract_pending(Scripted([], [])))["published"] == 0
    finally:
        complete.release.set()
        result = await asyncio.wait_for(task, timeout=3)
        sqlalchemy_event.remove(engine.sync_engine, "after_cursor_execute", after_cursor_execute)
    assert result["published"] == 1


async def test_runner_waits_for_extraction_and_records_partial_failure_before_store(db, monkeypatch):
    from uuid import uuid4

    from src.ai import peer_review_completer
    from src.domain.collection import PublishDirty, RunStage, SourceArtifact
    from src.services.situation_notices import runner

    async with database_module.get_engine().begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(
            sync, tables=[RunStage.__table__, PublishDirty.__table__, SourceArtifact.__table__]
        ))
    await _ready_reports("2026-DON655", "2026-DON656")

    class OneFailure(BlockedScripted):
        failed = False

        async def __call__(self, *args, **kwargs):
            if not self.failed:
                self.failed = True
                raise RuntimeError("one report failed")
            return await super().__call__(*args, **kwargs)

    complete = OneFailure()
    monkeypatch.setattr(peer_review_completer, "QueuedModelCompleter", lambda **kwargs: complete)
    task_uuid = str(uuid4())
    task = asyncio.create_task(runner.run_situation(task_uuid=task_uuid, fetch=False))
    try:
        await asyncio.wait_for(complete.started.wait(), timeout=2)
        async with db() as session:
            stages = {row.stage: row for row in (await session.execute(select(RunStage))).scalars()}
            assert stages["organize"].status == "running"
            assert stages["store"].status == "skipped"
            assert (await session.execute(select(SituationOutbreak))).scalars().all() == []
            assert (await session.execute(select(PublishDirty))).scalars().all() == []
        assert not task.done()
    finally:
        complete.release.set()
        result = await asyncio.wait_for(task, timeout=3)
    assert result["extraction"]["failed"] == 1
    assert result["extraction"]["published"] == 1
    assert result["outbreaks"] == 1
    async with db() as session:
        stages = {row.stage: row for row in (await session.execute(select(RunStage))).scalars()}
        assert all(row.status == "completed" for row in stages.values())
        assert stages["organize"].metrics["failed"] == 1
        assert stages["organize"].metrics["published"] == 1
        assert (await session.execute(select(PublishDirty))).scalar_one().source_task_uuid == task_uuid

    # Retrying the same parent only processes its failed report, with a new stage attempt.
    async with database_module.get_db() as session:
        await session.execute(update(SituationNotice).where(SituationNotice.ai_status == "failed").values(
            ai_next_attempt_at=None
        ))
    monkeypatch.setattr(peer_review_completer, "QueuedModelCompleter", lambda **kwargs: Scripted([DRAFT], [ACCEPT]))
    retry = await runner.run_situation(task_uuid=task_uuid, fetch=False)
    assert retry["extraction"]["failed"] == 0 and retry["extraction"]["published"] == 1
    async with db() as session:
        assert len((await session.execute(select(SituationExtraction))).scalars().all()) == 2
        assert len((await session.execute(select(SituationAlert))).scalars().all()) == 6
        assert len((await session.execute(select(SituationOutbreak))).scalars().all()) == 1
        stages = (await session.execute(select(RunStage))).scalars().all()
        assert len(stages) == 6 and {row.attempt for row in stages} == {1, 2}


async def test_queued_runner_can_dispatch_with_a_single_database_connection(db, monkeypatch, tmp_path):
    from uuid import uuid4

    from src.ai import work_queue as queue
    from src.domain import Task, TaskPriority, TaskStatus, TaskType
    from src.domain.ai_work_item import AIWorkItem
    from src.domain.collection import PublishDirty, RunStage, SourceArtifact
    from src.services.situation_notices import runner

    async with database_module.get_engine().begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(
            sync, tables=[Task.__table__, AIWorkItem.__table__, RunStage.__table__,
                          PublishDirty.__table__, SourceArtifact.__table__]
        ))
    monkeypatch.setattr(queue, "get_database", database_module.get_db)
    monkeypatch.setattr(queue, "ROOT", tmp_path)
    await _ready_reports("2026-DON660")
    parent = Task(
        task_uuid=str(uuid4()), task_type=TaskType.REFRESH_SITUATION_SOURCES,
        task_name="situation", status=TaskStatus.RUNNING,
        priority=TaskPriority.NORMAL, input_data={},
    )
    async with database_module.get_db() as session:
        session.add(parent)

    async def run_parent():
        async with queue.task_context(parent):
            result = await runner.run_situation(task_uuid=parent.task_uuid, fetch=False)
            queue.assert_children_succeeded()
            return result

    async def routes():
        return [{"model_key": key, "model_name": key, "runtime_model_capacity": 1,
                 "available_for_routing": True} for key in ("model-a", "model-b")]

    model_calls = []

    async def model(item, route):
        assert queue.in_dispatcher()
        # Other queue tasks may briefly use the pool; this call must still be
        # able to borrow its only connection while the parent awaits us.
        async with db() as session:
            assert await session.scalar(select(1)) == 1
        model_calls.append(item.payload["agent_name"])
        payload = DRAFT if item.payload["agent_name"].endswith("_author") else ACCEPT
        return {"text": json.dumps(payload), "conversation": {"model": route["model_key"]},
                "input_mode": "text"}

    dispatcher = queue.AIWorkDispatcher(routes, model)
    parent_task = asyncio.create_task(run_parent())
    try:
        async with asyncio.timeout(3):
            while parent.task_uuid not in queue.waiting_parent_uuids():
                if parent_task.done():
                    parent_task.result()
                await asyncio.sleep(0.01)
        async with db() as session:
            stored = (await session.execute(select(Task))).scalar_one()
            item = (await session.execute(select(AIWorkItem))).scalar_one()
            assert stored.status == TaskStatus.RUNNING
            assert stored.input_data["ai_waiting"] is True
            assert item.status == "queued" and item.line == "situation_notice"
            assert (await session.execute(select(SituationOutbreak))).scalars().all() == []
        assert model_calls == []

        async with asyncio.timeout(5):
            while not parent_task.done():
                await dispatcher.tick()
                if dispatcher.active:
                    await asyncio.gather(*dispatcher.active.values())
                await asyncio.sleep(0.01)
        result = parent_task.result()
        assert result["extraction"]["published"] == 1 and result["outbreaks"] == 1
        assert model_calls == ["situation_notice_author", "situation_notice_reviewer"]
        assert parent.task_uuid not in queue.waiting_parent_uuids()
        async with db() as session:
            items = (await session.execute(select(AIWorkItem))).scalars().all()
            assert len(items) == 2 and all(item.status == "succeeded" for item in items)
            assert len((await session.execute(select(SituationExtraction))).scalars().all()) == 1
            stages = (await session.execute(select(RunStage))).scalars().all()
            assert len(stages) == 3 and all(stage.status == "completed" for stage in stages)
    finally:
        if not parent_task.done():
            parent_task.cancel()
        await asyncio.gather(parent_task, return_exceptions=True)
        for task in dispatcher.active.values():
            task.cancel()
        await asyncio.gather(*dispatcher.active.values(), return_exceptions=True)
