"""Scoped generation reuses unchanged curves while independent lines publish."""
import copy
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.generation import site_data_export as export
from src.generation import site_data_views as views


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    output, public = tmp_path / "data", tmp_path / "public"
    export.prepare_site_output_dirs(output, public)
    diseases = [dict(disease_id=did, name_en=did, name_zh=did, category="Viral", slug=did.lower()) for did in ("D001", "D002")]
    catalogue = {d["disease_id"]: d for d in diseases}
    countries = [dict(code=code, name=name, language="en") for code, name in (("US", "United States"), ("JP", "Japan"))]
    country_data = {}
    summaries = []
    for country in countries:
        code = country["code"]
        records = [dict(disease_id=did, date="2026-01-01", year_month="2026-01", cases=1, deaths=0) for did in (("D001",) if code == "US" else ("D001", "D002"))]
        data = views.build_country_data(code, country["name"], records, catalogue)
        data["source_info"] = {"primary_scope": "official", "sources": []}
        country_data[code] = data
        export.write_pretty_json(output / "countries" / f"{code.lower()}.json", data)
        export.write_compact_json(public / "countries" / f"{code.lower()}.json", views.build_country_site_data(data))
        export.write_compact_json(public / "countries" / f"{code.lower()}-source-series.json", export.build_country_source_series_data(data))
        summaries.append({**country, **{key:data[key] for key in ("total_cases", "total_deaths", "disease_count", "date_range", "source_info", "data_layer_summary")}, "record_count":len(records), "data_available":True})
    for disease in diseases:
        did = disease["disease_id"]
        series = {code: data["disease_series"][did] for code, data in country_data.items() if did in data["disease_series"]}
        data = views.build_disease_data_from_country_series(did, disease, series)
        export.write_pretty_json(output / "diseases" / f"{did.lower()}.json", data)
        export.write_compact_json(public / "diseases" / f"{did.lower()}.json", views.build_disease_site_data(data))
    export.write_pretty_json(output / "meta.json", {"countries": summaries})
    export.write_pretty_json(output / "reports" / "index.json", [])
    @asynccontextmanager
    async def database():
        yield object()
    monkeypatch.setattr(export, "get_db", database)
    monkeypatch.setattr(export, "ensure_site_export_database_ready", AsyncMock())
    monkeypatch.setattr(export, "load_standard_diseases", lambda *_: copy.deepcopy(diseases))
    monkeypatch.setattr(export, "load_disease_ontology", lambda: SimpleNamespace(to_dict=lambda: {}))
    monkeypatch.setattr(export, "enrich_diseases_with_ontology", lambda *_: None)
    monkeypatch.setattr(export, "should_generate_public_disease_page", lambda *_: True)
    monkeypatch.setattr(export, "get_config", lambda: SimpleNamespace(database=SimpleNamespace(pool_size=2), literature=SimpleNamespace(public_article_limit=100)))
    monkeypatch.setattr(export, "has_population_table", AsyncMock(return_value=False))
    monkeypatch.setattr(export, "has_table", AsyncMock(return_value=True))
    monkeypatch.setattr(export, "_fetch_publish_states", AsyncMock(return_value=None))
    monkeypatch.setattr(export, "_published_country_codes_by_disease", AsyncMock(side_effect=lambda _: {"D001":{"US", "JP"}, "D002":{"JP"}}))
    monkeypatch.setattr(export, "fetch_countries", AsyncMock(return_value=countries))
    monkeypatch.setattr(export, "fetch_disease_knowledge_briefs", AsyncMock(return_value={}))
    monkeypatch.setattr(export, "fetch_country_briefs", AsyncMock(return_value={}))
    literature = AsyncMock(return_value={"articles": []})
    monkeypatch.setattr(export, "collect_literature_export", literature)
    monkeypatch.setattr(export, "latest_situation_snapshot", AsyncMock(return_value={}))
    monkeypatch.setattr(export, "attach_surveillance_evidence", lambda data, *_args, **_kwargs: data)
    return SimpleNamespace(output=output, public=public, country_data=country_data, diseases=diseases, literature=literature)


async def test_empty_scope_does_not_read_or_project_any_curve(snapshot, monkeypatch):
    def no_curve(*_args, **_kwargs):
        raise AssertionError("unchanged curve was read or projected")
    monkeypatch.setattr(export, "_collect_country_export", no_curve)
    monkeypatch.setattr(export, "build_country_site_data", no_curve)
    monkeypatch.setattr(export, "build_disease_site_data", no_curve)
    read = export._read_json_file
    def metadata_only(path, default):
        if path.parent.name in {"countries", "diseases"}:
            no_curve()
        return read(path, default)
    monkeypatch.setattr(export, "_read_json_file", metadata_only)
    context = await export.collect_site_export_context(snapshot.output, incremental_country_codes=[], incremental_site=True, public_site_data_dir=snapshot.public)
    assert context["changed_disease_ids"] == []
    assert context["incremental_country_codes"] == []
    assert all(item["unchanged_curve"] for item in context["country_exports"] + context["disease_exports"])
    assert snapshot.literature.await_args.kwargs["surveillance_coverage"] == {"D001":{"US", "JP"}, "D002":{"JP"}}


@pytest.mark.parametrize("remove_series", [False, True])
async def test_us_scope_only_projects_us_and_affected_disease(snapshot, monkeypatch, remove_series):
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (snapshot.output / "countries" / "jp.json", snapshot.public / "countries" / "jp.json", snapshot.output / "diseases" / "d002.json", snapshot.public / "diseases" / "d002.json")}
    async def collect(country, **_kwargs):
        assert country["code"] == "US"
        data = copy.deepcopy(snapshot.country_data["US"])
        data["disease_series"]["D001"]["cases"] = [9]
        data["total_cases"] = 9
        if remove_series:
            data["disease_series"] = {}
            data["total_cases"] = 0
            data["disease_count"] = 0
            data["date_range"] = {"start":None, "end":None}
        return dict(code="US", country_name="United States", country_name_zh="美国", country_data=data, records=[], source_records=[], source_info=data["source_info"])
    monkeypatch.setattr(export, "_collect_country_export", collect)
    country_projection, disease_projection = export.build_country_site_data, export.build_disease_site_data
    projected = []
    def country(data):
        projected.append(data["country_code"])
        return country_projection(data)
    def disease(data, *args):
        projected.append(data["disease_id"])
        return disease_projection(data, *args)
    monkeypatch.setattr(export, "build_country_site_data", country)
    monkeypatch.setattr(export, "build_disease_site_data", disease)
    context = await export.collect_site_export_context(snapshot.output, incremental_country_codes=["US"], public_site_data_dir=snapshot.public)
    assert projected == ["US", "D001"]
    assert context["changed_disease_ids"] == ["D001"]
    merged = next(item for item in context["disease_exports"] if item["disease_id"] == "D001")["disease_data"]
    if remove_series:
        assert "US" not in merged["country_series"]
        assert snapshot.literature.await_args.kwargs["surveillance_coverage"]["D001"] == {"JP"}
    else:
        assert merged["country_series"]["US"]["cases"] == [9]
    assert merged["country_series"]["JP"] == snapshot.country_data["JP"]["disease_series"]["D001"]
    export.write_incremental_site_export_artifacts(context, snapshot.output, snapshot.public)
    assert all((path.read_bytes(), path.stat().st_mtime_ns) == state for path, state in before.items())


async def test_knowledge_only_updates_and_withdraws_without_curve_writes(snapshot, monkeypatch):
    context = await export.collect_site_export_context(snapshot.output, incremental_site=True, public_site_data_dir=snapshot.public)
    curve_paths = [*snapshot.output.glob("countries/*.json"), *snapshot.output.glob("diseases/*.json"), *snapshot.public.glob("countries/*.json"), *snapshot.public.glob("diseases/*.json")]
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in curve_paths}
    # This test isolates orchestration; publication eligibility is covered by knowledge tests.
    monkeypatch.setattr(export, "build_disease_knowledge_fields", lambda disease, brief: {"disease_id":disease["disease_id"], "brief":brief or {}, "blocked":not bool(brief)})
    context["disease_knowledge_briefs"] = {"D001":{"en":{"brief":"new approved brief"}}}
    export.write_incremental_site_export_artifacts(context, snapshot.output, snapshot.public)
    knowledge = snapshot.output / "disease-knowledge" / "d001.json"
    assert "new approved brief" in knowledge.read_text()
    unchanged_mtime = knowledge.stat().st_mtime_ns
    export.write_incremental_site_export_artifacts(context, snapshot.output, snapshot.public)
    assert knowledge.stat().st_mtime_ns == unchanged_mtime
    stale = knowledge.parent / "retired-disease.json"
    stale.write_text('{"brief":"retired"}')
    context["disease_knowledge_briefs"] = {}
    export.write_incremental_site_export_artifacts(context, snapshot.output, snapshot.public)
    assert json.loads(knowledge.read_text()) == {"disease_id":"D001", "brief":{}, "blocked":True}
    assert not stale.exists()
    assert all((path.read_bytes(), path.stat().st_mtime_ns) == state for path, state in before.items())


async def test_empty_publication_table_reuses_existing_curves(snapshot, monkeypatch):
    monkeypatch.setattr(export, "_published_country_codes_by_disease", AsyncMock(return_value={}))
    def no_projection(*_args, **_kwargs):
        raise AssertionError("existing snapshot must preserve its projections")
    monkeypatch.setattr(export, "build_country_site_data", no_projection)
    monkeypatch.setattr(export, "build_disease_site_data", no_projection)
    context = await export.collect_site_export_context(snapshot.output, incremental_site=True, public_site_data_dir=snapshot.public)
    assert context["changed_disease_ids"] == []
    assert snapshot.literature.await_args.kwargs["surveillance_coverage"] == {"D001":{"US", "JP"}, "D002":{"JP"}}


@pytest.mark.parametrize("codes", [["UNKNOWN"], ["US", "US-UNKNOWN"]])
async def test_explicit_unknown_incremental_scope_still_fails(snapshot, codes):
    with pytest.raises(RuntimeError, match="unknown country scope"):
        await export.collect_site_export_context(snapshot.output, incremental_country_codes=codes, public_site_data_dir=snapshot.public)


@pytest.mark.parametrize("missing_table", ["public_disease_series", "country_publish_state"])
@pytest.mark.parametrize("incremental", [False, True])
async def test_missing_publication_schema_fails_before_projection(snapshot, monkeypatch, missing_table, incremental):
    monkeypatch.setattr(export, "has_table", AsyncMock(side_effect=lambda _session, name: name != missing_table))
    with pytest.raises(RuntimeError, match=f"migrated publication schema; missing {missing_table}"):
        await export.collect_site_export_context(snapshot.output, incremental_site=incremental, public_site_data_dir=snapshot.public)
    export.fetch_countries.assert_not_awaited()
    export._published_country_codes_by_disease.assert_not_awaited()
    export.ensure_site_export_database_ready.assert_not_awaited()


def _historical_ie_snapshot(snapshot, monkeypatch):
    """An earlier public release contained IE, before its gate was disabled."""
    for directory in (snapshot.output, snapshot.public):
        for path in list(directory.rglob("*.json")):
            path.write_text(path.read_text().replace('"JP"', '"IE"').replace('"Japan"', '"Ireland"'), encoding="utf-8")
            if path.name.startswith("jp"):
                path.rename(path.with_name("ie" + path.name[2:]))
    snapshot.country_data = json.loads(json.dumps(snapshot.country_data).replace('"JP"', '"IE"').replace('"Japan"', '"Ireland"'))
    monkeypatch.setattr(export, "_published_country_codes_by_disease", AsyncMock(side_effect=lambda _: {"D001":{"US", "IE"}, "D002":{"IE"}}))
    return snapshot


async def test_disabled_ie_is_removed_from_site_and_all_download_formats(snapshot, monkeypatch):
    import csv
    from openpyxl import load_workbook
    from src.generation.direct_download_files import build_direct_download_files

    snapshot = _historical_ie_snapshot(snapshot, monkeypatch)
    previous_countries = [dict(code=code, name=name, language="en") for code, name in (("US", "United States"), ("IE", "Ireland"))]
    monkeypatch.setattr(export, "fetch_countries", AsyncMock(return_value=previous_countries))
    async def existing_country(country, **_kwargs):
        code = country["code"]
        data = copy.deepcopy(snapshot.country_data[code])
        return dict(code=code, country_name=country["name"], country_name_zh=None, country_data=data, records=[], source_records=[], source_info=data["source_info"])
    monkeypatch.setattr(export, "_collect_country_export", existing_country)
    initial = await export.collect_site_export_context(snapshot.output, incremental_country_codes=["US", "IE"], public_site_data_dir=snapshot.public)
    downloads = snapshot.output.parent / "downloads"
    base = "https://raw.githubusercontent.com/example/data/main"
    first = build_direct_download_files(initial, downloads, download_url_base=base)
    us_paths = [snapshot.output / "countries/us.json", snapshot.public / "countries/us.json", snapshot.public / "countries/us-source-series.json"]
    us_paths.extend(downloads / file["relative_path"] for entry in first["countries"] if entry["code"] == "US" for part in entry["parts"] for file in part["files"].values())
    before = {path:(path.read_bytes(), path.stat().st_mtime_ns) for path in us_paths}

    # This matches fetch_countries after IE.public_release_enabled becomes false.
    monkeypatch.setattr(export, "fetch_countries", AsyncMock(return_value=[previous_countries[0]]))
    monkeypatch.setattr(export, "_collect_country_export", AsyncMock(side_effect=AssertionError("withdrawal must not recollect any country")))
    context = await export.collect_site_export_context(snapshot.output, incremental_site=True, public_site_data_dir=snapshot.public)
    assert context["retired_country_codes"] == ["IE"]
    assert context["incremental_country_codes"] == []
    assert context["changed_disease_ids"] == ["D001", "D002"]
    assert snapshot.literature.await_args.kwargs["surveillance_coverage"] == {"D001":{"US"}, "D002":set()}
    export.write_incremental_site_export_artifacts(context, snapshot.output, snapshot.public)
    second = build_direct_download_files(context, downloads, download_url_base=base, changed_country_codes=set(), changed_disease_ids=set(context["changed_disease_ids"]))
    for path in (snapshot.output / "countries/ie.json", snapshot.public / "countries/ie.json", snapshot.public / "countries/ie-source-series.json"):
        assert not path.exists()
    assert all((path.read_bytes(), path.stat().st_mtime_ns) == old for path, old in before.items())
    assert {entry["code"] for entry in second["countries"]} == {"US"}
    assert not list((downloads / "countries/ie").glob("*"))
    for disease in context["disease_exports"]:
        did = disease["disease_id"].lower()
        assert "IE" not in json.loads((snapshot.output / f"diseases/{did}.json").read_text())["country_series"]
        browser = json.loads((snapshot.public / f"diseases/{did}.json").read_text())
        assert "IE" not in {series["cc"] for series in browser["series"]}
    for entry in second["diseases"]:
        assert "IE" not in {country["code"] for country in entry["countries"]}
        for part in entry["parts"]:
            with (downloads / part["files"]["csv"]["relative_path"]).open() as stream:
                assert {row["country_code"] for row in csv.DictReader(stream)} <= {"US"}
            data = json.loads((downloads / part["files"]["json"]["relative_path"]).read_text())
            assert {row["country_code"] for row in data["records"]} <= {"US"}
            workbook = load_workbook(downloads / part["files"]["xlsx"]["relative_path"], read_only=True)
            rows = workbook["records"].values
            column = next(rows).index("country_code")
            assert {row[column] for row in rows} <= {"US"}
            workbook.close()
    # Once withdrawn metadata is committed, ordinary empty-dirty stays a no-op.
    def no_curve(*_args, **_kwargs):
        raise AssertionError("unchanged curve should remain unread after withdrawal")
    reader = export._read_json_file
    def metadata_only(path, default):
        if path.parent.name in {"countries", "diseases"}:
            no_curve()
        return reader(path, default)
    monkeypatch.setattr(export, "_read_json_file", metadata_only)
    repeat = await export.collect_site_export_context(snapshot.output, incremental_site=True, public_site_data_dir=snapshot.public)
    assert repeat["retired_country_codes"] == []
    assert repeat["changed_disease_ids"] == []


async def test_cached_full_export_invalidates_retired_ie_slices(snapshot, monkeypatch):
    snapshot = _historical_ie_snapshot(snapshot, monkeypatch)
    monkeypatch.setattr(export, "fetch_countries", AsyncMock(return_value=[dict(code="US", name="United States", language="en")]))
    monkeypatch.setattr(export, "_fetch_publish_states", AsyncMock(return_value={"US":{"content_hash":"same", "point_count":1}, "IE":{"content_hash":"retired", "point_count":2}}))
    monkeypatch.setattr(export, "_local_publish_hashes", lambda *_: {"US":"same"})
    monkeypatch.setattr(export, "_fetch_published_rows_by_country", AsyncMock(return_value={}))
    monkeypatch.setattr(export, "fetch_reports", AsyncMock(return_value=[]))
    monkeypatch.setattr(export, "_collect_country_export", AsyncMock(side_effect=AssertionError("no active country changed")))
    context = await export.collect_site_export_context(snapshot.output, public_site_data_dir=snapshot.public)
    assert context["publish_curve_refresh_codes"] == []
    assert context["retired_country_codes"] == ["IE"]
    assert context["changed_disease_ids"] == ["D001", "D002"]
    assert "IE" not in context["publish_hashes"]
    assert all("IE" not in item["disease_data"]["country_series"] for item in context["disease_exports"])


def test_incremental_merge_filters_disallowed_country_even_when_not_updated():
    merged = export._merge_incremental_disease_snapshot({"country_series":{"US":{"dates":["2026-01-01"]}, "IE":{"dates":["2026-01-01"]}}}, disease_id="D001", updated_country_data={"US":{"disease_series":{"D001":{"dates":["2026-02-01"]}}}}, jurisdiction_codes=["US"])
    assert set(merged["country_series"]) == {"US"}


async def test_export_readiness_never_imports_population_data(monkeypatch):
    import builtins
    readiness = AsyncMock(return_value=2)
    monkeypatch.setattr(export, "_ensure_site_export_database_ready", readiness)
    original_import = builtins.__import__
    def forbid_population_import(name, *args, **kwargs):
        if name == "scripts.import_wpp_population":
            raise AssertionError("site export must not import or upsert WPP population")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", forbid_population_import)
    await export.ensure_site_export_database_ready()
    readiness.assert_awaited_once_with()
