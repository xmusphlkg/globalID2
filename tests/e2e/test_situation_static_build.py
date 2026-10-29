"""Build the public site from a deterministic alert-based Situation export.

The fixture follows ``gids.situation_alerts.v1`` (see
``src/services/situation_notices/export.py``). The assertions only cover page
paths that survive the rewrite: ``/situation/`` in every locale, the
methodology page, the weekly change page and the situation sitemap.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("validate_situation_release", ROOT / "scripts" / "validate_situation_release.py")
_validator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_validator)
validate = _validator.validate
ASTRO = ROOT / "astro-site"
EXPORT_DIR = ASTRO / "src" / "data" / "situation" / "international"
SCHEMA = "gids.situation_alerts.v1"
RESEARCH_SNAPSHOT_DRIFT = "Research Radar source data changed during the Astro build"
PRERENDER_MODULE_DRIFT = "dist/.prerender/chunks/"
PERIOD = "2026-W43"
OUTBREAK_ID = "OB-e2efixture01"


def _retryable_astro_build_drift(completed: subprocess.CompletedProcess[str]) -> bool:
    output = completed.stdout + completed.stderr
    return (
        completed.returncode == 3 and RESEARCH_SNAPSHOT_DRIFT in output
    ) or (
        completed.returncode != 0
        and "Cannot find module" in output
        and PRERENDER_MODULE_DRIFT in output
    )


def _run_astro_build() -> subprocess.CompletedProcess[str]:
    def invoke() -> subprocess.CompletedProcess[str]:
        shutil.rmtree(ASTRO / "dist", ignore_errors=True)
        shutil.rmtree(ASTRO / ".astro", ignore_errors=True)
        return subprocess.run(
            ["npm", "run", "build:astro"],
            cwd=ASTRO,
            check=False,
            capture_output=True,
            text=True,
            # The French locale doubles the static route surface; keep this
            # deterministic build budget above production-sized render time.
            timeout=300,
        )

    completed = invoke()
    if _retryable_astro_build_drift(completed):
        completed = invoke()
    return completed


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
        "as_of": "2026-10-20",
        "published_date": "2026-10-21",
        "quote": f"{cases} confirmed cases were reported in {name_en}.",
        "summary_en": f"{name_en} reported {cases} confirmed cases.",
        "summary_zh": f"{name_zh}报告{cases}例确诊病例。",
        "summary_fr": f"{name_en} a signalé {cases} cas confirmés.",
        "ai": {"review_mode": "single_model", "input_mode": "text", "verdict": "accept", "prompt_version": "fixture"},
    }


def _payloads() -> dict[Path, dict]:
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
        EXPORT_DIR / "map.json": {
            "schema_version": SCHEMA,
            "generated_at": "2026-10-26T00:00:00+00:00",
            "as_of": "2026-10-26",
            "since_date": "2025-10-26",
            "point_count": 2,
            "points": alerts,
            "facets": facets,
            "outbreaks": [outbreak],
        },
        EXPORT_DIR / "outbreaks" / f"{OUTBREAK_ID}.json": {"schema_version": SCHEMA, "as_of": "2026-10-26", **outbreak, "alerts": alerts},
        EXPORT_DIR / "by-country" / "CN.json": {"schema_version": SCHEMA, "as_of": "2026-10-26", "country": {"code": "CN", "name_en": "China", "name_zh": "中国"}, "alert_count": 1, "alerts": alerts[:1]},
        EXPORT_DIR / "by-disease" / "D038.json": {"schema_version": SCHEMA, "as_of": "2026-10-26", "disease": {"disease_id": "D038", "name_en": "Influenza", "name_zh": "流感", "slug": "influenza"}, "alert_count": 2, "alerts": alerts},
        EXPORT_DIR / "weekly" / f"{PERIOD}.json": {"schema_version": SCHEMA, "period_key": PERIOD, **week},
        EXPORT_DIR / "weekly" / "index.json": {"schema_version": SCHEMA, "periods": [{"period_key": PERIOD, **week}]},
        EXPORT_DIR / "monitoring.json": {"schema_version": SCHEMA, "as_of": "2026-10-26", "products": []},
        EXPORT_DIR / "sources.json": {
            "schema_version": SCHEMA,
            "generated_at": "2026-10-26T00:00:00+00:00",
            "sources": [{"source_id": "who_don", "redistribution": "metadata_and_extract", "status": "ok", "fetched_count": 1, "error": None}],
        },
    }


def test_alert_export_builds_situation_pages_and_passes_the_release_gate(tmp_path: Path) -> None:
    backup = tmp_path / "international-backup"
    had_export = EXPORT_DIR.exists()
    if had_export:
        shutil.copytree(EXPORT_DIR, backup)
        shutil.rmtree(EXPORT_DIR)
    try:
        for path, payload in _payloads().items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        completed = _run_astro_build()
        assert completed.returncode == 0, completed.stdout + completed.stderr

        dist = ASTRO / "dist"
        # Frontend contract: these paths exist in en/zh/fr after the rewrite.
        latest = (dist / "situation" / "index.html").read_text(encoding="utf-8")
        assert 'rel="canonical" href="https://globalinfectiousdisease.com/situation/"' in latest
        assert "Influenza" in latest and "China" in latest
        for prefix in ("zh/", "fr/"):
            assert (dist / prefix / "situation" / "index.html").is_file()
        assert (dist / "situation" / "methodology" / "index.html").is_file()
        week = (dist / "situation" / "weekly" / PERIOD / "index.html").read_text(encoding="utf-8")
        assert PERIOD in week
        assert (dist / "zh" / "situation" / "weekly" / PERIOD / "index.html").is_file()
        sitemap = (dist / "sitemaps" / "situation.xml").read_text(encoding="utf-8")
        assert f"https://globalinfectiousdisease.com/situation/weekly/{PERIOD}/" in sitemap

        result = validate(EXPORT_DIR, dist)
        assert result["passed"], result["checks"]
    finally:
        shutil.rmtree(EXPORT_DIR, ignore_errors=True)
        if had_export:
            shutil.copytree(backup, EXPORT_DIR)
