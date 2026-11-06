"""Taiwan collection preserves source evidence across bounded backfills."""
from pathlib import Path
from types import SimpleNamespace
import io
import zipfile

import pytest
import requests

from src.data.crawlers.tw import TWDiseaseSource, TaiwanNIDSSCrawler
from src.data.processors.tw import TWMonthlyUpdater
from src.services.ingestion.countries import tw


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
    writer = Writer()
    with pytest.raises(requests.ConnectionError, match="3 consecutive transport failures"):
        await tw.collect(writer, source="nidss_open_data", months=[(2025, 9), (2026, 9)], output_csv=tmp_path / "prior.csv")
    assert calls == [("1", [(2026, 9)]), ("1", [(2025, 9)]), ("2", [(2026, 9)]), ("3", [(2026, 9)])]
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
    assert await tw.collect(Writer(), source="nidss_open_data", months=[(2026, 9)], output_csv=tmp_path / "prior.csv") == []
    assert calls == ["0", "1", "2", "3", "4"]


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
