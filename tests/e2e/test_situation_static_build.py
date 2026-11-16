"""Build the public site from a deterministic alert-based Situation export.

The fixture follows ``gids.situation_alerts.v1`` (see
``src/services/situation_notices/export.py``). The assertions only cover page
paths that survive the rewrite: ``/situation/`` in every locale, the
methodology page, the weekly change page and the situation sitemap.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from scripts.automation.isolated_site_build_fixture import copy_clean_site, fixture_build_environment
from scripts.automation.prepare_site_build_fixture import prepare_fixture


ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("validate_situation_release", ROOT / "scripts" / "validate_situation_release.py")
_validator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_validator)
validate = _validator.validate
ASTRO = ROOT / "astro-site"
SCHEMA = "gids.situation_alerts.v1"
PERIOD = "2026-W43"
OUTBREAK_ID = "OB-e2efixture01"


def _run_astro_build(site: Path) -> subprocess.CompletedProcess[str]:
    # A bounded clean fixture exercises the complete build and its default
    # performance/SEO gates without reading the developer's multi-GB corpus.
    return subprocess.run(
        ["npm", "run", "build:astro"],
        cwd=site,
        check=False,
        capture_output=True,
        text=True,
        env=fixture_build_environment(),
        timeout=180,
    )


def _alert(alert_id: str, country: str, name_en: str, name_zh: str, cases: int, deaths: int) -> dict:
    return {
        "alert_id": alert_id,
        "outbreak_id": OUTBREAK_ID,
        "notice_id": "who_don:e2e-1",
        "source_id": "who_don",
        "source_label_en": "WHO Disease Outbreak News",
        "source_label_zh": "世卫组织疾病暴发新闻",
        "agency_en": "World Health Organization",
        "agency_zh": "世界卫生组织",
        "title_en": "Influenza - E2E fixture",
        "url": "https://www.who.int/emergencies/disease-outbreak-news/item/e2e-1",
        "disease_id": "D038",
        "disease_slug": "influenza",
        "disease_label": "Influenza",
        "disease_name_en": "Influenza",
        "disease_name_zh": "流感",
        "country_code": country,
        "country_name_en": name_en,
        "country_name_zh": name_zh,
        "admin_area": None,
        "status": "ongoing",
        "cases": cases,
        "deaths": deaths,
        "count_basis": "confirmed",
        "host_type": "human",
        "count_unit": "people",
        "as_of": "2026-10-20",
        "published_date": "2026-10-21",
        "quote": f"{cases} confirmed cases were reported in {name_en}.",
        "summary_en": f"{name_en} reported {cases} confirmed cases.",
        "summary_zh": f"{name_zh}报告{cases}例确诊病例。",
        "summary_fr": f"{name_en} a signalé {cases} cas confirmés.",
        "ai": {"review_mode": "single_model", "input_mode": "text", "verdict": "accept", "prompt_version": "fixture"},
    }


def _payloads(export_dir: Path) -> dict[Path, dict]:
    alerts = [_alert("a1", "CN", "China", "中国", 12, 1), _alert("a2", "JP", "Japan", "日本", 3, 0)]
    outbreak = {
        "outbreak_id": OUTBREAK_ID,
        "disease_id": "D038",
        "disease_slug": "influenza",
        "disease_label": "Influenza",
        "disease_name_en": "Influenza",
        "disease_name_zh": "流感",
        "first_published": "2026-10-21",
        "last_published": "2026-10-21",
        "country_codes": ["CN", "JP"],
        "alert_count": 2,
        "notice_count": 1,
        "source_ids": ["who_don"],
        "status": "active",
    }
    facets = {
        "diseases": [{"id": "D038", "disease_name_en": "Influenza", "disease_name_zh": "流感", "disease_slug": "influenza", "count": 2}],
        "countries": [
            {"id": "CN", "country_name_en": "China", "country_name_zh": "中国", "count": 1},
            {"id": "JP", "country_name_en": "Japan", "country_name_zh": "日本", "count": 1},
        ],
        "sources": [{"id": "who_don", "source_label_en": "WHO Disease Outbreak News", "source_label_zh": "世卫组织疾病暴发新闻", "count": 2}],
    }
    week = {"new_outbreaks": [OUTBREAK_ID], "updated_outbreaks": [], "new_places": 2, "alerts": 2}
    return {
        export_dir / "map.json": {
            "schema_version": SCHEMA,
            "generated_at": "2026-10-26T00:00:00+00:00",
            "as_of": "2026-10-26",
            "since_date": "2025-10-26",
            "point_count": 2,
            "points": [
                {
                    key: alert[key]
                    for key in (
                        "alert_id",
                        "outbreak_id",
                        "source_id",
                        "source_label_en",
                        "source_label_zh",
                        "disease_id",
                        "disease_slug",
                        "disease_label",
                        "disease_name_en",
                        "disease_name_zh",
                        "country_code",
                        "country_name_en",
                        "country_name_zh",
                        "status",
                        "published_date",
                    )
                }
                for alert in alerts
            ],
            "facets": facets,
            "outbreaks": [outbreak],
        },
        export_dir / "outbreaks" / f"{OUTBREAK_ID}.json": {"schema_version": SCHEMA, "as_of": "2026-10-26", **outbreak, "alerts": alerts},
        export_dir / "by-country" / "CN.json": {"schema_version": SCHEMA, "as_of": "2026-10-26", "country": {"code": "CN", "name_en": "China", "name_zh": "中国"}, "alert_count": 1, "alerts": alerts[:1]},
        export_dir / "by-disease" / "D038.json": {"schema_version": SCHEMA, "as_of": "2026-10-26", "disease": {"disease_id": "D038", "name_en": "Influenza", "name_zh": "流感", "slug": "influenza"}, "alert_count": 2, "alerts": alerts},
        export_dir / "weekly" / f"{PERIOD}.json": {"schema_version": SCHEMA, "period_key": PERIOD, **week},
        export_dir / "weekly" / "index.json": {"schema_version": SCHEMA, "periods": [{"period_key": PERIOD, **week}]},
        export_dir / "monitoring.json": {"schema_version": SCHEMA, "as_of": "2026-10-26", "products": []},
        export_dir / "sources.json": {
            "schema_version": SCHEMA,
            "generated_at": "2026-10-26T00:00:00+00:00",
            "sources": [{"source_id": "who_don", "redistribution": "metadata_and_extract", "status": "ok", "fetched_count": 1, "error": None}],
        },
    }


@pytest.mark.skipif(
    not (ASTRO / "node_modules" / ".bin" / "astro").exists(),
    reason="Astro dependencies are not installed",
)
def test_alert_export_builds_situation_pages_and_passes_the_release_gate(tmp_path: Path) -> None:
    site = copy_clean_site(ASTRO, tmp_path / "astro-site")
    export_dir = site / "src" / "data" / "situation" / "international"
    # Write the actual alert contract before filling only missing build inputs.
    # This retains two human alerts, two countries, one outbreak and one week.
    for path, payload in _payloads(export_dir).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    prepare_fixture(site, environment={"CI": "1"})
    completed = _run_astro_build(site)
    (tmp_path / "astro-build.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "[performance-budget] PASS" in completed.stdout
    assert "[seo-output] PASS" in completed.stdout

    dist = site / "dist"
    # Frontend contract: these paths exist in en/zh/fr after the rewrite.
    latest = (dist / "situation" / "index.html").read_text(encoding="utf-8")
    assert 'rel="canonical" href="https://globalinfectiousdisease.com/situation/"' in latest
    assert "/situation/map-data.json" in latest
    map_data = json.loads((dist / "situation" / "map-data.json").read_text(encoding="utf-8"))
    assert any(point.get("disease_name_en") == "Influenza" for point in map_data.get("points") or [])
    assert any(point.get("country_name_en") == "China" for point in map_data.get("points") or [])
    for prefix in ("zh/", "fr/"):
        assert (dist / prefix / "situation" / "index.html").is_file()
    assert (dist / "situation" / "methodology" / "index.html").is_file()
    week = (dist / "situation" / "weekly" / PERIOD / "index.html").read_text(encoding="utf-8")
    assert PERIOD in week
    for prefix in ("zh/", "fr/"):
        assert (dist / prefix / "situation" / "weekly" / PERIOD / "index.html").is_file()
    sitemap = (dist / "sitemaps" / "situation.xml").read_text(encoding="utf-8")
    assert f"https://globalinfectiousdisease.com/situation/weekly/{PERIOD}/" in sitemap

    result = validate(export_dir, dist)
    assert result["passed"], result["checks"]
