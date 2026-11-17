"""Summarize pending province image votes without importing observations."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.cn_province_image_review import (
    PROTOCOL,
    native_model_capacity,
    review_next_action,
)


def summarize(path: Path, payload: dict, capacity: dict | None = None) -> dict:
    audit = payload.get("audit") or {}
    if audit.get("protocol") != PROTOCOL:
        raise ValueError(f"Unsupported or incomplete expert-vote audit: {path}")
    readings = audit.get("readings") or []
    return {
        "audit_path": str(path),
        "status": payload.get("status"),
        "province_code": audit.get("province_code"),
        "report_month": audit.get("report_month"),
        "task_uuid": payload.get("task_uuid"),
        "original_documents": [
            {key: doc.get(key) for key in ("sha256", "source_url", "media_type", "page_count")}
            for doc in audit.get("documents") or []
        ],
        "panel_size": audit.get("panel_size"),
        "quorum": audit.get("quorum"),
        "valid_votes": audit.get("valid_votes", 0),
        "winner_votes": audit.get("winner_votes", 0),
        "readings": [
            {key: slot.get(key) for key in ("slot", "status", "model_identity", "reason")}
            for slot in readings
        ],
        "vote_counts": audit.get("vote_counts") or [],
        "current_model_capacity": capacity,
        "next_action": review_next_action(audit, capacity),
    }


async def run(paths: list[Path], *, offline: bool = False) -> dict:
    audits = [(path, json.loads(path.read_text(encoding="utf-8"))) for path in paths]
    sizes = set()
    for path, payload in audits:
        audit = payload.get("audit") or {}
        if audit.get("protocol") != PROTOCOL or type(audit.get("panel_size")) is not int:
            raise ValueError(f"Unsupported or incomplete expert-vote audit: {path}")
        sizes.add(audit["panel_size"])
    capacities: dict[int, dict] = {}
    model_error = None
    if not offline:
        for size in sorted(sizes):
            try:
                capacities[size] = await native_model_capacity(size)
            except Exception as exc:  # noqa: BLE001 - preserve an offline triage path when model routing is down
                model_error = f"{type(exc).__name__}: {exc}"
    reviews = [summarize(path, payload, capacities.get(payload["audit"]["panel_size"]))
               for path, payload in audits]
    return {"status": "needs_review" if any(item["status"] != "validated" for item in reviews) else "validated",
            "model_center_error": model_error, "reviews": reviews}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, action="append", required=True)
    parser.add_argument("--offline", action="store_true", help="Use recorded votes without querying model routes")
    parser.add_argument("--output", type=Path, help="Write the concise manual-review queue to a new JSON file")
    args = parser.parse_args()
    result = asyncio.run(run(args.audit, offline=args.offline))
    content = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        if args.output.exists():
            parser.error("Output already exists; preserve previous review queues")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    print(content, end="")
    return 0 if result["status"] == "validated" else 2


if __name__ == "__main__":
    raise SystemExit(main())
