"""Taiwan collection preserves source evidence across bounded backfills."""
from pathlib import Path
from types import SimpleNamespace
import io
import zipfile
import json

import pytest
import requests

from src.data.crawlers.tw import TWDiseaseSource, TaiwanNIDSSCrawler
from src.data.processors.tw import TWMonthlyUpdater
from src.services.ingestion.countries import tw


async def test_legacy_import_does_not_convert_unknown_origin_to_zero(monkeypatch, tmp_path):
    updater = TWMonthlyUpdater(output_csv=tmp_path / "monthly.csv")
    async def country_id(db):
        return 1
    async def mappings(db):
        return {"aids": 7, "hiv": 7}
    monkeypatch.setattr(updater, "_get_country_id", country_id)
    monkeypatch.setattr(updater, "_load_mapping_dict", mappings)
    class DB:
        async def execute(self, statement, rows):
            self.rows = rows
    db = DB()
    rows = [
        {"Date": "2026-01-01", "RawDiseaseLabel": "aids", "Cases": "5", "LocalCases": "", "ImportedCases": ""},
        {"Date": "2026-01-01", "RawDiseaseLabel": "hiv", "Cases": "3", "LocalCases": "2", "ImportedCases": "1"},
    ]
    await updater.import_rows(db, rows, db_latest_date=None, source_latest_date=None)
    assert db.rows[0]["cases"] == 8
    metadata = json.loads(db.rows[0]["metadata"])
    assert metadata["local_cases"] is None
    assert metadata["imported_cases"] is None


class Writer:
    task_uuid = "test-tw"
    attempt = 1

    def __init__(self):
        self.artifacts = []

    async def write(self, payload, *, source_url, kind, metadata=None):
        artifact = SimpleNamespace(payload=payload, source_url=source_url, kind=kind, metadata_=metadata or {})
        self.artifacts.append(artifact)
        return artifact


def test_ods_windows_are_contiguous_bounded_and_newest_first():
    months = [(2024, month) for month in range(1, 13)] + [(2025, 1), (2025, 3), (2025, 3)]
    assert tw._ods_month_windows(months) == [[(2025, 3)], [(2025, 1)], [(2024, month) for month in range(1, 13)]]
    assert tw._ods_month_windows(None) == [None]
    with pytest.raises(ValueError, match="Invalid NIDSS month"):
        tw._ods_month_windows([(2026, 13)])


async def test_collect_retains_successes_then_stops_after_consecutive_ods_outage(monkeypatch, tmp_path):
    diseases = [TWDiseaseSource(str(i), str(i), f"https://od.cdc.gov.tw/eic/{i}.csv", "") for i in range(5)]
    calls = []
    raw_csv = "發病年份,發病月份,確定病例數\n2026,9,4\n".encode()
    class Crawler:
        raw_dir = tmp_path
        last_csv_transport_url = "http://od.cdc.gov.tw/eic/0.csv"
        session = SimpleNamespace(close=lambda: None)
        def __init__(self, **kwargs): pass
        def fetch_disease_index(self): return diseases
        def download_csv_bytes(self, disease): return raw_csv if disease.code == "0" else None
        def fetch_monthly_exports(self, disease, *, months):
            calls.append((disease.code, months))
            if len(calls) == 1:
                return [{"payload": b"ods", "source_url": "https://nidss.cdc.gov.tw/export", "metadata": {"code": disease.code, "months": months}}]
            raise requests.Timeout("upstream timed out")
    monkeypatch.setattr(tw, "TaiwanNIDSSCrawler", Crawler)
    async def no_delay(_seconds): pass
    monkeypatch.setattr(tw.asyncio, "sleep", no_delay)
    writer = Writer()
    with pytest.raises(requests.ConnectionError, match="3 consecutive transport failures"):
        await tw.collect(writer, source="nidss_open_data", months=[(2025, 9), (2026, 9)], output_csv=tmp_path / "prior.csv")
    assert calls == ([("1", [(2026, 9)])] + [("1", [(2025, 9)])] * 3
                     + [("2", [(2026, 9)])] * 3 + [("3", [(2026, 9)])] * 3)
    assert [(item.kind, item.payload) for item in writer.artifacts] == [("monthly_csv", raw_csv), ("monthly_ods", b"ods")]
    csv = writer.artifacts[0]
    assert csv.source_url == "http://od.cdc.gov.tw/eic/0.csv"
    assert csv.metadata_["canonical_source_url"] == diseases[0].monthly_csv_url
    assert csv.metadata_["transport_security"] == "http"
    monkeypatch.setattr(tw, "TaiwanNIDSSCrawler", TaiwanNIDSSCrawler)
    monkeypatch.setattr(tw, "read_artifact", lambda artifact: artifact.payload)
    updater = TWMonthlyUpdater(output_csv=tmp_path / "monthly.csv")
    normalized = tw.organize([csv], updater=updater, source="nidss_open_data", months=[(2026, 9)], output_csv=updater.output_csv)
    assert normalized.rows[0]["SourceURL"] == csv.source_url


async def test_nontransient_ods_error_resets_outage_count(monkeypatch, tmp_path):
    diseases = [TWDiseaseSource(str(i), str(i), "https://od.cdc.gov.tw/source.csv", "") for i in range(5)]
    calls = []
    class Crawler:
        raw_dir = tmp_path
        session = SimpleNamespace(close=lambda: None)
        def __init__(self, **kwargs): pass
        def fetch_disease_index(self): return diseases
        def download_csv_bytes(self, disease): return None
        def fetch_monthly_exports(self, disease, *, months):
            calls.append(disease.code)
            if disease.code == "2":
                raise ValueError("disease has no confirmed cases")
            raise requests.Timeout("upstream timed out")
    monkeypatch.setattr(tw, "TaiwanNIDSSCrawler", Crawler)
    async def no_delay(_seconds): pass
    monkeypatch.setattr(tw.asyncio, "sleep", no_delay)
    with pytest.raises(requests.ConnectionError, match="temporarily unavailable after retries"):
        await tw.collect(Writer(), source="nidss_open_data", months=[(2026, 9)], output_csv=tmp_path / "prior.csv")
    assert calls == ["0"] * 3 + ["1"] * 3 + ["2"] + ["3"] * 3 + ["4"] * 3


async def test_ods_transient_retry_recovers_without_losing_population_batch(monkeypatch):
    disease = TWDiseaseSource("061", "登革熱", "", "")
    calls, delays = [], []
    class Crawler:
        def fetch_monthly_exports(self, disease, *, months):
            calls.append(months)
            if len(calls) < 3:
                raise requests.Timeout("timed out")
            return [{"population": "local"}, {"population": "imported"}]
    async def delay(seconds): delays.append(seconds)
    monkeypatch.setattr(tw.asyncio, "sleep", delay)
    result = await tw._fetch_monthly_exports_with_retry(Crawler(), disease, [(2026, 9)])
    assert calls == [[(2026, 9)]] * 3
    assert delays == [1, 2]
    assert len(result) == 2


async def test_ods_certificate_error_is_not_retried(monkeypatch):
    disease = TWDiseaseSource("061", "登革熱", "", "")
    calls = []
    class Crawler:
        def fetch_monthly_exports(self, disease, *, months):
            calls.append(months)
            raise requests.exceptions.SSLError("certificate verify failed")
    with pytest.raises(requests.exceptions.SSLError):
        await tw._fetch_monthly_exports_with_retry(Crawler(), disease, [(2026, 9)])
    assert len(calls) == 1


async def test_successful_csv_breaks_consecutive_export_outage(monkeypatch, tmp_path):
    diseases = [TWDiseaseSource(str(i), str(i), f"https://example.test/{i}", "") for i in range(5)]
    calls = []
    class Crawler:
        raw_dir = tmp_path
        session = SimpleNamespace(close=lambda: None)
        def __init__(self, **kwargs): pass
        def fetch_disease_index(self): return diseases
        def download_csv_bytes(self, disease):
            return b"csv" if disease.code in {"2", "4"} else None
        def fetch_monthly_exports(self, disease, *, months):
            calls.append(disease.code)
            raise requests.Timeout("timed out")
    async def no_delay(_seconds): pass
    monkeypatch.setattr(tw, "TaiwanNIDSSCrawler", Crawler)
    monkeypatch.setattr(tw.asyncio, "sleep", no_delay)
    writer = Writer()
    with pytest.raises(requests.ConnectionError, match="temporarily unavailable after retries"):
        await tw.collect(writer, source="nidss_open_data", months=[(2026, 9)], output_csv=tmp_path / "prior.csv")
    assert calls == ["0"] * 3 + ["1"] * 3 + ["3"] * 3
    assert [item.metadata_["code"] for item in writer.artifacts] == ["2", "4"]


async def test_index_transport_error_with_cache_remains_retryable(monkeypatch, tmp_path):
    class Crawler:
        raw_dir = tmp_path
        session = SimpleNamespace(close=lambda: None)
        def __init__(self, **kwargs): pass
        def fetch_disease_index(self): raise requests.Timeout("index timed out")
    monkeypatch.setattr(tw, "TaiwanNIDSSCrawler", Crawler)
    prior = tmp_path / "prior.csv"
    prior.write_text("Date,Disease,Cases\n")
    writer = Writer()
    with pytest.raises(requests.Timeout, match="index timed out"):
        await tw.collect(writer, source="nidss_open_data", months=[(2026, 9)], output_csv=prior)
    assert [item.kind for item in writer.artifacts] == ["prior_csv"]


async def test_store_uses_live_rows_instead_of_overwriting_with_fallback(monkeypatch):
    from src.services.ingestion import strategies
    from src.services.ingestion.strategies import UpdaterCommitStrategy
    live = {"DiseaseCode": "061", "Cases": "25"}
    cached = {"DiseaseCode": "042", "Cases": "7", "SourceFreshness": "cache_fallback"}
    class Updater:
        country_code = "TW"
        async def get_db_latest_date(self, db): return None
    class Service:
        async def _import_rows_with_series(self, db, updater, rows, **kwargs):
            self.rows = rows
            return SimpleNamespace(inserted_or_updated=len(rows))
    async def mark_dirty(*args, **kwargs): pass
    monkeypatch.setattr(strategies, "mark_observation_changes_dirty", mark_dirty)
    service = Service()
    fetched = SimpleNamespace(rows=[live, cached], store_rows=[live], source_latest_date=None)
    result = await UpdaterCommitStrategy().commit(object(), service=service, updater=Updater(), fetched=fetched, force=False, task_uuid="test")
    assert result.inserted_or_updated == 1
    assert service.rows == [live]


def _change_ods_year(payload, year):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(payload)) as source, zipfile.ZipFile(output, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            target.writestr(name, data.replace(b"2026", str(year).encode()) if name == "content.xml" else data)
    return output.getvalue()


def test_organize_keeps_disjoint_windows_of_same_disease(monkeypatch, tmp_path):
    fixture = Path(__file__).parents[1] / "fixtures/ingestion/TW"
    artifacts = []
    for year in (2025, 2026):
        for filename, population in (("monthly_local.ods", "本土病例"), ("monthly_imported.ods", "境外移入病例")):
            payload = _change_ods_year((fixture / filename).read_bytes(), year)
            metadata = {"code": "061", "name": "登革熱", "months": [(year, 9)], "date_basis": "發病日", "population": population}
            artifacts.append(SimpleNamespace(kind="monthly_ods", payload=payload, metadata_=metadata))
    monkeypatch.setattr(tw, "read_artifact", lambda artifact: artifact.payload)
    updater = TWMonthlyUpdater(output_csv=tmp_path / "monthly.csv")
    result = tw.organize(artifacts, updater=updater, source="nidss_open_data", months=[(2025, 9), (2026, 9)], output_csv=updater.output_csv)
    assert {(row["Date"], row["Cases"]) for row in result.rows} == {("2025-09-01", "25"), ("2026-09-01", "25")}
    assert not result.source_audit["rejected_exports"]
