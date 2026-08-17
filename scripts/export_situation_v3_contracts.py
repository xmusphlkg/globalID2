#!/usr/bin/env python3
"""Regenerate every Situation Room v3 contract artifact from Pydantic."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_situation_v3_schema.py")],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_dashboard_openapi.py")],
        cwd=ROOT,
        check=True,
    )
    generator = ROOT / "dashboard" / "node_modules" / ".bin" / "openapi-typescript"
    if not generator.exists():
        raise RuntimeError("Run npm install in dashboard before generating TypeScript contracts")
    openapi = ROOT / "dashboard" / "openapi.json"
    for target in (
        ROOT / "dashboard" / "src" / "generated" / "api.d.ts",
        ROOT / "astro-site" / "src" / "generated" / "api.d.ts",
    ):
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [str(generator), str(openapi), "--output", str(target)],
            cwd=ROOT,
            check=True,
        )


if __name__ == "__main__":
    main()
