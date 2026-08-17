#!/usr/bin/env python3
"""Export the canonical Situation Room v3 JSON Schema."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.situation_v3.contracts import SituationReportV3  # noqa: E402


def main() -> None:
    schema = SituationReportV3.model_json_schema()
    schema["$id"] = "https://globalinfectiousdisease.com/schemas/situation-room-v3.json"
    schema["title"] = "GIDS Situation Room v3 report"
    target = ROOT / "configs" / "situation_room.v3.schema.json"
    target.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
