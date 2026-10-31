"""SINAN source bytes preserve monthly counts and the legacy projection input."""
import hashlib
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.crawlers.br import BrazilSINANCrawler, SINANFile
from src.data.processors.br import BRMonthlyUpdater
from src.data.storage.series_observation_store import SeriesObservationStore
from src.services.ingestion.countries.br import organize
from tests.contract.compare import assert_observations_match

FIXTURE = Path(__file__).parents[1] / "fixtures/ingestion/BR/AIDABR25.dbc"


@pytest.mark.parametrize("recover_from_history", [False, True])
def test_br_monthly_source_and_observation_contract(tmp_path, monkeypatch, recover_from_history):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "br.csv"
    # This deterministic DBF envelope uses an identity decompression double.
    # Both baseline and new parser still read the DBF records themselves.
    monkeypatch.setattr(BrazilSINANCrawler, "_decompress_to_dbf", staticmethod(lambda raw, dbf: shutil.copyfile(raw, dbf)))
    monkeypatch.setattr(BrazilSINANCrawler, "_load_cached_metrics", lambda *a, **kw: None)
    monkeypatch.setattr(BrazilSINANCrawler, "_write_cached_counts", lambda *a, **kw: None)
    item = SINANFile("AIDA", "AIDS in adults", 2025, "AIDABR25.dbc", "ftp://example/AIDABR25.dbc", "final", FIXTURE.stat().st_size, datetime(2026, 1, 1, tzinfo=timezone.utc))
    monkeypatch.setattr(BrazilSINANCrawler, "fetch_file_index", lambda self: [item])
    monkeypatch.setattr(BrazilSINANCrawler, "_download_file", lambda *a: FIXTURE)
    expected = BRMonthlyUpdater(output_csv=output).refresh_source(months=[(2025, 1), (2025, 2)], load_csv_fallback=False, write_csv=False)
    payload = FIXTURE.read_bytes()
    path = Path("data/raw/br/contract/source.dbc")
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    metadata = asdict(item)
    metadata["modified_at"] = item.modified_at.isoformat()
    artifact = SimpleNamespace(relative_path=str(path), byte_size=len(payload), sha256=hashlib.sha256(payload).hexdigest(), kind="sinan_dbc", source_url=item.url, metadata_={"file": metadata})
    artifacts = [artifact]
    if recover_from_history:
        updater = BRMonthlyUpdater(output_csv=output)
        updater._write_rows_to_output_csv(expected.rows)
        cached = output.read_bytes()
        cache_path = path.with_name("history.csv")
        cache_path.write_bytes(cached)
        artifacts.insert(0, SimpleNamespace(relative_path=str(cache_path), byte_size=len(cached), sha256=hashlib.sha256(cached).hexdigest(), kind="history_csv"))
        def invalid_dbc(*args):
            raise ValueError("invalid live DBC")
        monkeypatch.setattr(BrazilSINANCrawler, "_decompress_to_dbf", staticmethod(invalid_dbc))
        expected = updater.refresh_source(months=[(2025, 1), (2025, 2)], write_csv=False)
    monkeypatch.setattr(BrazilSINANCrawler, "_download_file", lambda *a: (_ for _ in ()).throw(AssertionError("organization downloaded source")))
    monkeypatch.setattr(BrazilSINANCrawler, "fetch_file_index", lambda *a: (_ for _ in ()).throw(AssertionError("organization requested index")))
    actual = organize(artifacts, updater=BRMonthlyUpdater(output_csv=output), source="all", months=[(2025, 1), (2025, 2)], history_csv=output, start_year=2025)
    assert actual.rows == expected.rows
    assert [row["Cases"] for row in actual.rows] == ["2", "1"]
    store = SeriesObservationStore()
    before = store.build_observations(expected.rows, "BR", source_id="SRC_BR_SINAN").observations
    after = store.build_observations(actual.rows, "BR", source_id="SRC_BR_SINAN").observations
    assert after
    assert_observations_match(after, before)
