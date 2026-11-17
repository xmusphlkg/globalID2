"""SINAN avoids repeat transfers/parsing without hiding changed or damaged input."""
import hashlib
import json
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from scripts import update_br_subdivisions as state_script
from src.data.crawlers import br as crawler_module
from src.data.crawlers.br import BrazilSINANCrawler, SINANFile
from src.data.crawlers.br_cache import SINANContentCache
from src.data.processors.br import BRMonthlyUpdater
from src.services.ingestion.countries import br
from tests.unit.test_br_subdivisions import write_dbf

ITEM = SINANFile("COQU", "Pertussis", 2026, "COQUBR26.dbc", "ftp://official/COQUBR26.dbc",
                 "preliminary", 4, datetime(2026, 1, 1, tzinfo=timezone.utc))


@pytest.fixture(autouse=True)
def isolate_cache(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(crawler_module, "DEFAULT_CACHE_DIR", tmp_path / "cache")


class Writer:
    def __init__(self):
        self.artifacts = []
        self.task_uuid = "05c04b32-7481-427e-8764-a52c7b58f031"
        self.attempt = 1

    async def write(self, payload, *, source_url, kind, metadata=None):
        self.artifacts.append(SimpleNamespace(payload=payload, source_url=source_url, kind=kind, metadata_=metadata))


@pytest.mark.asyncio
async def test_national_and_states_share_download_and_force_refresh(monkeypatch):
    calls = []
    monkeypatch.setattr(BrazilSINANCrawler, "fetch_file_index", lambda self: [ITEM])
    def request(self, url):
        calls.append(url)
        return b"data"
    monkeypatch.setattr(BrazilSINANCrawler, "_request_with_retries", request)
    for country in ("BR", "BR-SP", "BR-RJ"):
        writer = Writer()
        await br.collect(writer, source="all", months=[(2026, 1)], history_csv="missing.csv", country_code=country)
        assert writer.artifacts[0].payload == b"data"
        assert writer.artifacts[0].kind == "sinan_dbc"
    assert calls == [ITEM.url]
    await br.collect(Writer(), source="all", months=[(2026, 1)], history_csv="missing.csv", force=True)
    assert calls == [ITEM.url, ITEM.url]


@pytest.mark.parametrize("change", ["modified", "size", "url", "status", "unknown_date", "unknown_size", "expired", "local_damage", "missing_metadata"])
def test_download_invalidates_uncertain_or_changed_file(tmp_path, monkeypatch, change):
    crawler = BrazilSINANCrawler()
    target = tmp_path / "downloads"
    calls = []
    def request(url):
        calls.append(url)
        return b"newer" if change == "size" and len(calls) == 2 else b"data"
    monkeypatch.setattr(crawler, "_request_with_retries", request)
    path = crawler._download_file(ITEM, target)
    item = ITEM
    if change == "modified":
        item = replace(ITEM, modified_at=datetime(2026, 2, 1, tzinfo=timezone.utc))
    elif change == "size":
        item = replace(ITEM, size_bytes=5)
    elif change == "url":
        item = replace(ITEM, url="ftp://another/COQUBR26.dbc")
    elif change == "status":
        item = replace(ITEM, dataset_status="final")
    elif change == "unknown_date":
        item = replace(ITEM, modified_at=None)
    elif change == "unknown_size":
        item = replace(ITEM, size_bytes=0)
    elif change == "expired":
        metadata_path = path.with_suffix(".dbc.metadata.json")
        metadata = json.loads(metadata_path.read_text())
        metadata["verified_at"] = "2020-01-01T00:00:00+00:00"
        metadata_path.write_text(json.dumps(metadata))
    elif change == "local_damage":
        original_mtime = path.stat().st_mtime_ns
        path.write_bytes(b"oops")
        os.utime(path, ns=(original_mtime + 1_000_000, original_mtime + 1_000_000))
    else:
        path.with_suffix(".dbc.metadata.json").unlink()
    crawler._download_file(item, target)
    assert len(calls) == 2
    assert path.read_bytes() == (b"newer" if change == "size" else b"data")


def test_concurrent_runs_download_once(tmp_path, monkeypatch):
    crawler = BrazilSINANCrawler()
    calls = []
    def request(url):
        calls.append(url)
        return b"data"
    monkeypatch.setattr(crawler, "_request_with_retries", request)
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(lambda _: crawler._download_file(ITEM, tmp_path / "downloads"), range(4)))
    assert len(set(paths)) == 1
    assert calls == [ITEM.url]


def source_artifact(payload, item):
    path = Path("data/raw/br/source.dbc")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    metadata = asdict(item)
    metadata["modified_at"] = item.modified_at.isoformat()
    return SimpleNamespace(relative_path=str(path), byte_size=len(payload), sha256=hashlib.sha256(payload).hexdigest(),
                           kind="sinan_dbc", source_url=item.url, metadata_={"file": metadata})


def test_national_content_cache_covers_new_months_and_invalidates_bytes_and_version(tmp_path, monkeypatch):
    dbf = tmp_path / "input.dbf"
    write_dbf(dbf, [("DT_NOTIFIC", "D", 8)], [
        (False, {"DT_NOTIFIC": "20260101"}), (False, {"DT_NOTIFIC": "20260201"}),
    ])
    item = replace(ITEM, size_bytes=dbf.stat().st_size)
    artifact = source_artifact(dbf.read_bytes(), item)
    decompressed = []
    def decompress(raw, target):
        decompressed.append(1)
        shutil.copyfile(raw, target)
    monkeypatch.setattr(BrazilSINANCrawler, "_decompress_to_dbf", staticmethod(decompress))
    updater = BRMonthlyUpdater(output_csv=tmp_path / "history.csv")
    def organize(months, **kwargs):
        return br.organize([artifact], updater=updater, source="all", months=months, history_csv=updater.output_csv, **kwargs)
    assert len(organize([(2026, 1)]).rows) == 1
    assert [r["Cases"] for r in organize([(2026, 1), (2026, 2)]).rows] == ["1", "1"]
    assert len(decompressed) == 1
    organize([(2026, 1)], force=True)
    assert len(decompressed) == 2
    monkeypatch.setattr(crawler_module, "NATIONAL_PARSER_VERSION", "national-monthly-test-v2")
    organize([(2026, 1)])
    assert len(decompressed) == 3
    # Same filename/size/mtime but different captured content must reparse.
    payload = dbf.read_bytes().replace(b"20260201", b"20260102")
    artifact = source_artifact(payload, item)
    assert organize([(2026, 1)]).rows[0]["Cases"] == "2"
    assert len(decompressed) == 4
    # A matching derived cache must never hide damaged raw artifacts.
    Path(artifact.relative_path).write_bytes(b"x" * artifact.byte_size)
    with pytest.raises(ValueError, match="Artifact integrity"):
        organize([(2026, 1)])


def test_state_parse_cache_shared_across_states_but_scoped_by_months(tmp_path, monkeypatch):
    dbf = tmp_path / "input.dbf"
    write_dbf(dbf, [("SG_UF", "C", 2), ("DT_NOTIFIC", "D", 8)], [
        (False, {"SG_UF": "35", "DT_NOTIFIC": "20260101"}),
        (False, {"SG_UF": "33", "DT_NOTIFIC": "20260201"}),
    ])
    artifact = source_artifact(dbf.read_bytes(), replace(ITEM, size_bytes=dbf.stat().st_size))
    calls = []
    def decompress(raw, target):
        calls.append(1)
        shutil.copyfile(raw, target)
    monkeypatch.setattr(BrazilSINANCrawler, "_decompress_to_dbf", staticmethod(decompress))
    def organize(country, months):
        updater = BRMonthlyUpdater(country_code=country)
        return br.organize([artifact], updater=updater, source="all", months=months,
                           history_csv=tmp_path / "history.csv", country_code=country)
    first = organize("BR-SP", [(2026, 1)])
    second = organize("BR-RJ", [(2026, 1)])
    assert first.rows[0]["Cases"] == "1" and second.rows[0]["Cases"] == "0"
    assert first.source_audit == second.source_audit
    assert len(calls) == 1
    assert organize("BR-RJ", [(2026, 2)]).rows[0]["Cases"] == "1"
    assert len(calls) == 2


def test_corrupt_parse_cache_is_a_miss(tmp_path):
    cache = SINANContentCache(tmp_path / "cache", ITEM, "a" * 64, version="test-v1")
    cache.write({"rows": [{"Cases": "1"}]})
    damaged = json.loads(cache.path.read_text())
    damaged["result"]["rows"][0]["Cases"] = "999"
    cache.path.write_text(json.dumps(damaged))
    assert cache.read() is None
    cache.path.write_text("{truncated")
    assert cache.read() is None


def test_listing_failure_does_not_renew_stale_index(tmp_path, monkeypatch):
    crawler = BrazilSINANCrawler()
    crawler._write_cached_file_index([ITEM])
    path = crawler._file_index_cache_path()
    os.utime(path, (1, 1))
    def fail(url):
        raise TimeoutError("directory unavailable")
    monkeypatch.setattr(crawler, "_fetch_listing_text", fail)
    assert crawler.fetch_file_index() == [ITEM]
    assert path.stat().st_mtime == 1
    crawler._file_index = None
    crawler.force_refresh = True
    with pytest.raises(RuntimeError, match="Unable to refresh"):
        crawler.fetch_file_index()
    assert path.stat().st_mtime == 1


def test_weekly_state_script_only_fetches_recent_annual_files(tmp_path, monkeypatch):
    year = datetime.now(ZoneInfo("America/Sao_Paulo")).year
    items = [replace(ITEM, year=value, filename=f"COQUBR{value % 100:02d}.dbc") for value in (2000, year - 2, year - 1, year)]
    monkeypatch.setattr(BrazilSINANCrawler, "fetch_file_index", lambda self: items)
    monkeypatch.setattr(state_script, "ROOT", tmp_path)
    monkeypatch.setattr(state_script, "ProcessPoolExecutor", ThreadPoolExecutor)
    monkeypatch.setattr(sys, "argv", ["update_br_subdivisions.py", "--recent-years", "2", "--force"])
    requested = []
    def aggregate(item, *, force):
        requested.append((item.year, force))
        return {"file": item.filename, "rows": [{
            "CountryCode": "BR-SP", "Date": f"{item.year}-01-01", "Year": str(item.year),
            "DiseaseCode": "COQU", "Cases": "1", "SourceFiles": item.filename,
            "SourceURLs": item.url, "DatasetStatus": item.dataset_status,
        }]}
    monkeypatch.setattr(state_script, "aggregate_file", aggregate)
    state_script.main()
    assert sorted(requested) == [(year - 1, True), (year, True)]


def test_state_script_revalidation_detects_replacement_with_same_metadata(tmp_path, monkeypatch):
    dbf = tmp_path / "input.dbf"
    write_dbf(dbf, [("SG_UF", "C", 2), ("DT_NOTIFIC", "D", 8)], [
        (False, {"SG_UF": "35", "DT_NOTIFIC": "20260101"}),
    ])
    original = dbf.read_bytes()
    replacement = original.replace(b"3520260101", b"3320260101")
    assert replacement != original and len(replacement) == len(original)
    responses = iter([original, replacement])
    crawler = BrazilSINANCrawler()
    item = replace(ITEM, size_bytes=len(original))
    monkeypatch.setattr(crawler, "_request_with_retries", lambda url: next(responses))
    parsed = []
    def decompress(raw, target):
        parsed.append(1)
        shutil.copyfile(raw, target)
    monkeypatch.setattr(crawler, "_decompress_to_dbf", decompress)
    first = state_script._aggregate_file(crawler, item)
    assert state_script._aggregate_file(crawler, item) == first
    assert len(parsed) == 1
    crawler.download_revalidate_seconds = 0
    updated = state_script._aggregate_file(crawler, item)
    assert len(parsed) == 2
    assert updated["sha256"] != first["sha256"]
    assert next(row for row in first["rows"] if row["CountryCode"] == "BR-SP")["Cases"] == "1"
    assert next(row for row in updated["rows"] if row["CountryCode"] == "BR-SP")["Cases"] == "0"
    assert next(row for row in updated["rows"] if row["CountryCode"] == "BR-RJ")["Cases"] == "1"


def test_state_script_does_not_make_zeroes_for_unknown_only_month(tmp_path, monkeypatch):
    dbf = tmp_path / "unknown-month.dbf"
    write_dbf(dbf, [("SG_UF", "C", 2), ("DT_NOTIFIC", "D", 8)], [
        (False, {"SG_UF": "35", "DT_NOTIFIC": "20260101"}),
        (False, {"SG_UF": "99", "DT_NOTIFIC": "20260201"}),
        (False, {"SG_UF": "35", "DT_NOTIFIC": ""}),
    ])
    crawler = BrazilSINANCrawler()
    monkeypatch.setattr(crawler, "_download_file", lambda *args: dbf)
    monkeypatch.setattr(crawler, "_decompress_to_dbf", shutil.copyfile)
    result = state_script._aggregate_file(crawler, ITEM)
    assert len(result["rows"]) == 27
    assert {r["Date"] for r in result["rows"]} == {"2026-01-01"}
    assert result["assigned_count"] == 1
    assert result["unknown_residence_count"] == 1
    assert result["undated_count"] == 1
    assert result["record_count"] == 3
    assert not any(r.get("AuthoritativeRevision") for r in result["rows"])


def test_state_script_unknown_only_file_keeps_audit_without_zeroes(tmp_path, monkeypatch):
    dbf = tmp_path / "unknown.dbf"
    write_dbf(dbf, [("SG_UF", "C", 2), ("DT_NOTIFIC", "D", 8)], [
        (False, {"SG_UF": "99", "DT_NOTIFIC": "20260101"}),
    ])
    crawler = BrazilSINANCrawler()
    monkeypatch.setattr(crawler, "_download_file", lambda *args: dbf)
    monkeypatch.setattr(crawler, "_decompress_to_dbf", shutil.copyfile)
    result = state_script._aggregate_file(crawler, ITEM)
    assert result["rows"] == []
    assert result["unknown_residence_count"] == result["record_count"] == 1
    assert result["sha256"] == hashlib.sha256(dbf.read_bytes()).hexdigest()


def test_state_script_rejection_keeps_isolated_report_and_never_exports(tmp_path, monkeypatch):
    from src.data.storage.series.quality import (
        SeriesObservationQualityReport,
        SeriesObservationQuarantinedError,
    )
    output = tmp_path / "candidate"
    monkeypatch.setattr(BrazilSINANCrawler, "fetch_file_index", lambda self: [ITEM])
    monkeypatch.setattr(state_script, "ROOT", tmp_path)
    monkeypatch.setattr(state_script, "ProcessPoolExecutor", ThreadPoolExecutor)
    monkeypatch.setattr(sys, "argv", [
        "update_br_subdivisions.py", "--apply", "--export", "--output-dir", str(output),
    ])
    row = {
        "CountryCode": "BR-SP", "Date": "2026-01-01", "Year": "2026",
        "DiseaseCode": "COQU", "Cases": "0", "SourceFiles": ITEM.filename,
        "SourceURLs": ITEM.url, "DatasetStatus": ITEM.dataset_status,
    }
    monkeypatch.setattr(state_script, "aggregate_file", lambda *args, **kwargs: {
        "file": ITEM.filename, "sha256": "a" * 64, "rows": [row],
    })
    report = SeriesObservationQualityReport(country_code="BR", source_row_count=1, analyzed_observations=1, history_observations=1)
    async def rejected(rows):
        raise SeriesObservationQuarantinedError(report)
    monkeypatch.setattr(state_script, "apply", rejected)
    with pytest.raises(SeriesObservationQuarantinedError):
        state_script.main()
    saved = json.loads((output / "import_report.json").read_text())
    assert saved["applied"] is False
    assert saved["quality_report"] == report.to_dict()
    assert saved["files"][0]["sha256"] == "a" * 64
    assert (output / "state_monthly.csv").exists()
    assert not (tmp_path / "data/processed/br/subdivisions").exists()
