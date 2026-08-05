from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "scripts" / "install_systemd_services.sh"
TEMPLATE_DIR = REPO_ROOT / "deploy" / "systemd"


def test_installer_manifest_and_dry_run_include_every_systemd_template(
    tmp_path: Path,
) -> None:
    installer_source = INSTALLER.read_text(encoding="utf-8")
    manifest_match = re.search(r"UNIT_NAMES=\((.*?)\n\)", installer_source, re.DOTALL)
    assert manifest_match is not None

    manifest_units = set(
        re.findall(
            r"^\s+([^\s]+\.(?:service|target))\s*$",
            manifest_match.group(1),
            re.MULTILINE,
        )
    )
    template_units = {
        path.name
        for path in TEMPLATE_DIR.iterdir()
        if path.suffix in {".service", ".target"}
    }
    assert manifest_units == template_units
    assert "globalid-notify-failure@.service" in manifest_units

    env = os.environ.copy()
    env["TMPDIR"] = str(tmp_path)
    result = subprocess.run(
        [
            "bash",
            str(INSTALLER),
            "--dry-run",
            "--project-dir",
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "  globalid-notify-failure@.service\n" in result.stdout
    output_dir_line = next(
        line
        for line in result.stdout.splitlines()
        if line.startswith("Rendered unit files to: ")
    )
    output_dir = Path(output_dir_line.removeprefix("Rendered unit files to: "))
    rendered_units = {path.name for path in output_dir.iterdir()}
    assert rendered_units == template_units
    assert (output_dir / "globalid-notify-failure@.service").is_file()
