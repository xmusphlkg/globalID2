"""Build the public site from a deterministic notice-based Situation export."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ASTRO = ROOT / "astro-site"
EXPORT_DIR = ASTRO / "src" / "data" / "situation" / "international"
SCHEMA = "gids.international_authority_events.v1"
RESEARCH_SNAPSHOT_DRIFT = "Research Radar source data changed during the Astro build"
PRERENDER_MODULE_DRIFT = "dist/.prerender/chunks/"
PERIOD = "2026-W43"


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


def _notice() -> dict:
    return {
        "event_id": "who_don:e2e-1",
        "source_id": "who_don",
        "native_id": "e2e-1",
        "record_type": "outbreak_notice",
        "title_en": "Influenza - E2E fixture",
        "title_zh": "流感 - 端到端测试",
        "title_fr": "Grippe - test de bout en bout",
        "summary_en": "Deterministic end-to-end fixture notice.",
        "summary_zh": "确定性的端到端测试通报。",
        "summary_fr": "Avis de test déterministe.",
        "url": "https://www.who.int/emergencies/disease-outbreak-news/item/e2e-1",
        "source_label_en": "WHO Disease Outbreak News",
        "source_label_zh": "世卫组织疾病暴发新闻",
        "agency_en": "World Health Organization",
        "agency_zh": "世界卫生组织",
        "language": "en",
        "published_date": "2026-10-21",
        "updated_at_source": "2026-10-21",
        "iso_year": 2026,
        "iso_week": 43,
        "iso_week_key": PERIOD,
        "diseases": [{"disease_id": "D038", "name_en": "Influenza", "name_zh": "流感", "slug": "influenza"}],
        "countries": [{"code": "CN", "name_en": "China", "name_zh": "中国"}],
        "retrieved_at": "2026-10-21T00:00:00+00:00",
        "fingerprint": "e2e-fixture",
        "event_type": "outbreak_update",
        "risk_level": "low",
        "risk_rationale": "Deterministic fixture.",
        "ai": {
            "author_model": "fixture",
            "reviewer_model": "fixture",
            "review_mode": "single_model",
            "verdict": "accept",
            "rounds": 1,
            "prompt_version": "fixture",
        },
    }


def _payloads() -> dict[Path, dict]:
    notice = _notice()
    shard = {"schema_version": SCHEMA, "event_count": 1, "events": [notice]}
    digest = {
        "schema_version": SCHEMA,
        "period_key": PERIOD,
        "headline_en": "E2E fixture weekly brief",
        "headline_zh": "端到端测试周综述",
        "headline_fr": "Synthèse hebdomadaire de test",
        "points": [
            {
                "text_en": "Influenza notice from the fixture.",
                "text_zh": "测试流感通报。",
                "text_fr": "Avis de grippe de test.",
                "notice_ids": [notice["event_id"]],
            }
        ],
        "notice_count": 1,
        "generated_at": "2026-10-26T00:00:00+00:00",
        "ai": {"author_model": "fixture", "reviewer_model": "fixture", "review_mode": "single_model", "verdict": "accept", "score": 0.9, "prompt_version": "fixture"},
    }
    return {
        EXPORT_DIR / "latest.json": {
            "schema_version": SCHEMA,
            "generated_at": "2026-10-26T00:00:00+00:00",
            "window": {"since_days": 120, "since_date": "2026-06-28", "as_of": "2026-10-26"},
            "event_count": 1,
            "recent_event_count": 1,
            "recent": [notice],
            "iso_weeks": [{"iso_week_key": PERIOD, "iso_year": 2026, "iso_week": 43, "event_count": 1}],
            "diseases": [{"disease_id": "D038", "name_en": "Influenza", "name_zh": "流感", "event_count": 1}],
            "countries": [{"code": "CN", "name_en": "China", "name_zh": "中国", "event_count": 1}],
            "sources": [],
        },
        EXPORT_DIR / "archive" / "2026.json": {**shard, "year": 2026},
        EXPORT_DIR / "by-disease" / "D038.json": {**shard, "disease": notice["diseases"][0]},
        EXPORT_DIR / "by-country" / "CN.json": {**shard, "country": notice["countries"][0]},
        EXPORT_DIR / "events.json": {"schema_version": SCHEMA, "as_of": "2026-10-26", "events": []},
        EXPORT_DIR / "digests" / "index.json": {
            "schema_version": SCHEMA,
            "periods": [{"period_key": PERIOD, "headline_en": digest["headline_en"], "headline_zh": digest["headline_zh"], "headline_fr": digest["headline_fr"]}],
        },
        EXPORT_DIR / "digests" / f"{PERIOD}.json": digest,
    }


def test_notice_export_builds_situation_weekly_monthly_and_sitemap(tmp_path: Path) -> None:
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
        latest = (dist / "situation" / "index.html").read_text(encoding="utf-8")
        week = (dist / "situation" / "weekly" / PERIOD / "index.html").read_text(encoding="utf-8")
        sitemap = (dist / "sitemaps" / "situation.xml").read_text(encoding="utf-8")

        assert 'rel="canonical" href="https://globalinfectiousdisease.com/situation/"' in latest
        assert "E2E fixture weekly brief" in latest
        assert PERIOD in week and "Influenza - E2E fixture" in week
        assert (dist / "situation" / "monthly" / "2026-10" / "index.html").is_file()
        assert (dist / "zh" / "situation" / "weekly" / PERIOD / "index.html").is_file()
        assert (dist / "situation" / "methodology" / "index.html").is_file()
        assert f"https://globalinfectiousdisease.com/situation/weekly/{PERIOD}/" in sitemap
    finally:
        shutil.rmtree(EXPORT_DIR, ignore_errors=True)
        if had_export:
            shutil.copytree(backup, EXPORT_DIR)
