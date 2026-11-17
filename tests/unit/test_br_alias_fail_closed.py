"""An available history snapshot cannot resolve ambiguous official AFP revisions."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
from types import SimpleNamespace

import pytest

from src.data.crawlers.br import (
    BrazilSINANCrawler,
    SINANAliasConflictError,
    SINANFile,
    _deduplicate_sinan_file_aliases,
)
from src.data.processors.br import BRMonthlyUpdater
from src.services.ingestion.countries import br
from tests.unit.test_br_incremental_cache import Writer


OLD = SINANFile(
    "PFAN", "Acute flaccid paralysis", 2026, "PFANBR26.dbc",
    "ftp://official/PFANBR26.dbc", "preliminary", 1,
    datetime(2026, 9, 21, tzinfo=timezone.utc),
)
NEW = replace(OLD, prefix="PFA", filename="PFABR26.dbc", url="ftp://official/PFABR26.dbc")
HISTORY = b"Disease,DiseaseCode,Date,Cases\nAcute flaccid paralysis,PFAN,2026-01-01,999\n"


@pytest.fixture(params=[None, OLD.modified_at], ids=["missing_modified_at", "tied_modified_at"])
def ambiguous_history(request, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("src.data.crawlers.br.DEFAULT_CACHE_DIR", tmp_path / "cache")
    history = tmp_path / "data/raw/br/history.csv"
    history.parent.mkdir(parents=True)
    history.write_bytes(HISTORY)
    updater = BRMonthlyUpdater(output_csv=history)
    # Ensure that this is a usable fallback, which the conflict must bypass.
    prior = updater._load_rows(history)
    assert updater._prior_rows_candidate(prior, [(2026, 1)])[0][0]["Cases"] == "999"
    return updater, [OLD, replace(NEW, modified_at=request.param)]


def artifact(path, payload, kind, metadata=None):
    path.write_bytes(payload)
    return SimpleNamespace(
        relative_path=str(path), byte_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(), kind=kind,
        source_url=str(path), metadata_=metadata or {},
    )


@pytest.mark.asyncio
async def test_collection_alias_conflict_cannot_fall_back_to_history(ambiguous_history, monkeypatch):
    updater, files = ambiguous_history
    # Actual index discovery performs alias selection before returning.
    monkeypatch.setattr(BrazilSINANCrawler, "fetch_file_index",
                        lambda self: _deduplicate_sinan_file_aliases(files))
    writer = Writer()
    with pytest.raises(SINANAliasConflictError, match="PFA/PFAN"):
        await br.collect(writer, source="PFA", months=[(2026, 1)], history_csv=updater.output_csv)
    assert [entry.kind for entry in writer.artifacts] == ["history_csv"]
    assert writer.artifacts[0].payload == HISTORY


def test_offline_alias_conflict_cannot_fall_back_to_history(ambiguous_history):
    updater, files = ambiguous_history
    captured = [artifact(updater.output_csv, HISTORY, "history_csv")]
    for item in files:
        metadata = asdict(item)
        metadata["modified_at"] = item.modified_at.isoformat() if item.modified_at else None
        captured.append(artifact(updater.output_csv.parent / item.filename, b"x", "sinan_dbc", {"file": metadata}))
    with pytest.raises(SINANAliasConflictError, match="PFA/PFAN"):
        br.organize(captured, updater=updater, source="PFA", months=[(2026, 1)], history_csv=updater.output_csv)
    assert updater.output_csv.read_bytes() == HISTORY


def test_legacy_refresh_alias_conflict_cannot_fall_back_to_history(ambiguous_history, monkeypatch):
    updater, files = ambiguous_history
    # Use the real national crawler so selection happens before download/parse.
    monkeypatch.setattr(BrazilSINANCrawler, "fetch_file_index", lambda self: files)
    crawler = BrazilSINANCrawler(save_raw=False)
    try:
        with pytest.raises(SINANAliasConflictError, match="PFA/PFAN"):
            updater.refresh_source(source="PFA", months=[(2026, 1)], crawler=crawler, write_csv=False)
    finally:
        crawler.session.close()
    assert updater.output_csv.read_bytes() == HISTORY
