"""Overlapping PFA/PFAN releases must not double-count AFP notifications."""
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
import hashlib
import shutil
from types import SimpleNamespace

import pytest

from src.data.crawlers.br import BrazilSINANCrawler, SINANFile, _deduplicate_sinan_file_aliases
from src.data.crawlers.br_cache import SINANContentCache
from src.data.processors.br import BRMonthlyUpdater
from src.services.ingestion.countries import br
from tests.unit.test_br_subdivisions import write_dbf


OLD = SINANFile("PFAN", "Acute flaccid paralysis", 2026, "PFANBR26.dbc",
                "ftp://official/PFANBR26.dbc", "preliminary", 1,
                datetime(2026, 9, 21, tzinfo=timezone.utc))
NEW = replace(OLD, prefix="PFA", disease_name="PFA", filename="PFABR26.dbc",
              url="ftp://official/PFABR26.dbc", modified_at=datetime(2026, 10, 29, tzinfo=timezone.utc))


@pytest.mark.parametrize("items", [[OLD, NEW], [NEW, OLD]])
def test_same_year_alias_chooses_latest_without_adding_sources(items):
    selected = _deduplicate_sinan_file_aliases(items)
    assert selected == [replace(NEW, prefix="PFAN", disease_name=OLD.disease_name)]
    assert _deduplicate_sinan_file_aliases(selected) == selected
    assert selected[0].filename == "PFABR26.dbc"


def test_final_outranks_newer_preliminary_and_other_years_remain():
    final = replace(OLD, dataset_status="final")
    previous = replace(OLD, year=2025, filename="PFANBR25.dbc", url="ftp://official/PFANBR25.dbc")
    assert _deduplicate_sinan_file_aliases([NEW, previous, final]) == [final, previous]


@pytest.mark.parametrize("other", [replace(NEW, modified_at=None), replace(NEW, modified_at=OLD.modified_at)])
def test_ambiguous_alias_chronology_fails_closed(other):
    with pytest.raises(ValueError, match="PFA/PFAN"):
        _deduplicate_sinan_file_aliases([OLD, other])


@pytest.mark.parametrize("country", ["BR", "BR-SP"])
def test_offline_replay_keeps_latest_original_and_ignores_old_alias_cache(tmp_path, monkeypatch, country):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data/raw").mkdir(parents=True)
    monkeypatch.setattr(BrazilSINANCrawler, "_decompress_to_dbf", staticmethod(shutil.copyfile))
    monkeypatch.setattr("src.data.crawlers.br.DEFAULT_CACHE_DIR", tmp_path / "cache")
    artifacts = []
    for item, count in [(OLD, 2), (NEW, 3)]:
        path = tmp_path / "data/raw" / item.filename
        write_dbf(path, [("DT_NOTIFIC", "D", 8), ("SG_UF", "C", 2)],
                  [(False, {"DT_NOTIFIC": "20260101", "SG_UF": "35"})] * count)
        metadata = asdict(item)
        metadata["modified_at"] = item.modified_at.isoformat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        artifacts.append(SimpleNamespace(kind="sinan_dbc", source_url=item.url,
                         metadata_={"file": metadata}, relative_path=str(path),
                         byte_size=path.stat().st_size, sha256=digest))
        # A pre-normalization PFA cache is a different semantic identity.
        if item.prefix == "PFA":
            SINANContentCache(tmp_path / "cache/content", item, digest,
                              version=br.STATE_PARSER_VERSION, scope=[(2026, 1)]).write(
                {"rows": [{"Cases": "999"}]})
    updater = BRMonthlyUpdater(country_code=country, output_csv=tmp_path / "absent.csv")
    if country == "BR":
        updater._write_rows_to_output_csv([
            {"Date": "2026-01-01", "DiseaseCode": "PFA", "RawDiseaseLabel": "PFA", "Cases": "999"},
            {"Date": "2026-01-01", "DiseaseCode": "PFAN", "RawDiseaseLabel": "Acute flaccid paralysis", "Cases": "2"},
        ])
        history = tmp_path / "data/raw/history.csv"
        history.write_bytes(updater.output_csv.read_bytes())
        artifacts.append(SimpleNamespace(kind="history_csv", relative_path=str(history),
                         byte_size=history.stat().st_size, sha256=hashlib.sha256(history.read_bytes()).hexdigest()))
    result = br.organize(artifacts, updater=updater, source="PFA", months=[(2026, 1)],
                         history_csv=updater.output_csv, country_code=country)
    assert len(result.rows) == 1
    assert result.rows[0]["DiseaseCode"] == "PFAN"
    assert result.rows[0]["Cases"] == "3"
    assert result.rows[0]["SourceFiles"] == "PFABR26.dbc"
    assert result.rows[0]["RawDiseaseLabel"] == "Acute flaccid paralysis"


def test_legacy_refresh_fresh_alias_wins_over_longer_cached_snapshot(tmp_path):
    updater = BRMonthlyUpdater(output_csv=tmp_path / "history.csv")
    updater._write_rows_to_output_csv([
        {"Date": "2026-01-01", "DiseaseCode": "PFA", "RawDiseaseLabel": "PFA", "Cases": "999"},
        {"Date": "2026-01-01", "DiseaseCode": "PFAN", "RawDiseaseLabel": "Acute flaccid paralysis", "Cases": "2"},
        {"Date": "2026-01-01", "DiseaseCode": "DENG", "RawDiseaseLabel": "Dengue", "Cases": "7"},
    ])
    current = {"Date": "2026-01-01", "DiseaseCode": "PFAN", "RawDiseaseLabel": "Acute flaccid paralysis", "Cases": "3"}
    crawler = SimpleNamespace(crawl_monthly_national=lambda *a, **k: SimpleNamespace(
        rows=[current], row_count=1, files_fetched=1, latest_date=date(2026, 1, 1)))
    result = updater.refresh_source(months=[(2026, 1)], crawler=crawler, write_csv=False)
    assert {r['DiseaseCode']:r['Cases'] for r in result.rows} == {'PFAN':'3','DENG':'7'}


@pytest.mark.asyncio
async def test_collect_accepts_new_alias_prefix_and_captures_only_selected_revision(tmp_path, monkeypatch):
    from tests.unit.test_br_incremental_cache import Writer

    monkeypatch.setattr("src.data.crawlers.br.DEFAULT_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(BrazilSINANCrawler, "fetch_file_index", lambda self: [OLD, NEW])
    requested = []
    def request(self, url):
        requested.append(url)
        return b"x"
    monkeypatch.setattr(BrazilSINANCrawler, "_request_with_retries", request)
    writer = Writer()
    await br.collect(writer, source="PFA", months=[(2026, 1)], history_csv=tmp_path / "none.csv")
    assert requested == [NEW.url]
    assert writer.artifacts[0].metadata_["file"]["prefix"] == "PFAN"
    assert writer.artifacts[0].metadata_["file"]["filename"] == "PFABR26.dbc"
