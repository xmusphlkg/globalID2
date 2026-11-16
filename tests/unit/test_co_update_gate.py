"""Scheduled Colombia checks must never mistake one source page for a refresh."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from src.data.crawlers.co import COUNT_URL, FIELDS, METADATA_URL
from src.data.processors.co import ColombiaINSSivigilaUpdater
from src.services.ingestion import co_update_gate as gate


def metadata(*, updated=1729208137):
    return json.dumps({"id": "4hyg-wa9d",
                       "attribution": "Instituto Nacional de Salud - INS, Bogotá D.C.",
                       "columns": [{"fieldName": field} for field in FIELDS[:-1]],
                       "rowsUpdatedAt": updated, "viewLastModified": 1779130843,
                       "licenseId": "CC_40_BY_SA"}).encode()


def fixtures(tmp_path, monkeypatch, *, state_status="complete", changed=False):
    payloads = {"metadata_before": metadata(updated=1729208137),
                "grouped_year_counts": b'[{"ano":"2007","n":"2"},{"ano":"2008","n":"3"}]',
                "metadata_after": metadata(updated=1729208137)}
    artifacts = [SimpleNamespace(metadata_={"endpoint": endpoint},
                                 source_url=COUNT_URL + "?$group=ano" if endpoint == "grouped_year_counts" else METADATA_URL,
                                 name=endpoint) for endpoint in payloads]
    monkeypatch.setattr(gate, "read_artifact", lambda artifact: payloads[artifact.name])
    from src.data.crawlers.co import parse_metadata
    version = parse_metadata(metadata())
    state = {"status": state_status, "completed_rows": 5, "expected_rows": 5,
             "identity": {"dataset_version": version, "year_counts": {"2007": 2, "2008": 3},
                          "start_year": 2007, "end_year": 2008}}
    acceptance = {"complete": True, "dataset_version": version,
                  "years": {year: {"official_rows": total, "database_rows": total,
                                   "complete": True} for year, total in {"2007": 2, "2008": 3}.items()}}
    if changed:
        payloads["metadata_after"] = metadata(updated=1729208138)
    state_path, acceptance_path = tmp_path / "state.json", tmp_path / "acceptance.json"
    state_path.write_text(json.dumps(state))
    acceptance_path.write_text(json.dumps(acceptance))
    return artifacts, state_path, acceptance_path


def test_grouped_counts_reject_duplicates_and_bad_rows():
    assert gate.parse_year_counts(b'[{"ano":"2008","n":"3"},{"ano":"2007","n":"2"}]') == {"2007": 2, "2008": 3}
    with pytest.raises(ValueError):
        gate.parse_year_counts(b'[{"ano":"2007","n":"2"},{"ano":"2007","n":"3"}]')
    with pytest.raises(ValueError):
        gate.parse_year_counts(b'[{"ano":"2007","n":"-1"}]')


def test_complete_accepted_unchanged_history_is_metadata_only_noop(tmp_path, monkeypatch):
    artifacts, state_path, acceptance_path = fixtures(tmp_path, monkeypatch)
    fetched = gate.organize(artifacts, updater=ColombiaINSSivigilaUpdater(),
                            state_path=state_path, acceptance_path=acceptance_path)
    assert fetched.rows == []
    assert fetched.source_audit["status"] == "unchanged_verified_metadata_only"
    assert fetched.source_audit["source_rows"] == 5
    assert fetched.source_audit["downloaded_case_pages"] == 0


def test_incomplete_changed_or_forced_history_requires_full_backfill(tmp_path, monkeypatch):
    artifacts, state_path, acceptance_path = fixtures(tmp_path, monkeypatch, state_status="running")
    with pytest.raises(gate.ColombiaHistoryRefreshRequired, match="requires_full_backfill.*co_sivigila_history.py"):
        gate.organize(artifacts, updater=ColombiaINSSivigilaUpdater(),
                      state_path=state_path, acceptance_path=acceptance_path)
    state = json.loads(state_path.read_text())
    state["status"] = "complete"
    state_path.write_text(json.dumps(state))
    with pytest.raises(gate.ColombiaHistoryRefreshRequired, match="requires_full_backfill"):
        gate.organize(artifacts, updater=ColombiaINSSivigilaUpdater(),
                      state_path=state_path, acceptance_path=acceptance_path, force=True)
    artifacts, state_path, acceptance_path = fixtures(tmp_path, monkeypatch, changed=True)
    with pytest.raises(gate.ColombiaHistoryRefreshRequired, match="changed during metadata polling"):
        gate.organize(artifacts, updater=ColombiaINSSivigilaUpdater(),
                      state_path=state_path, acceptance_path=acceptance_path)


@pytest.mark.asyncio
async def test_gate_collects_metadata_and_counts_but_no_case_page():
    requests = []
    written = []

    class Crawler:
        def __init__(self):
            self.session = SimpleNamespace(close=lambda: None)

        def get(self, url, *, params):
            requests.append((url, params))
            content = metadata() if url == METADATA_URL else b'[{"ano":"2007","n":"2"}]'
            return SimpleNamespace(url=url + "?$group=ano" if params else url, content=content)

    class Writer:
        artifacts = written

        async def write(self, payload, *, source_url, kind, metadata):
            written.append((source_url, kind, metadata["endpoint"]))

    result = await gate.collect(Writer(), crawler_type=Crawler)
    assert result == written
    assert [url for url, _ in requests] == [METADATA_URL, COUNT_URL, METADATA_URL]
    assert all("4hyg-wa9d.csv" not in url for url, _ in requests)


@pytest.mark.asyncio
async def test_crawl_service_uses_metadata_only_gate(monkeypatch):
    from src.services.crawl_service import CrawlService
    from src.services.ingestion import runner

    called = {}

    async def fake_runner(service, **kwargs):
        called.update(kwargs)
        return SimpleNamespace(total_records=0)

    monkeypatch.setattr(runner, "run_surveillance", fake_runner)
    result = await CrawlService()._execute_co_surveillance(
        task=SimpleNamespace(), updater=ColombiaINSSivigilaUpdater(),
        source="co_ins_sivigila_historical", force=False, process=True,
        save_raw=True, fill_missing=False)
    assert result.total_records == 0
    assert called["connector"] is gate
    assert called["process"] is False
    assert called["source"] == "co_ins_sivigila_historical"


@pytest.mark.asyncio
async def test_failed_gate_marks_its_crawl_run_failed(monkeypatch):
    from src.services import crawl_service
    from src.services.ingestion import runner

    async def failed_runner(service, **kwargs):
        raise gate.ColombiaHistoryRefreshRequired("requires_full_backfill")

    class Database:
        async def execute(self, query):
            return SimpleNamespace(scalar_one_or_none=lambda: 91)

    @asynccontextmanager
    async def database():
        yield Database()

    finished = []

    async def finish(self, *args, **kwargs):
        finished.append((args, kwargs))

    monkeypatch.setattr(runner, "run_surveillance", failed_runner)
    monkeypatch.setattr(crawl_service, "get_database", database)
    monkeypatch.setattr(crawl_service.CrawlService, "_finish_crawl_run", finish)
    with pytest.raises(gate.ColombiaHistoryRefreshRequired, match="requires_full_backfill"):
        await crawl_service.CrawlService()._execute_co_surveillance(
            task=SimpleNamespace(task_uuid="task-uuid"), updater=ColombiaINSSivigilaUpdater(),
            source="co_ins_sivigila_historical", force=False, process=True,
            save_raw=True, fill_missing=False)
    assert finished == [((91, 0, 0, 0), {"status": "failed", "error": "requires_full_backfill"})]
