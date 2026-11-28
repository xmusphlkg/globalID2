"""Tests for disease projection repair in write_site_export_artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from src.generation import site_data_export


def _base_context(*, countries_simple: list[dict], disease_exports: list[dict]) -> dict:
    return {
        "all_records_by_country": {},
        "countries_simple": countries_simple,
        "country_download_entries": [],
        "country_exports": [],
        "disease_download_entries": [],
        "disease_exports": disease_exports,
        "disease_knowledge_briefs": {},
        "diseases": [{"disease_id": "D001", "name_en": "Influenza", "name_zh": "流感"}],
        "diseases_by_id": {
            "D001": {"disease_id": "D001", "name_en": "Influenza", "name_zh": "流感"}
        },
        "generated_at": "2026-11-28T00:00:00Z",
        "ontology": SimpleNamespace(concept_ids=["D001"], series_ids=[]),
        "ontology_document": {
            "registry_id": "registry",
            "schema_version": 1,
            "default_rollup_policy": "sum",
        },
        "report_details": {},
        "reports": [],
        "reuse_published_research": True,
    }


def _private_disease_snapshot() -> dict:
    return {
        "disease_id": "D001",
        "name_en": "Influenza",
        "name_zh": "流感",
        "country_series": {
            "CN": {
                "dates": ["2026-01-01", "2026-02-01"],
                "cases": [100, 120],
                "deaths": [1, 2],
                "weekly_equiv_cases": [25.0, 30.0],
                "total_cases": 220,
                "total_deaths": 3,
            },
            "US": {
                "dates": ["2026-01-01"],
                "cases": [50],
                "deaths": [0],
                "weekly_equiv_cases": [12.5],
                "total_cases": 50,
                "total_deaths": 0,
            },
        },
    }


def test_repair_disease_projection_rebuilds_compact_public_file_with_country_names(
    tmp_path: Path, monkeypatch
) -> None:
    output_dir = tmp_path / "build"
    public_dir = tmp_path / "public"
    private_path = output_dir / "diseases" / "d001.json"
    public_path = public_dir / "diseases" / "d001.json"
    private_path.parent.mkdir(parents=True)
    public_path.parent.mkdir(parents=True)

    private_snapshot = _private_disease_snapshot()
    private_path.write_text(
        json.dumps(private_snapshot, ensure_ascii=False), encoding="utf-8"
    )
    # Truncated public projection with fewer series than the lossless private snapshot
    public_path.write_text(
        json.dumps({"v": 1, "dates": [], "sources": [], "series": []}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        site_data_export,
        "build_disease_knowledge_fields",
        lambda *_: {"knowledge_display_mode": "full", "knowledge_completeness": 1.0},
    )
    monkeypatch.setattr(site_data_export, "build_about_snapshot", lambda **_: {})

    context = _base_context(
        countries_simple=[
            {"code": "CN", "name": "China", "name_zh": "中国", "data_available": True},
            {
                "code": "US",
                "name": "United States",
                "name_zh": "美国",
                "data_available": True,
            },
        ],
        disease_exports=[
            {
                "disease_id": "D001",
                "disease_data": {},
                "site_data": {"v": 1, "dates": [], "sources": [], "series": []},
                "unchanged_curve": True,
            }
        ],
    )

    site_data_export.write_site_export_artifacts(context, output_dir, public_dir)

    repaired_public = json.loads(public_path.read_text(encoding="utf-8"))
    series_by_cc = {entry["cc"]: entry for entry in repaired_public["series"]}
    assert set(series_by_cc) == {"CN", "US"}
    assert series_by_cc["CN"]["n"] == "China"
    assert series_by_cc["CN"]["n_zh"] == "中国"
    assert series_by_cc["CN"]["tc"] == 220
    assert series_by_cc["US"]["n"] == "United States"
    assert series_by_cc["US"]["n_zh"] == "美国"
    assert series_by_cc["US"]["tc"] == 50
    # Lossless private snapshot must stay intact
    assert json.loads(private_path.read_text(encoding="utf-8")) == private_snapshot


def test_unchanged_disease_projection_not_overwritten_when_complete(
    tmp_path: Path, monkeypatch
) -> None:
    output_dir = tmp_path / "build"
    public_dir = tmp_path / "public"
    private_path = output_dir / "diseases" / "d001.json"
    public_path = public_dir / "diseases" / "d001.json"
    private_path.parent.mkdir(parents=True)
    public_path.parent.mkdir(parents=True)

    private_path.write_text(
        json.dumps(_private_disease_snapshot(), ensure_ascii=False), encoding="utf-8"
    )
    existing_public = {
        "v": 1,
        "marker": "keep-existing",
        "series": [{"cc": "CN"}, {"cc": "US"}],
    }
    public_path.write_text(json.dumps(existing_public), encoding="utf-8")

    monkeypatch.setattr(
        site_data_export,
        "build_disease_knowledge_fields",
        lambda *_: {"knowledge_display_mode": "full", "knowledge_completeness": 1.0},
    )
    monkeypatch.setattr(site_data_export, "build_about_snapshot", lambda **_: {})

    context = _base_context(
        countries_simple=[
            {"code": "CN", "name": "China", "name_zh": "中国", "data_available": True},
            {
                "code": "US",
                "name": "United States",
                "name_zh": "美国",
                "data_available": True,
            },
        ],
        disease_exports=[
            {
                "disease_id": "D001",
                "disease_data": {},
                "site_data": {"v": 1, "series": []},
                "unchanged_curve": True,
            }
        ],
    )

    site_data_export.write_site_export_artifacts(context, output_dir, public_dir)

    assert json.loads(public_path.read_text(encoding="utf-8")) == existing_public
