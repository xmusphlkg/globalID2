"""Incremental disease downloads retain published rows outside selected scopes."""
import copy
import json
import subprocess

import pytest

from src.generation import direct_download_files as downloads


def _series(dates, values):
    return {"dates": dates, "cases": values, "deaths": [0] * len(dates),
            "projection_policy": "temporal_handoff", "source_series": [{
                "series_code": "source", "metric_type": "case_notifications",
                "temporal_granularity": "monthly", "dates": dates, "values": values,
            }]}


@pytest.fixture
def baseline(tmp_path):
    data = {"disease_id": "D007", "slug": "test", "name_en": "Test", "name_zh": "测试", "category": "Viral",
            "country_series": {"CN": _series(["2010-01-01", "2026-01-01"], [1, 2]),
                               "IS": _series(["2026-01-01"], [3]), "IE": _series(["2026-01-01"], [4])}}
    names = {"CN": "China", "IS": "Iceland", "IE": "Ireland"}
    rows = downloads.build_disease_download_rows(data, {}, names)
    # Historical manifests may already contain public-only projection rows.
    # They must be preserved as such, not fabricated from today's lossy cache.
    for row in rows:
        if row["country_code"] == "CN" and row["record_kind"] == "public_projection":
            row["projection_policy"] = "published_series"
    dataset = {"kind": "disease", "directory": "diseases", "dataset_id": "D007", "path_id": "d007", "slug": "test", "name": "Test"}
    base = "https://raw.githubusercontent.com/example/data/main"
    built = downloads._build_dataset_parts_task(kind="disease", dataset=dataset, rows=rows, output_dir=tmp_path,
        download_url_base=base, max_file_bytes=100_000, existing_parts={})
    return data, names, rows, built, base


def test_merge_keeps_unselected_provenance_removes_retired_country_and_reuses_other_window(tmp_path, baseline, monkeypatch):
    data, names, old_rows, built, base = baseline
    existing = {part["id"]: part for part in built["parts"]}
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in built["paths"]}
    current = copy.deepcopy(data)
    current["country_series"]["CN"] = {"dates": ["2010-01-01"], "cases": [999], "projection_policy": "published_series"}
    current["country_series"]["IS"] = _series(["2026-01-01"], [9])
    names.pop("IE")
    calls = []
    xlsx = downloads._xlsx_bytes
    def record(metadata, rows):
        calls.append(metadata["partition_id"])
        return xlsx(metadata, rows)
    monkeypatch.setattr(downloads, "_xlsx_bytes", record)
    result = downloads._build_disease_parts_task({"disease_id": "D007", "disease_data": current}, {}, names,
        tmp_path, base, 100_000, existing, changed_country_codes={"IS"})
    rows = downloads._sort_rows([row for part in result["parts"]
        for row in json.loads((tmp_path / part["files"]["json"]["relative_path"]).read_text())["records"]])
    assert [row for row in rows if row["country_code"] == "CN"] == [row for row in old_rows if row["country_code"] == "CN"]
    assert {row["country_code"] for row in rows} == {"CN", "IS"}
    assert {row["value"] for row in rows if row["country_code"] == "IS"} == {9}
    assert result["preserved_legacy_projection_rows"] == 2
    assert calls == ["2026-2029"]
    assert result["reused_partitions"] == 1
    for path, state in before.items():
        if "2010-2014" in path.name:
            assert (path.read_bytes(), path.stat().st_mtime_ns) == state
    repeated = downloads._build_disease_parts_task({"disease_id": "D007", "disease_data": current}, {}, names,
        tmp_path, base, 100_000, {part["id"]: part for part in result["parts"]}, changed_country_codes={"IS"})
    assert repeated["rebuilt_partitions"] == 0
    assert repeated["changed"] == 0
    assert calls == ["2026-2029"]


def test_selected_lossy_country_has_localized_error(tmp_path, baseline):
    data, names, _, built, base = baseline
    data["country_series"]["IS"]["projection_policy"] = "published_series"
    with pytest.raises(ValueError, match="Disease D007, country IS: Public curve"):
        downloads._build_disease_parts_task({"disease_id": "D007", "disease_data": data}, {}, names,
            tmp_path, base, 100_000, {part["id"]: part for part in built["parts"]}, changed_country_codes={"IS"})


def test_baseline_json_is_verified_before_retaining_any_country(tmp_path, baseline):
    data, names, _, built, base = baseline
    file = built["parts"][0]["files"]["json"]
    path = tmp_path / file["relative_path"]
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="Disease D007, partition .*checksum"):
        downloads._build_disease_parts_task({"disease_id": "D007", "disease_data": data}, {}, names,
            tmp_path, base, 100_000, {part["id"]: part for part in built["parts"]}, changed_country_codes={"IS"})


def test_dependency_fingerprint_never_serializes_country_curve_arrays():
    class UnreadableCurve:
        def __str__(self):
            raise AssertionError("unchanged international curves were serialized")
    item = {"disease_data": {"disease_id": "D007", "country_series": {"CN": UnreadableCurve(), "IS": UnreadableCurve()}}}
    previous = {"countries": [{"code": "CN"}, {"code": "IS"}, {"code": "IE"}], "parts": [{"content_sha256": "irrelevant-output"}]}
    countries = {"cn": {"input_sha256": "cn-input"}, "is": {"input_sha256": "is-input"}}
    kwargs = dict(country_entries=countries, country_fingerprints={"is": "is-new"}, country_names={"CN": "China", "IS": "Iceland"},
                  country_sources={}, projection_code="code", max_file_bytes=100_000)
    first = downloads._disease_dependency_sha256(item, previous, **kwargs)
    previous["parts"][0]["content_sha256"] = "different-output"
    assert downloads._disease_dependency_sha256(item, previous, **kwargs) == first
    kwargs["country_fingerprints"]["is"] = "is-revised"
    assert downloads._disease_dependency_sha256(item, previous, **kwargs) != first


@pytest.mark.parametrize("damage", ["replaced", "missing"])
def test_interrupted_partition_write_recovers_only_manifest_matching_local_head(tmp_path, baseline, damage):
    data, names, old_rows, built, _ = baseline
    for args in (["init"], ["add", "."], ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "Published partitions"]):
        subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, check=True)
    part = built["parts"][0]
    path = tmp_path / part["files"]["json"]["relative_path"]
    damaged = b'{"metadata":{},"records":[]}'
    if damage == "replaced":
        path.write_bytes(damaged)
    else:
        path.unlink()
    rows, _ = downloads._merge_existing_disease_rows(
        data, [], changed_country_codes={"IS"}, country_names=names,
        output_dir=tmp_path, existing_parts={part["id"]: part for part in built["parts"]},
    )
    assert rows == downloads._sort_rows([row for row in old_rows if row["country_code"] != "IS"])
    if damage == "replaced":
        assert path.read_bytes() == damaged
    else:
        assert not path.exists()
    wrong_manifest = copy.deepcopy({part["id"]: part for part in built["parts"]})
    wrong_manifest[part["id"]]["files"]["json"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="Disease D007, partition .*checksum"):
        downloads._merge_existing_disease_rows(data, [], changed_country_codes={"IS"}, country_names=names,
            output_dir=tmp_path, existing_parts=wrong_manifest)
