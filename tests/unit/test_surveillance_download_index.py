from copy import deepcopy
import json

from src.generation.surveillance_download_index import build_download_index, refresh_summary_download


def part(name, *, current=False, records=10):
    return {"id": name, "label": name, "is_current": current,
            "record_count": records, "window": {"start": "2020-01-01", "end": "2025-12-31"},
            "date_range": {"start": "2021-01-01", "end": "2024-01-01"},
            "content_sha256": "internal", "files": {kind: {
                "url": f"https://raw.githubusercontent.com/owner/repo/main/{name}.{kind}",
                "filename": f"{name}.{kind}", "bytes": 100, "sha256": "a" * 64,
                "relative_path": f"countries/ca/{name}.{kind}", "internal_debug": "discard",
            } for kind in ("csv", "json", "xlsx")}}


def test_index_preserves_total_and_current_counts_and_original_archive_date():
    current = part("current", current=True, records=7)
    manifest = {"generated_at": "2026-01-02", "surveillance_release_id": "data-original",
                "formats": ["csv", "json", "xlsx"],
                "countries": [{"code": "CA-ON", "id": "ca-on", "record_count": 120,
                               "generated_at": "2025-12-31", "date_range": {"start": "1900-01-01", "end": "2025-01-01"},
                               "parts": [part("history"), current, part("tail")]}]}
    before = deepcopy(manifest)
    result = build_download_index(manifest, countries=[{"code": "CA-ON", "location_type": "subdivision", "parent_code": "CA"}])
    entry = result["countries"][0]
    assert result["generated_at"] == "2026-01-02"
    assert result["surveillance_release_id"] == "data-original"
    assert entry["generated_at"] == "2025-12-31"
    assert entry["record_count"] == 120
    assert entry["date_range"] == manifest["countries"][0]["date_range"]
    assert entry["parts_count"] == 3
    assert len(entry["parts"]) == 1
    assert entry["parts"][0]["id"] == "current"
    assert entry["parts"][0]["record_count"] == 7
    assert entry["parent_code"] == "CA"
    assert entry["location_type"] == "subdivision"
    assert set(entry["parts"][0]["files"]) == {"csv", "json", "xlsx"}
    for kind, file in entry["parts"][0]["files"].items():
        assert file == {k: v for k, v in current["files"][kind].items() if k != "internal_debug"}
    assert manifest == before
    entry["parts"][0]["files"]["csv"]["bytes"] = 123
    assert manifest == before


def test_index_falls_back_to_last_part_without_claiming_current():
    entry = build_download_index({"diseases": [{"id": "d1", "disease_id": "D1", "slug": "disease", "country_count": 2,
                                              "parts": [part("first"), part("last")]}]})["diseases"][0]
    assert entry["country_count"] == 2
    assert entry["parts_count"] == 2
    assert entry["parts"][0]["id"] == "last"
    assert entry["parts"][0]["is_current"] is False


def test_empty_downloads_do_not_invent_a_date_or_nonexistent_files():
    result = build_download_index(None)
    assert result["generated_at"] is None
    assert result["surveillance_release_id"] is None
    assert result["countries"] == result["diseases"] == []
    entry = build_download_index({"diseases": [{"id": "d0", "record_count": 0, "parts": []}]})["diseases"][0]
    assert entry["parts_count"] == 0
    assert entry["parts"] == []


def test_payload_size_does_not_grow_with_historical_parts_or_source_provenance():
    historical = [part(str(year)) for year in range(10000)]
    manifest = {"diseases": [{"disease_id": "D1", "record_count": 100000,
                             "parts": historical + [part("now", current=True)],
                             "source_info": {"sources": ["large provenance"] * 10000},
                             "countries": [{"code": "CA"}] * 10000,
                             "input_sha256": "internal", "dependency_sha256": "internal"}]}
    result = build_download_index(manifest)
    payload = json.dumps(result)
    assert len(payload) < 2500
    assert result["diseases"][0]["parts_count"] == 10001
    assert "source_info" not in payload
    assert "input_sha256" not in payload
    assert "dependency_sha256" not in payload
    assert "large provenance" not in payload


def test_unchanged_summary_gets_new_archives_without_relabelling_curve_date():
    summary = {"generated_at": "2020-old-curves", "total_cases": 10,
               "diseases": [{"name": "original"}], "download": {"generated_at": "old-archive"}}
    download = {"generated_at": "2026-new-archive", "record_count": 100, "parts": [part("now")]}
    original = deepcopy(summary)
    refreshed = refresh_summary_download(summary, download)
    assert refreshed["generated_at"] == "2020-old-curves"
    assert refreshed["total_cases"] == 10
    assert refreshed["download"]["generated_at"] == "2026-new-archive"
    assert refreshed["download"]["record_count"] == 100
    assert summary == original
    download["parts"][0]["record_count"] = 999
    assert refreshed["download"]["parts"][0]["record_count"] == 10
    assert refresh_summary_download(summary, None)["download"] is None


def test_cell_identity_survives_compact_index():
    cell = {**part('2026-2029', current=True), 'country_code': 'CN', 'disease_id': 'D007'}
    result = build_download_index({'countries': [{'code': 'CN', 'parts': [cell]}]})
    current = result['countries'][0]['parts'][0]
    assert current['country_code'] == 'CN'
    assert current['disease_id'] == 'D007'
