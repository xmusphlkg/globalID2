from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ASTRO = ROOT / "astro-site"
FIXTURE = ROOT / "tests" / "fixtures" / "situation" / "public_report_v3.json"


def test_fixture_snapshot_builds_latest_month_week_json_and_seo(tmp_path: Path) -> None:
    snapshot = json.loads(FIXTURE.read_text(encoding="utf-8"))
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
        subprocess.run(["npm", "run", "build:astro"], cwd=ASTRO, check=True, capture_output=True, text=True, timeout=180)

        latest = (ASTRO / "dist" / "situation" / "index.html").read_text(encoding="utf-8")
        month = (ASTRO / "dist" / "situation" / "monthly" / "2026-08" / "index.html").read_text(encoding="utf-8")
        week = (ASTRO / "dist" / "situation" / "weekly" / "2026-W33" / "index.html").read_text(encoding="utf-8")
        legacy_week = (ASTRO / "dist" / "situation" / "2026-W33" / "index.html").read_text(encoding="utf-8")
        sitemap = (ASTRO / "dist" / "sitemaps" / "situation.xml").read_text(encoding="utf-8")
        home = (ASTRO / "dist" / "index.html").read_text(encoding="utf-8")

        assert "Today's review brief" in home and "Example disease" in home
        assert 'rel="canonical" href="https://globalinfectiousdisease.com/situation/"' in latest
        assert 'hreflang="zh-CN"' in latest
        assert '"@type":"Report"' in latest and '"@type":"Dataset"' in latest
        assert "Monthly Infectious Disease Situation — 2026-08" in month
        assert 'data-lang-zh="修订版"' in month and ">revision</i> 1" in month
        assert "2026-W33" in week
        assert "/situation/weekly/2026-W33/" in legacy_week
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
