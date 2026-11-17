from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.automation.prepare_site_build_fixture import prepare_fixture
from scripts.automation.isolated_site_build_fixture import copy_clean_site, fixture_build_environment


ROOT = Path(__file__).resolve().parents[2]
ASTRO = ROOT / "astro-site"


@pytest.mark.skipif(
    not (ASTRO / "node_modules" / ".bin" / "astro").exists(),
    reason="Astro dependencies are not installed",
)
def test_missing_only_ci_fixture_supports_clean_build_and_performance_gate(
    tmp_path: Path,
) -> None:
    site = copy_clean_site(ASTRO, tmp_path / "astro-site")

    prepared = prepare_fixture(site, environment={"CI": "1"})
    assert prepared["created"]
    completed = subprocess.run(
        ["npm", "run", "build:astro"],
        cwd=site,
        check=False,
        capture_output=True,
        text=True,
        env=fixture_build_environment(),
        timeout=180,
    )
    (tmp_path / "astro-build.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "[performance-budget] PASS" in completed.stdout
    assert (site / "dist" / "index.html").is_file()
    assert (site / "dist" / "research" / "index.html").is_file()
    assert (site / "dist" / "situation" / "index.html").is_file()
    assert (site / "dist" / "situation" / "methodology" / "index.html").is_file()
    assert (site / "dist" / "situation" / "weekly" / "index.html").is_file()
