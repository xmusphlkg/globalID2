from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_generated_data_is_ignored_and_untracked() -> None:
    subprocess.run(
        [str(ROOT / "scripts" / "check_repository_boundaries.sh")],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
