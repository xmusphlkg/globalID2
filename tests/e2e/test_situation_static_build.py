from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ASTRO = ROOT / "astro-site"
FIXTURE = ROOT / "tests" / "fixtures" / "situation" / "public_report_v3.json"
RESEARCH_SNAPSHOT_DRIFT = "Research Radar source data changed during the Astro build"
PRERENDER_MODULE_DRIFT = "dist/.prerender/chunks/"


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
            timeout=180,
        )

    completed = invoke()
    if _retryable_astro_build_drift(completed):
        completed = invoke()
    return completed


def test_fixture_snapshot_builds_latest_month_week_json_and_seo(tmp_path: Path) -> None:
    snapshot = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # Keep the fixture signal synthetic while pointing its public disease link
    # at a real generated route so the strict SEO output audit remains valid.
    snapshot["signals"][0]["identity"].update({
        "disease_name": "Influenza",
        "disease_slug": "influenza",
    })
    weekly = json.loads(json.dumps(snapshot))
    weekly["report"].update({
        "report_id": "situation-v3-weekly-2026-W33-r1",
        "kind": "weekly",
        "period_key": "2026-W33",
        "period_start": "2026-08-10",
        "period_end": "2026-08-16",
    })
    weekly["signals"][0]["lifecycle"]["status"] = "persistent"
    weekly["summary"].update({"new_count": 0, "persistent_count": 1})
    monthly = json.loads(json.dumps(snapshot))
    monthly["report"].update({
        "report_id": "situation-v3-monthly-2026-08-r1",
        "kind": "monthly",
        "period_key": "2026-08",
        "period_start": "2026-08-01",
        "period_end": "2026-08-31",
    })
    targets = {
        ASTRO / "src" / "data" / "situation" / "v3" / "latest.json": snapshot,
        ASTRO / "src" / "data" / "situation" / "latest.json": snapshot,
        ASTRO / "src" / "data" / "situation" / "v3" / "monthly" / "2026-08.json": monthly,
        ASTRO / "src" / "data" / "situation" / "v3" / "weekly" / "2026-W33.json": weekly,
        ASTRO / "public" / "site-data" / "situation" / "v3" / "latest.json": snapshot,
        ASTRO / "public" / "site-data" / "situation" / "latest.json": snapshot,
        ASTRO / "public" / "site-data" / "situation" / "v3" / "monthly" / "2026-08.json": monthly,
        ASTRO / "public" / "site-data" / "situation" / "v3" / "weekly" / "2026-W33.json": weekly,
    }
    backups: dict[Path, bytes | None] = {}
    try:
        for path, payload in targets.items():
            backups[path] = path.read_bytes() if path.exists() else None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        completed = _run_astro_build()
        assert completed.returncode == 0, completed.stdout + completed.stderr

        latest = (ASTRO / "dist" / "situation" / "index.html").read_text(encoding="utf-8")
        month = (ASTRO / "dist" / "situation" / "monthly" / "2026-08" / "index.html").read_text(encoding="utf-8")
        week = (ASTRO / "dist" / "situation" / "weekly" / "2026-W33" / "index.html").read_text(encoding="utf-8")
        redirects = (ASTRO / "dist" / "_redirects").read_text(encoding="utf-8")
        sitemap = (ASTRO / "dist" / "sitemaps" / "situation.xml").read_text(encoding="utf-8")
        home = (ASTRO / "dist" / "index.html").read_text(encoding="utf-8")

        assert "A searchable evidence base for global infectious disease surveillance" in home
        assert "Current attention" in home
        assert "Official events" in home and "Published signals" in home
        assert "Open the full situation review" in home
        assert 'rel="canonical" href="https://globalinfectiousdisease.com/situation/"' in latest
        assert 'hreflang="zh-CN"' in latest
        assert '"@type":"Report"' in latest and '"@type":"Dataset"' in latest
        assert "Monthly Infectious Disease Situation — 2026-08" in month
        assert "Aug 1, 2026" in month and "Aug 31, 2026" in month
        assert "revision</i> 1" in month
        assert "2026-W33" in week
        assert "/situation/2026-W33/  /situation/weekly/2026-W33/  301" in redirects
        assert "https://globalinfectiousdisease.com/situation/monthly/2026-08/" in sitemap
        assert "https://globalinfectiousdisease.com/situation/weekly/2026-W33/" in sitemap
        assert (ASTRO / "dist" / "site-data" / "situation" / "latest.json").is_file()
        assert (ASTRO / "dist" / "site-data" / "situation" / "v3" / "latest.json").is_file()
    finally:
        for path, content in backups.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
        for directory in sorted({path.parent for path, content in backups.items() if content is None}, key=lambda item: len(item.parts), reverse=True):
            if directory.exists() and not any(directory.iterdir()):
                directory.rmdir()
        shutil.rmtree(tmp_path, ignore_errors=True)
