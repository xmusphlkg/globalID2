"""Daily INS BES refresh keeps source failures retryable without reimporting history."""

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from scripts import refresh_co_bes
from scripts.imports import kh_camewarn_history
from src.data.crawlers.co_bes import BESContractError
from src.domain import TaskStatus


def test_delta_writer_keeps_only_changed_pdf_and_audits_full_listing(tmp_path, monkeypatch):
    monkeypatch.setattr(refresh_co_bes, "ROOT", tmp_path)
    monkeypatch.setattr(kh_camewarn_history, "ROOT", tmp_path)
    old = b"%PDF old %%EOF"
    new = b"%PDF new %%EOF"
    old_url = "https://www.ins.gov.co/buscador-eventos/old.pdf"
    new_url = "https://www.ins.gov.co/buscador-eventos/new.pdf"
    writer = refresh_co_bes.DeltaSnapshotWriter(
        tmp_path / "data/raw/co/bes-refresh/test", {old_url: hashlib.sha256(old).hexdigest()})

    async def capture():
        for url, payload in ((old_url, old), (new_url, new)):
            await writer.write(payload, source_url=url, kind="cda_pdf",
                               metadata={"endpoint": "pdf", "report": {"pdf_url": url},
                                         "sha256": hashlib.sha256(payload).hexdigest()})
        full = {"reports": [{"pdf_url": old_url}, {"pdf_url": new_url}],
                "captured": [{"url": old_url}, {"url": new_url}],
                "failed_downloads": [{"url": "https://www.ins.gov.co/missing.pdf"}]}
        await writer.write(json.dumps(full).encode(), source_url=new_url,
                           kind="archive_json", metadata={"endpoint": "collection_plan"})

    asyncio.run(capture())
    assert writer.pending == {new_url: hashlib.sha256(new).hexdigest()}
    assert len([a for a in writer.artifacts if a.metadata_.get("endpoint") == "pdf"]) == 1
    delta_artifact = next(a for a in writer.artifacts
                          if a.metadata_.get("endpoint") == "collection_plan")
    delta = json.loads((tmp_path / delta_artifact.relative_path).read_bytes())
    assert delta["reports"] == [{"pdf_url": new_url}]
    assert delta["captured"] == [{"url": new_url}]
    assert delta["failed_downloads"] == []
    assert writer.failed_downloads == [{"url": "https://www.ins.gov.co/missing.pdf"}]


@pytest.mark.parametrize("facts_match", [True, False])
def test_refresh_marks_only_complete_reports_applied(tmp_path, monkeypatch, facts_match):
    monkeypatch.setattr(refresh_co_bes, "ROOT", tmp_path)
    monkeypatch.setattr(kh_camewarn_history, "ROOT", tmp_path)
    url_ok = "https://www.ins.gov.co/buscador-eventos/2026_ok.pdf"
    url_gap = "https://www.ins.gov.co/buscador-eventos/2026_gap.pdf"
    payloads = {url_ok: b"%PDF ok %%EOF", url_gap: b"%PDF gap %%EOF"}
    digests = {url: hashlib.sha256(payload).hexdigest() for url, payload in payloads.items()}

    async def no_other_task():
        return None

    async def fake_collect(writer, **_options):
        for url, payload in payloads.items():
            await writer.write(payload, source_url=url, kind="cda_pdf",
                               metadata={"endpoint": "pdf", "report": {"pdf_url": url},
                                         "sha256": digests[url]})
        await writer.write(json.dumps({
            "reports": [{"pdf_url": url} for url in payloads],
            "captured": [{"url": url} for url in payloads], "failed_downloads": [],
        }).encode(), source_url=url_ok, kind="archive_json",
            metadata={"endpoint": "collection_plan"})

    async def fake_import(*_args, **_kwargs):
        return {"task_uuid": "test-task", "records": 2, "source_audit": {
            "report_audits": [{"source_url": url, "sha256": digest, "parsed_rows": 1}
                              for url, digest in digests.items()],
            "parser_gaps": [{"report_url": url_gap, "reason": "missing_column"},
                            {"report_url": url_ok, "reason": "cumulative_count_unreadable",
                             "source_cell_retained": True}],
            "failed_reports": [],
        }}

    monkeypatch.setattr(refresh_co_bes, "_ensure_no_other_bes_task", no_other_task)
    monkeypatch.setattr(refresh_co_bes, "collect", fake_collect)
    monkeypatch.setattr(refresh_co_bes, "import_archive", fake_import)
    async def fake_verify(*_args, **kwargs):
        assert kwargs["only_manifest_reports"] is True
        return {"facts_match": facts_match, "complete": False}

    monkeypatch.setattr(refresh_co_bes, "verify", fake_verify)
    state_path = tmp_path / "data/current/co/state.json"
    state = {"schema": 1, "source": refresh_co_bes.DEFAULT_SCOPE,
             "applied_reports": {url_gap: "a" * 64},
             "contract_versions": {"annex": refresh_co_bes.ANNEX_VERSION,
                                   "weekly_notifications": refresh_co_bes.WEEKLY_VERSION}}
    args = SimpleNamespace(state_path=state_path, cache_directory=tmp_path,
                           download_workers=1, parse_workers=1)
    result = asyncio.run(refresh_co_bes.refresh_once(args, state))
    assert result["applied_reports"].get(url_ok) == (digests[url_ok] if facts_match else None)
    assert result["applied_reports"][url_gap] == "a" * 64
    assert result["pending_reports"] == ([url_gap] if facts_match else sorted(payloads))
    assert result["status"] == "retry_pending"
    assert result["reconciliation_facts_match"] is facts_match
    assert json.loads(state_path.read_text())["pending_reports"] == result["pending_reports"]


@pytest.mark.parametrize("ownership,stale,recovers", [
    ("ours", True, True), ("ours", False, False), ("foreign", True, False),
])
def test_only_stale_owned_task_is_recovered(monkeypatch, ownership, stale, recovers):
    from src.ai.runtime import store as ai_store
    from src.core import database

    now = datetime.now(timezone.utc)
    age = timedelta(minutes=20 if stale else 1)
    child = SimpleNamespace(id=7)
    task = SimpleNamespace(
        task_uuid="owned-task", status=TaskStatus.RUNNING,
        started_at=now - age, completed_at=None, retry_count=0, last_error=None,
        metadata_={
            "origin": ("co_bes_automatic_refresh" if ownership == "ours" else "manual"),
            "automation_job_id": refresh_co_bes.AUTOMATION_JOB_ID,
            "task_lease": {"owner": "runner", "heartbeat_at": (now - age).isoformat()},
        },
    )

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def scalars(self):
            return self

        def all(self):
            return self.rows

    class FakeDB:
        async def execute(self, _statement):
            return Result([task])

        async def scalars(self, _statement):
            return Result([child])

    @asynccontextmanager
    async def fake_get_db():
        yield FakeDB()

    cancelled = []

    async def fake_cancel(_db, item):
        cancelled.append(item.id)

    monkeypatch.setattr(database, "get_db", fake_get_db)
    monkeypatch.setattr(ai_store, "request_cancellation", fake_cancel)
    if recovers:
        asyncio.run(refresh_co_bes._ensure_no_other_bes_task())
        assert task.status == TaskStatus.FAILED
        assert task.metadata_["task_lease"]["terminal_status"] == "failed"
        assert cancelled == [7]
    else:
        with pytest.raises(BESContractError, match="Another BES crawl task"):
            asyncio.run(refresh_co_bes._ensure_no_other_bes_task())
        assert task.status == TaskStatus.RUNNING
        assert cancelled == []
