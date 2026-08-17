"""Export the canonical FastAPI contract for TypeScript client generation."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard.api.main import app  # noqa: E402


TARGET = ROOT / "dashboard" / "openapi.json"


def main() -> None:
    payload = json.dumps(app.openapi(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    TARGET.write_text(payload, encoding="utf-8")
    print(f"Exported {len(app.openapi().get('paths', {}))} paths to {TARGET}")


if __name__ == "__main__":
    main()
