"""Dynamic surveillance releases publish immutable data without an Astro build."""
import copy
import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.generation import surveillance_data_export as dynamic


def _context():
    series = {"disease_id": "D001", "name_en": "Disease", "name_zh": "疾病", "slug": "disease", "dates": ["2026-01-01"], "cases": [7], "total_cases": 7, "source_series": []}
    country = {"country_code": "CA", "country_name": "Canada", "total_cases": 7, "total_deaths": 0, "disease_count": 1, "date_range": {"start": "2026-01-01", "end": "2026-01-01"}, "source_info": {}, "disease_series": {"D001": series}}
    disease = {"disease_id": "D001", "name_en": "Disease", "country_series": {"CA": series}}
    return {
        "generated_at": "2026-01-01T00:00:00Z", "incremental": False,
        "countries_simple": [{"code": "CA", "name": "Canada", "name_en": "Canada", "data_available": True}],
        "diseases": [{"disease_id": "D001", "name_en": "Disease"}],
        "country_exports": [{"code": "CA", "country_data": country, "site_data": {"v": 1, "meta": {"cc": "CA", "tc": 7}, "series": []}}],
        "disease_exports": [{"disease_id": "D001", "disease_data": disease, "site_data": {"v": 1, "meta": {"id": "D001"}, "series": []}}],
        "country_dependency_inputs": {}, "retired_country_codes": [],
    }


def test_json_generation_pins_immutable_objects_and_keeps_old_snapshot(tmp_path):
    state, public = tmp_path / "private", tmp_path / "public"
    context = _context()
    logical = dynamic.write_surveillance_artifacts(context, state, public)
    first = dynamic.freeze_public_generation(public, state, logical, release_id="first", generated_at=context["generated_at"])
    assert set(first["files"]) == {"meta.json", "site-status.json", "home-influenza.json", "countries/ca.json", "countries/ca-source-series.json", "countries/ca-summary.json", "diseases/d001.json", "diseases/d001-summary.json"}
    previous = {entry["url"]: (public / entry["url"]).read_bytes() for entry in first["files"].values()}
    summary = json.loads(previous[first["files"]["countries/ca-summary.json"]["url"]])
    assert summary["total_cases"] == 7
    assert summary["diseases"][0]["disease_id"] == "D001"
    assert summary["download"] is None
    for name, entry in first["files"].items():
        assert entry["url"] == f"objects/{entry['sha256']}.json"
        assert hashlib.sha256((public / entry["url"]).read_bytes()).hexdigest() == entry["sha256"]
    updated = copy.deepcopy(context)
    updated["country_exports"][0]["country_data"]["total_cases"] = 9
    updated["country_exports"][0]["site_data"]["meta"]["tc"] = 9
    logical = dynamic.write_surveillance_artifacts(updated, state, public)
    second = dynamic.freeze_public_generation(public, state, logical, release_id="second", generated_at=context["generated_at"])
    assert second["files"]["countries/ca.json"]["sha256"] != first["files"]["countries/ca.json"]["sha256"]
    assert all((public / url).read_bytes() == body for url, body in previous.items())
    assert json.loads((public / "current.json").read_text())["release_id"] == "second"
    assert not (state / "reports").exists()
    assert not (public / "reports").exists()


def test_freeze_failure_does_not_replace_current_pointer(tmp_path, monkeypatch):
    state, public = tmp_path / "private", tmp_path / "public"
    context = _context()
    logical = dynamic.write_surveillance_artifacts(context, state, public)
    dynamic.freeze_public_generation(public, state, logical, release_id="first", generated_at=context["generated_at"])
    before = (public / "current.json").read_bytes()
    (public / "meta.json").write_text('{"updated":true}')
    monkeypatch.setattr(dynamic.shutil, "copyfile", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        dynamic.freeze_public_generation(public, state, logical, release_id="failed", generated_at=context["generated_at"])
    assert (public / "current.json").read_bytes() == before


@pytest.mark.parametrize("relative", [
    "generation/site_series_projection.py", "services/disease_series_policy.py",
    "core/reporting_period.py", "core/disease_cutover.py",
])
def test_projection_rule_change_invalidates_completed_baseline(tmp_path, monkeypatch, relative):
    state, public = tmp_path / "private", tmp_path / "public"
    context = _context()
    logical = dynamic.write_surveillance_artifacts(context, state, public)
    manifest = dynamic.freeze_public_generation(public, state, logical, release_id="before", generated_at=context["generated_at"])
    dynamic._complete_generation(state, public, logical, manifest)
    assert dynamic.completed_generation(state, public) is not None

    target = Path(dynamic.__file__).resolve().parents[1] / relative
    original_read = Path.read_bytes

    def changed_source(path):
        content = original_read(path)
        return content + b"\n# changed projection rule\n" if path == target else content

    monkeypatch.setattr(Path, "read_bytes", changed_source)
    assert dynamic.completed_generation(state, public) is None


async def test_data_export_uses_surveillance_collection_and_freezes_ack_projection(tmp_path, monkeypatch):
    from src.generation import site_data_export
    from src.core import database
    context = _context()
    collector = AsyncMock(return_value=context)
    monkeypatch.setattr(site_data_export, "collect_site_export_context", collector)
    db = type("Database", (), {"execute": AsyncMock(return_value=[])})()
    @asynccontextmanager
    async def session():
        yield db
    monkeypatch.setattr(database, "get_db", session)
    state, public = tmp_path / "private", tmp_path / "public"
    manifest = await dynamic.export_surveillance_data(state, public, release_id="dynamic-release", public_url_base="https://data.example/surveillance")
    assert collector.await_args.kwargs["surveillance_only"] is True
    assert manifest["files"]["meta.json"]["url"].startswith("https://data.example/surveillance/objects/")
    frozen = json.loads((state / "_release-projection/manifest.json").read_text())
    assert set(frozen["countries"]) == {"CA"}
    db.execute.assert_awaited_once()
    assert dynamic.completed_generation(state, public)["release_id"] == "dynamic-release"
    # A partial later export must not authorize using the mutated baseline.
    (state / "countries/ca.json").write_text('{"partial":true}')
    assert dynamic.completed_generation(state, public) is None


async def test_incremental_data_export_rejects_unattested_meta(tmp_path):
    state, public = tmp_path / "private", tmp_path / "public"
    state.mkdir()
    (state / "meta.json").write_text('{"countries":[]}')
    with pytest.raises(ValueError, match="attested completed generation"):
        await dynamic.export_surveillance_data(state, public, release_id="partial", incremental_country_codes=["CA"], incremental=True)


def test_summaries_keep_national_coverage_and_latest_nonmissing_period():
    series = {"disease_id": "D001", "dates": ["2026-01-05", "2026-01-12", "2026-01-19"], "cases": [1, 4, None], "period_granularity": "weekly"}
    country = dynamic.country_summary({"country_code": "CA", "disease_series": {"D001": series}}, generated_at="test")
    disease = country["diseases"][0]
    assert disease["latest_cases"] == 4
    assert disease["latest_period_start"] == "2026-01-12"
    assert disease["latest_period_end_exclusive"] == "2026-01-19"
    assert disease["date_range"] == {"start": "2026-01-05", "end": "2026-01-19"}
    summary = dynamic.disease_summary({"disease_id": "D001", "country_series": {"CA": series, "CA-ON": {"dates": ["2020-01-01"] * 10}}}, generated_at="test", countries={"CA": {"name": "Canada"}, "CA-ON": {"location_type": "subdivision", "parent_code": "CA"}})
    assert summary["country_count"] == summary["subdivision_count"] == 1
    assert summary["preferred_country"] == "CA"
    assert [item["code"] for item in summary["reporting_countries"]] == ["CA"]


async def test_partial_disease_snapshot_has_unknown_country_high_water(tmp_path, monkeypatch):
    from src.generation import site_data_export
    from src.core import database
    context = _context()
    collector = AsyncMock(return_value=context)
    monkeypatch.setattr(site_data_export, "collect_site_export_context", collector)
    db = type("Database", (), {"execute": AsyncMock(return_value=[])})()
    @asynccontextmanager
    async def session():
        yield db
    monkeypatch.setattr(database, "get_db", session)
    state, public = tmp_path / "private", tmp_path / "public"
    await dynamic.export_surveillance_data(state, public, release_id="full")
    db.execute.reset_mock()
    partial = copy.deepcopy(context)
    partial.update(incremental=True, incremental_country_codes=["CA"], partial_country_codes=["CA"])
    collector.return_value = partial
    await dynamic.export_surveillance_data(state, public, release_id="partial", incremental=True, incremental_country_codes=["CA"])
    db.execute.assert_not_awaited()
    projection = json.loads((state / "_release-projection/ca.json").read_text())
    assert projection["source_high_water"] is None


async def test_projection_code_change_cannot_promote_pointer_or_completion(tmp_path, monkeypatch):
    from src.generation import site_data_export
    from src.core import database
    monkeypatch.setattr(site_data_export, "collect_site_export_context", AsyncMock(return_value=_context()))
    db = type("Database", (), {"execute": AsyncMock(return_value=[])})()
    @asynccontextmanager
    async def session():
        yield db
    monkeypatch.setattr(database, "get_db", session)
    versions = iter(("original", "changed"))
    monkeypatch.setattr(dynamic, "_generation_version", lambda: next(versions))
    state, public = tmp_path / "private", tmp_path / "public"
    with pytest.raises(ValueError, match="implementation changed"):
        await dynamic.export_surveillance_data(state, public, release_id="interrupted")
    assert not (public / "current.json").exists()
    assert not (state / dynamic.COMPLETED_GENERATION).exists()


def test_home_influenza_keeps_real_extrema_and_missing_values_with_bounded_size():
    from datetime import date, timedelta
    cases = [index % 9 for index in range(5000)]
    cases[1234] = 100_000
    cases[2500] = None
    dates = [(date(2010, 1, 1) + timedelta(days=index)).isoformat() for index in range(5000)]
    result = dynamic.home_influenza_series({"disease_series": {"D038": {"dates": dates, "cases": cases}}})
    assert len(result["cases"]) <= 242
    assert max(value for value in result["cases"] if value is not None) == 100_000
    assert None in result["cases"]
    assert result["date_range"] == {"start": dates[0], "end": dates[-1]}
    assert all(cases[dates.index(day)] == value for day, value in zip(result["dates"], result["cases"]))
    assert len(json.dumps(result).encode()) < 10_000


def test_unrelated_updates_reuse_hero_without_reading_country_history(tmp_path, monkeypatch):
    state, public = tmp_path / "private", tmp_path / "public"
    context = _context()
    dynamic.write_surveillance_artifacts(context, state, public)
    before = (public / "home-influenza.json").read_bytes()
    monkeypatch.setattr(dynamic, "_read", lambda path: (_ for _ in ()).throw(AssertionError(f"Unexpected history read: {path}")))
    context["generated_at"] = "2026-02-01T00:00:00Z"
    dynamic.write_surveillance_artifacts(context, state, public)
    assert (public / "home-influenza.json").read_bytes() == before


def test_home_updates_only_changed_country_and_removes_retired_series(tmp_path, monkeypatch):
    state, public = tmp_path / "private", tmp_path / "public"
    context = _context()
    template = context["country_exports"][0]
    context["countries_simple"] = [{"code": code, "data_available": True} for code in ("JP", "CN")]
    context["country_exports"] = []
    for code in ("JP", "CN"):
        item = copy.deepcopy(template)
        item["code"] = item["country_data"]["country_code"] = code
        series = item["country_data"]["disease_series"].pop("D001")
        series["disease_id"] = "D038"
        item["country_data"]["disease_series"]["D038"] = series
        context["country_exports"].append(item)
    dynamic.write_surveillance_artifacts(context, state, public)
    context["country_exports"][1]["unchanged_curve"] = True
    context["country_exports"][0]["country_data"]["disease_series"]["D038"]["cases"] = [11]
    original_read = dynamic._read
    def small_reads_only(path):
        assert path == public / "home-influenza.json", "Must reuse the other country's compressed curve"
        return original_read(path)
    monkeypatch.setattr(dynamic, "_read", small_reads_only)
    dynamic.write_surveillance_artifacts(context, state, public)
    hero = original_read(public / "home-influenza.json")["series"]
    assert hero["JP"]["cases"] == [11] and hero["CN"]["cases"] == [7]
    context["countries_simple"] = context["countries_simple"][:1]
    context["country_exports"] = context["country_exports"][:1]
    context["country_exports"][0]["unchanged_curve"] = True
    context["retired_country_codes"] = ["CN"]
    dynamic.write_surveillance_artifacts(context, state, public)
    assert set(original_read(public / "home-influenza.json")["series"]) == {"JP"}
