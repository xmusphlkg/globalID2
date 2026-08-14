from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ASTRO = ROOT / "astro-site"
FIXTURE = ROOT / "tests" / "fixtures" / "situation" / "public_snapshot_v2.json"


def test_fixture_snapshot_builds_latest_month_week_json_and_seo(tmp_path: Path) -> None:
    snapshot = json.loads(FIXTURE.read_text(encoding="utf-8"))
    targets = {
        ASTRO / "src" / "data" / "situation" / "latest.json": snapshot,
        ASTRO / "src" / "data" / "situation" / "months" / "2026-08.json": snapshot,
        ASTRO / "src" / "data" / "situation" / "weeks" / "2026-W33.json": {**snapshot, "snapshot_kind": "weekly", "period_key": "2026-W33"},
        ASTRO / "public" / "site-data" / "situation" / "latest.json": snapshot,
        ASTRO / "public" / "site-data" / "situation" / "months" / "2026-08.json": snapshot,
        ASTRO / "public" / "site-data" / "situation" / "weeks" / "2026-W33.json": {**snapshot, "snapshot_kind": "weekly", "period_key": "2026-W33"},
    }
    backups: dict[Path, bytes | None] = {}
    try:
        for path, payload in targets.items():
            backups[path] = path.read_bytes() if path.exists() else None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        subprocess.run(["npm", "run", "build:astro"], cwd=ASTRO, check=True, capture_output=True, text=True, timeout=180)

        latest = (ASTRO / "dist" / "situation" / "index.html").read_text(encoding="utf-8")
        month = (ASTRO / "dist" / "situation" / "2026-08" / "index.html").read_text(encoding="utf-8")
        week = (ASTRO / "dist" / "situation" / "2026-W33" / "index.html").read_text(encoding="utf-8")
        sitemap = (ASTRO / "dist" / "sitemaps" / "situation.xml").read_text(encoding="utf-8")
        home = (ASTRO / "dist" / "index.html").read_text(encoding="utf-8")

        assert "Increasing" in home and "Respiratory" in home and "Emerging" in home and "Unusual" in home
        assert 'rel="canonical" href="https://globalinfectiousdisease.com/situation/"' in latest
        assert 'hreflang="zh-CN"' in latest
        assert '"@type":"Report"' in latest and '"@type":"Dataset"' in latest
        assert "Global Infectious Disease Trends — August 2026 | GIDS" in month
        assert "revision 1" in month
        assert "2026-W33" in week
        assert "https://globalinfectiousdisease.com/situation/2026-08/" in sitemap
        assert "https://globalinfectiousdisease.com/situation/2026-W33/" in sitemap
        assert (ASTRO / "dist" / "site-data" / "situation" / "latest.json").is_file()
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
