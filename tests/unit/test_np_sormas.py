"""Source counts and stable storage identities from real public bulletin bytes."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.data.crawlers.np_sormas import API_URL, WEEKS_URL, NepalSORMASContractError, discover_weeks, parse_bulletin
from src.data.processors.np_sormas import NepalSORMASUpdater
from src.data.storage.series_observation_store import SeriesObservationStore
from src.services.ingestion.countries.np_sormas import organize

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/ingestion/NP"


def report(week=45):
    return {"year": 2026, "week": week, "url": f"{API_URL}/bulletin/2026/{week}"}


def document(week=45):
    return json.loads((FIXTURES / f"bulletin-2026-{week}.json").read_bytes())


def parse(value=None, week=45):
    return parse_bulletin(json.dumps(value or document(week)).encode(), report(week))[0]


def test_real_bulletin_week_counts_provinces_and_totals_are_distinct():
    rows = parse()
    assert len(rows) == 72
    ili = [r for r in rows if r["RawDiseaseLabel"] == "Influenza Like Illness"]
    assert next(r["Cases"] for r in ili if r["Geography"] == "Nepal") == "696"
    assert next(r["Cases"] for r in ili if r["Geography"] == "Sudurpaschim") == "516"
    assert len({r["GeographyKey"] for r in ili}) == 8
    assert all(r["Cases"] != "6750" for r in ili)  # trend-window total is not this week's cases
    assert all(r["PublicReleaseEnabled"] == "false" for r in rows)
    assert all(r["DateRole"] == "source_year_storage_anchor_only" for r in rows)


def test_explicit_zero_missing_and_conflicting_totals():
    d = document()
    kala = next(r for r in d["distributionRows"] if r["label"] == "Kala-azar")
    del kala["provinceCounts"]["Madhesh"]
    rows = [r for r in parse(d) if r["RawDiseaseLabel"] == "Kala-azar"]
    assert next(r["MissingValueStatus"] for r in rows if r["Geography"] == "Madhesh") == "source_missing"
    assert next(r["Cases"] for r in rows if r["Geography"] == "Bagmati") == "0"
    d = document()
    d["distributionRows"][0]["provinceCounts"]["Koshi"] += 1
    with pytest.raises(NepalSORMASContractError, match="reconcile"):
        parse(d)


def test_identity_ignores_changing_publication_and_trend_window_metadata():
    before = parse()
    d = document()
    d["lockedAt"] = "2026-11-17T00:00:00Z"
    d["periodStart"] = "2026-10-01T00:00:00Z"
    assert [r["Dimensions"] for r in before] == [r["Dimensions"] for r in parse(d)]


def test_week_index_and_requested_identity_validation():
    weeks = discover_weeks((FIXTURES / "weeks.json").read_bytes(), start_year=2026)
    assert weeks[0]["week"] == 45
    assert len(weeks) == 15
    with pytest.raises(NepalSORMASContractError, match="requested"):
        parse_bulletin(json.dumps(document()).encode(), report(19))
    d = document()
    d["items"].append(d["items"][0])
    with pytest.raises(NepalSORMASContractError, match="duplicate"):
        parse(d)


async def test_holding_store_retains_week_and_geographic_identities(monkeypatch):
    rows = parse(week=19) + parse(week=45)
    category = SimpleNamespace(id=1, category_key="sormas_test", source_id="SRC_NP_EDCD_SORMAS",
        source_code="sormas_test", canonical_source_label="test", definition_version=rows[0]["DefinitionVersion"],
        source_definition=None, source_definition_uri=None)
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: category)),
                         add=lambda _: None, flush=AsyncMock())
    # Resolution is intentionally mocked for unreviewed categories; this test
    # exercises holding identities independently of unrelated catalogue edits.
    store = SeriesObservationStore(ontology=SimpleNamespace())
    monkeypatch.setattr(store, "_resolve_series", lambda **kwargs: [])
    monkeypatch.setattr(store, "_load_holding_series", AsyncMock(return_value=None))
    # One category per call, matching discovery's source-category association.
    rows = [r for r in rows if r["RawDiseaseLabel"] == "Influenza Like Illness"]
    facts, handled = await store._build_holding_observations(db, rows, "NP", source_id="SRC_NP_EDCD_SORMAS",
                                                           value_field="Cases", geography_key=None)
    assert handled == len(facts) == 16
    assert len({(r["time"], r["series_code"], r["geography_key"], r["dimension_key"]) for r in facts}) == 16
    assert {r["dimensions"]["source_week"] for r in facts} == {19, 45}
    assert len({r["geography_key"] for r in facts}) == 8


def test_offline_replay_integrity_and_missing_report_audit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    artifacts = []
    def artifact(payload, url, endpoint):
        path = Path("data/raw/np") / str(len(artifacts))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        result = SimpleNamespace(relative_path=str(path), source_url=url, byte_size=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(), metadata_={"endpoint": endpoint})
        artifacts.append(result)
        return path
    artifact(json.dumps({"reports": [report(19), report(45)], "failed_downloads": [],
                        "requested_start_year": 2026}).encode(), WEEKS_URL, "plan")
    raw = artifact((FIXTURES / "bulletin-2026-45.json").read_bytes(), report()["url"], "bulletin")
    result = organize(artifacts, updater=NepalSORMASUpdater())
    assert len(result.rows) == 72
    assert result.source_audit["status"] == "partial"
    assert result.source_audit["failed_reports"][0]["stage"] == "missing_artifact"
    raw.write_bytes(b"changed")
    with pytest.raises(ValueError):
        organize(artifacts, updater=NepalSORMASUpdater())
