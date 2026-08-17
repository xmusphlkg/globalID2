#!/usr/bin/env python3
"""Validate the generated Research Radar release artifact."""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.literature.release_validation import assert_public_research_payload  # noqa: E402


if __name__ == "__main__":
    path = ROOT / "astro-site" / "src" / "data" / "research" / "index.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert_public_research_payload(payload)
    print(json.dumps({
        "ok": True,
        "path": str(path),
        "articles": len(payload.get("articles") or []),
        "preprints": len(payload.get("preprints") or []),
        "integrity_alerts": len(payload.get("integrity_alerts") or []),
        "evidence_gaps": len((payload.get("surveillance_evidence") or {}).get("evidence_gaps") or []),
    }, ensure_ascii=False, indent=2))
