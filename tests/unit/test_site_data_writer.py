import json
from pathlib import Path

from scripts import generate_site_data
from src.generation.site_data_writer import (
    clean_generated_dir,
    existing_site_export_has_content,
    prepare_site_output_dirs,
    remove_stale_json_files,
    reset_public_data_dir,
    write_compact_json,
    write_pretty_json,
)
from src.generation.site_data_views import build_country_source_series_data


def test_json_writers_preserve_historical_bytes(tmp_path: Path) -> None:
    payload = {"name": "流感", "values": [1, None], "enabled": True}
    pretty_path = tmp_path / "pretty.json"
    compact_path = tmp_path / "compact.json"

    write_pretty_json(pretty_path, payload)
    write_compact_json(compact_path, payload)

    assert pretty_path.read_bytes() == json.dumps(
        payload, ensure_ascii=False, indent=2
    ).encode("utf-8")
    assert compact_path.read_bytes() == json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    assert not pretty_path.read_bytes().endswith(b"\n")
    assert not compact_path.read_bytes().endswith(b"\n")


def test_clean_generated_dir_only_removes_top_level_json_and_csv(
    tmp_path: Path,
) -> None:
    generated = tmp_path / "generated"
    nested = generated / "nested"
    nested.mkdir(parents=True)
    for name in ("old.json", "old.csv", "keep.txt"):
        (generated / name).write_text(name, encoding="utf-8")
    (nested / "keep.json").write_text("nested", encoding="utf-8")

    clean_generated_dir(generated)

    assert sorted(path.name for path in generated.iterdir()) == ["keep.txt", "nested"]
    assert (nested / "keep.json").read_text(encoding="utf-8") == "nested"


def test_reset_public_data_dir_replaces_only_target_tree(tmp_path: Path) -> None:
    public_root = tmp_path / "public"
    target = public_root / "site-data" / "countries"
    target.mkdir(parents=True)
    (target / "stale.json").write_text("stale", encoding="utf-8")
    sibling = public_root / "favicon.svg"
    sibling.write_text("icon", encoding="utf-8")

    reset_public_data_dir(target)

    assert target.is_dir()
    assert list(target.iterdir()) == []
    assert sibling.read_text(encoding="utf-8") == "icon"


def test_prepare_site_output_dirs_preserves_existing_artifacts_until_reconciled(
    tmp_path: Path,
) -> None:
    output = tmp_path / "build-data"
    public = tmp_path / "public-data"
    for dirname in ("countries", "diseases", "disease-knowledge", "reports"):
        directory = output / dirname
        directory.mkdir(parents=True)
        (directory / "stale.json").write_text("stale", encoding="utf-8")
        (directory / "keep.md").write_text("keep", encoding="utf-8")
    (public / "countries").mkdir(parents=True)
    (public / "countries" / "stale.json").write_text("stale", encoding="utf-8")
    (public / "other.txt").write_text("keep", encoding="utf-8")

    prepare_site_output_dirs(output, public)

    for dirname in ("countries", "diseases", "disease-knowledge", "reports"):
        assert (output / dirname / "stale.json").exists()
        assert (output / dirname / "keep.md").read_text(encoding="utf-8") == "keep"
    assert (public / "countries" / "stale.json").exists()
    assert (public / "diseases").is_dir()
    assert (public / "other.txt").read_text(encoding="utf-8") == "keep"

    assert remove_stale_json_files(output / "countries", {"current.json"}) == 1
    assert not (output / "countries" / "stale.json").exists()
    assert (output / "countries" / "keep.md").exists()


def test_existing_site_export_content_detection_and_legacy_reexports(
    tmp_path: Path,
) -> None:
    assert not existing_site_export_has_content(tmp_path)
    (tmp_path / "meta.json").write_text("not-json", encoding="utf-8")
    assert not existing_site_export_has_content(tmp_path)

    write_pretty_json(
        tmp_path / "meta.json",
        {"total_reports": 0, "countries": [{"disease_count": 0, "total_cases": 1}]},
    )
    assert existing_site_export_has_content(tmp_path)
    assert generate_site_data.clean_generated_dir is clean_generated_dir
    assert generate_site_data.reset_public_data_dir is reset_public_data_dir
    assert generate_site_data.existing_site_export_has_content is existing_site_export_has_content


def test_source_series_payload_keeps_only_complete_plottable_observations() -> None:
    payload = build_country_source_series_data(
        {
            "country_code": "JP",
            "disease_series": {
                "D001": {
                    "source_series": [
                        {"series_code": "valid", "dates": ["2026-01-01"], "values": [3]},
                        {"series_code": "invalid", "dates": ["2026-01-01"], "values": []},
                    ]
                },
                "D002": {"source_series": [{"series_code": "metadata-only"}]},
            },
        }
    )

    assert payload == {
        "v": 1,
        "country_code": "JP",
        "series": {"D001": [{"series_code": "valid", "dates": ["2026-01-01"], "values": [3]}]},
    }
