"""State ingestion must retain residence geography and never import national totals."""
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import hashlib
import shutil

import pytest

from src.data.crawlers.br import BrazilSINANCrawler, SINANFile
from src.data.processors.br import BRMonthlyUpdater
from src.data.processors.br_subdivisions import aggregate_residence_file
from src.services.ingestion.countries import br
from tests.unit.test_br_subdivisions import write_dbf

ITEM = SINANFile("COQU", "Pertussis", 2026, "COQUBR26.dbc", "ftp://official/COQUBR26.dbc", "preliminary", 0, None)
FIELDS = [("SG_UF", "C", 2), ("SG_UF_NOT", "C", 2), ("DT_NOTIFIC", "D", 8)]


def fail_network(*args, **kwargs):
    raise AssertionError("organize must not access a source")


def artifact(tmp_path, payload, kind="sinan_dbc"):
    path = tmp_path / "data/raw" / (kind + ".bin")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return SimpleNamespace(
        relative_path=path.relative_to(tmp_path).as_posix(), byte_size=len(payload), sha256=hashlib.sha256(payload).hexdigest(),
        kind=kind, metadata_={"file": asdict(ITEM)},
    )


def test_residence_aggregation_reconciles_unknown_without_using_notifying_state(tmp_path):
    dbf = tmp_path / "records.dbf"
    write_dbf(dbf, FIELDS, [
        (False, {"SG_UF": "35", "SG_UF_NOT": "33", "DT_NOTIFIC": "20260103"}),
        (False, {"SG_UF": "33", "SG_UF_NOT": "35", "DT_NOTIFIC": "20260105"}),
        (False, {"SG_UF": "99", "SG_UF_NOT": "35", "DT_NOTIFIC": "20260108"}),
        (False, {"SG_UF": "35", "SG_UF_NOT": "33", "DT_NOTIFIC": ""}),
    ])
    result = aggregate_residence_file(ITEM, dbf, months={(2026, 1)})
    assert result["assigned_count"] == 2
    assert result["unknown_residence_count"] == 1
    assert result["undated_count"] == 1
    assert result["dated_record_count"] == 3
    assert len(result["rows"]) == 27
    counts = {row["CountryCode"]: row["Cases"] for row in result["rows"]}
    assert counts["BR-SP"] == counts["BR-RJ"] == "1"
    assert sum(map(int, counts.values())) == 2


def test_organize_state_reads_artifact_offline_and_filters_other_states(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    dbf = tmp_path / "records.dbf"
    write_dbf(dbf, FIELDS, [
        (False, {"SG_UF": "35", "DT_NOTIFIC": "20260103"}),
        (False, {"SG_UF": "33", "DT_NOTIFIC": "20260105"}),
    ])
    source = artifact(tmp_path, dbf.read_bytes())
    monkeypatch.setattr(BrazilSINANCrawler, "_decompress_to_dbf", staticmethod(shutil.copyfile))
    monkeypatch.setattr(BrazilSINANCrawler, "_download_file", fail_network)
    monkeypatch.setattr(BrazilSINANCrawler, "fetch_file_index", fail_network)
    result = br.organize([source], updater=BRMonthlyUpdater(country_code="BR-SP"), source="sinan_datasus",
                          months=[(2026, 1)], history_csv=tmp_path / "national.csv", country_code="BR-SP")
    assert len(result.rows) == 1
    assert result.rows[0]["Cases"] == "1"
    assert result.rows[0]["CountryCode"] == "BR-SP"
    assert result.rows[0]["GeographyKey"] == "country:BR-SP:national"
    assert result.source_audit[0]["assigned_count"] == 2
    assert result.source_audit[0]["unknown_residence_count"] == 0


def test_state_organize_refuses_national_csv_fallback(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    old = artifact(tmp_path, b"Date,Disease,Cases\n2026-01-01,Pertussis,999\n", "history_csv")
    with pytest.raises(RuntimeError, match="no usable residence rows"):
        br.organize([old], updater=BRMonthlyUpdater(country_code="BR-SP"), source="sinan_datasus",
                    months=[(2026, 1)], history_csv=tmp_path / "national.csv", country_code="BR-SP")


def test_unknown_only_month_is_not_invented_as_state_zeroes(tmp_path):
    dbf = tmp_path / "unknown.dbf"
    write_dbf(dbf, FIELDS, [(False, {"SG_UF": "99", "SG_UF_NOT": "35", "DT_NOTIFIC": "20260101"})])
    result = aggregate_residence_file(ITEM, dbf)
    assert result["unknown_residence_count"] == 1
    assert result["rows"] == []


@pytest.mark.asyncio
async def test_state_collect_does_not_hide_failed_download_with_national_history(tmp_path, monkeypatch):
    national = tmp_path / "national.csv"
    national.write_text("Date,Disease,Cases\n2026-01-01,Pertussis,999\n")
    monkeypatch.setattr(BrazilSINANCrawler, "fetch_file_index", lambda self: [ITEM])
    def unavailable(*args, **kwargs):
        raise RuntimeError("official download failed")
    monkeypatch.setattr(BrazilSINANCrawler, "_download_file", unavailable)
    writer = SimpleNamespace(artifacts=[], task_uuid="c7aa0f40-bd17-43df-a07a-5a4ee3a58a46", attempt=1)
    with pytest.raises(RuntimeError, match="official download failed"):
        await br.collect(writer, source="sinan_datasus", months=[(2026, 1)],
                         history_csv=national, country_code="BR-SP")
    assert writer.artifacts == []
