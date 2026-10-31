"""The release gate accepts marker maps and still requires quotes on timelines."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "validate_situation_release",
    ROOT / "scripts" / "validate_situation_release.py",
)
_validator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_validator)
validate = _validator.validate

SCHEMA = "gids.situation_alerts.v1"


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _tree(root: Path, *, marker: dict, alert: dict) -> None:
    outbreak = {
        "outbreak_id": "OB-1",
        "disease_id": "D050",
        "first_published": "2026-10-23",
        "last_published": "2026-10-23",
        "country_codes": ["KE"],
        "alert_count": 1,
        "notice_count": 1,
        "source_ids": ["who_don"],
        "status": "active",
        "alerts": [alert],
    }
    _write(
        root / "map.json",
        {
            "schema_version": SCHEMA,
            "generated_at": "2026-10-30T00:00:00+00:00",
            "as_of": "2026-10-30",
            "since_date": "2025-10-30",
            "point_count": 1,
            "points": [marker],
            "facets": {},
            "outbreaks": [{key: value for key, value in outbreak.items() if key != "alerts"}],
        },
    )
    _write(root / "outbreaks" / "OB-1.json", {"schema_version": SCHEMA, "as_of": "2026-10-30", **outbreak})
    _write(root / "weekly" / "index.json", {"schema_version": SCHEMA, "periods": []})
    _write(root / "monitoring.json", {"schema_version": SCHEMA, "as_of": "2026-10-30", "products": []})
    _write(
        root / "sources.json",
        {
            "schema_version": SCHEMA,
            "sources": [{"source_id": "who_don", "redistribution": "metadata_and_extract"}],
        },
    )


def _marker() -> dict:
    return {
        "alert_id": "a1",
        "outbreak_id": "OB-1",
        "source_id": "who_don",
        "disease_label": "Ebola",
        "disease_name_en": "Ebola virus disease",
        "country_code": "KE",
        "country_name_en": "Kenya",
        "status": "ongoing",
        "published_date": "2026-10-23",
    }


def _alert() -> dict:
    return {
        **_marker(),
        "url": "https://www.who.int/item/1",
        "quote": "Nine cases were reported in Kenya.",
        "summary_en": "Kenya reported nine cases.",
    }


def test_marker_map_passes_when_the_timeline_keeps_the_quote(tmp_path: Path) -> None:
    data = tmp_path / "data"
    site = tmp_path / "site"
    for prefix in ("", "zh/", "fr/"):
        (site / prefix / "situation").mkdir(parents=True)
        (site / prefix / "situation" / "index.html").write_text("<html></html>", encoding="utf-8")
    _tree(data, marker=_marker(), alert=_alert())
    result = validate(data, site)
    assert result["passed"], result["checks"]


def test_prose_on_a_map_point_fails_the_release_gate(tmp_path: Path) -> None:
    data = tmp_path / "data"
    site = tmp_path / "site"
    (site / "situation").mkdir(parents=True)
    (site / "situation" / "index.html").write_text("<html></html>", encoding="utf-8")
    (site / "zh" / "situation").mkdir(parents=True)
    (site / "zh" / "situation" / "index.html").write_text("<html></html>", encoding="utf-8")
    (site / "fr" / "situation").mkdir(parents=True)
    (site / "fr" / "situation" / "index.html").write_text("<html></html>", encoding="utf-8")
    _tree(data, marker={**_marker(), "quote": "Nine cases were reported in Kenya."}, alert=_alert())
    result = validate(data, site)
    assert result["passed"] is False
    failed = {item["id"] for item in result["checks"] if not item["passed"]}
    assert "map_markers" in failed
