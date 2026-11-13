#!/usr/bin/env python3
"""Check the checked-in dashboard API contract without modifying the worktree.

The old dashboard command regenerated files and then compared them with Git.
That made the result depend on unrelated dirty files and could hide a stale
generated client. This command renders both artifacts in temporary files and
compares them with the current files.
"""
from __future__ import annotations

import json
from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard"
sys.path.insert(0, str(ROOT))


def canonical_openapi() -> bytes:
    # Application startup configures logging and may emit informational lines;
    # keep this command's stdout a single machine-readable JSON result.
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        from dashboard.api.main import app
        payload = app.openapi()
    return (json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode()


def generated_typescript(openapi_path: Path, output_path: Path) -> tuple[bool, str]:
    command = ["npx", "--no-install", "openapi-typescript", str(openapi_path), "--output", str(output_path)]
    result = subprocess.run(command, cwd=DASHBOARD, text=True, capture_output=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "openapi-typescript failed").strip().splitlines()[-1]
        return False, detail
    return True, "ok"


def check() -> dict:
    current_openapi = DASHBOARD / "openapi.json"
    current_types = DASHBOARD / "src/generated/api.d.ts"
    result = {
        "schema_version": 1,
        "openapi_json": {"status": "unknown", "path": str(current_openapi.relative_to(ROOT))},
        "typescript": {"status": "unknown", "path": str(current_types.relative_to(ROOT))},
        "ready": False,
    }
    with tempfile.TemporaryDirectory(prefix="gids-openapi-check-") as directory:
        directory = Path(directory)
        expected_openapi = directory / "openapi.json"
        expected_types = directory / "api.d.ts"
        expected_openapi.write_bytes(canonical_openapi())
        if not current_openapi.is_file():
            result["openapi_json"].update(status="missing")
        elif current_openapi.read_bytes() != expected_openapi.read_bytes():
            result["openapi_json"].update(status="stale")
        else:
            result["openapi_json"].update(status="ok")
        generated_ok, detail = generated_typescript(expected_openapi, expected_types)
        if not generated_ok:
            result["typescript"].update(status="unavailable", detail=detail)
        elif not current_types.is_file():
            result["typescript"].update(status="missing")
        elif current_types.read_bytes() != expected_types.read_bytes():
            result["typescript"].update(status="stale")
        else:
            result["typescript"].update(status="ok")
    result["ready"] = result["openapi_json"]["status"] == result["typescript"]["status"] == "ok"
    return result


def main() -> int:
    result = check()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
