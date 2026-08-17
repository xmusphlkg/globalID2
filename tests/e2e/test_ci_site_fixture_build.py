from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.automation.prepare_site_build_fixture import prepare_fixture


ROOT = Path(__file__).resolve().parents[2]
ASTRO = ROOT / "astro-site"


def _clean_checkout_ignore(directory: str, names: list[str]) -> set[str]:
    relative = Path(directory).resolve().relative_to(ASTRO.resolve())
    ignored: set[str] = set()
    if relative == Path("."):
        ignored.update({"node_modules", "dist", ".astro"})
    elif relative == Path("src"):
        ignored.add("data")
    elif relative == Path("public"):
        ignored.add("site-data")
    return ignored.intersection(names)


@pytest.mark.skipif(
    not (ASTRO / "node_modules" / ".bin" / "astro").exists(),
    reason="Astro dependencies are not installed",
)
def test_missing_only_ci_fixture_supports_clean_build_and_performance_gate(
    tmp_path: Path,
) -> None:
    site = tmp_path / "astro-site"
    shutil.copytree(ASTRO, site, ignore=_clean_checkout_ignore)
    source_data = site / "src" / "data"
    source_data.mkdir(parents=True)
    for filename in ("acknowledgements.ts", "changelog.ts"):
        shutil.copy2(ASTRO / "src" / "data" / filename, source_data / filename)
    (site / "node_modules").symlink_to(ASTRO / "node_modules", target_is_directory=True)

    prepared = prepare_fixture(site, environment={"CI": "1"})
    assert prepared["created"]
    completed = subprocess.run(
        ["npm", "run", "build:astro"],
        cwd=site,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "[performance-budget] PASS" in completed.stdout
    assert (site / "dist" / "index.html").is_file()
    assert (site / "dist" / "research" / "index.html").is_file()
    assert (site / "dist" / "situation" / "index.html").is_file()
    assert (
        site / "dist" / "site-data" / "situation" / "v3" / "latest.json"
    ).is_file()
