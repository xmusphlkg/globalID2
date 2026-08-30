#!/usr/bin/env python3
"""Record a human Research Radar weekly-brief review, bound to its content.

The command is a dry-run unless ``--apply`` is supplied.  It never invents a
reviewer: an operator must provide a real name and role and explicitly attest
that they reviewed the cited findings, monitoring context, and evidence gaps.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.literature.weekly_briefs import (  # noqa: E402
    WEEKLY_REVIEW_REGISTRY_PATH,
    project_weekly_editorial_review,
    weekly_brief_review_fingerprint,
)


DEFAULT_WEEKLY_DIR = ROOT / "astro-site" / "src" / "data" / "research" / "weekly"
WEEK = re.compile(r"^\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])$")


class ReviewError(RuntimeError):
    """Stable, safe-to-log review workflow error."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--reviewer-name", required=True)
    parser.add_argument("--reviewer-role", required=True)
    parser.add_argument("--institution")
    parser.add_argument("--note-en")
    parser.add_argument("--note-zh")
    parser.add_argument("--reviewed-at", help="Timezone-aware ISO timestamp; defaults to now")
    parser.add_argument("--internal-reviewer-id", help="Private audit identifier; never publicly projected")
    parser.add_argument("--brief", type=Path)
    parser.add_argument("--registry", type=Path, default=WEEKLY_REVIEW_REGISTRY_PATH)
    parser.add_argument(
        "--attest-reviewed",
        action="store_true",
        help="Confirm that the named human reviewed every cited finding and disclosed relationship",
    )
    parser.add_argument("--replace-existing", action="store_true")
    parser.add_argument("--apply", action="store_true")
    return parser


def _read_json(path: Path, code: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReviewError(f"{code}_not_found") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewError(f"{code}_unreadable_or_invalid") from exc
    if not isinstance(payload, dict):
        raise ReviewError(f"{code}_object_required")
    return payload


def build_review_record(args: argparse.Namespace) -> tuple[dict[str, Any], Path]:
    if not WEEK.fullmatch(args.week):
        raise ReviewError("invalid_week")
    if not args.attest_reviewed:
        raise ReviewError("human_review_attestation_required")
    brief_path = args.brief or DEFAULT_WEEKLY_DIR / f"{args.week}.json"
    brief = _read_json(brief_path, "weekly_brief")
    if brief.get("week") != args.week:
        raise ReviewError("weekly_brief_week_mismatch")
    findings = brief.get("cited_findings")
    if not isinstance(findings, list) or not findings:
        raise ReviewError("weekly_brief_cited_findings_required")

    review: dict[str, Any] = {
        "name": args.reviewer_name,
        "role": args.reviewer_role,
        "reviewed_at": args.reviewed_at or datetime.now(timezone.utc).isoformat(),
    }
    if args.institution:
        review["institution"] = args.institution
    if args.note_en is not None or args.note_zh is not None:
        review.update({"note_en": args.note_en or "", "note_zh": args.note_zh or ""})
    if args.internal_reviewer_id:
        review["internal_reviewer_id"] = args.internal_reviewer_id.strip()[:160]
    if project_weekly_editorial_review(review) is None:
        raise ReviewError("invalid_human_review_metadata")

    return {
        "week": args.week,
        "brief_fingerprint": weekly_brief_review_fingerprint(brief),
        "review": review,
    }, brief_path


def _load_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 2, "reviews": []}
    payload = _read_json(path, "weekly_review_registry")
    if payload.get("schema_version") != 2 or not isinstance(payload.get("reviews"), list):
        raise ReviewError("weekly_review_registry_schema_invalid")
    return {"schema_version": 2, "reviews": list(payload["reviews"])}


def update_registry(path: Path, record: dict[str, Any], *, replace: bool, apply: bool) -> dict[str, Any]:
    payload = _load_registry(path)
    week = record["week"]
    existing = [row for row in payload["reviews"] if isinstance(row, dict) and row.get("week") == week]
    if existing and not replace:
        raise ReviewError("weekly_review_already_exists")
    payload["reviews"] = [
        row for row in payload["reviews"]
        if not isinstance(row, dict) or row.get("week") != week
    ]
    payload["reviews"].append(record)
    payload["reviews"].sort(key=lambda row: str(row.get("week") or ""))
    if apply:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_name = ""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False,
            ) as handle:
                temporary_name = handle.name
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        finally:
            if temporary_name:
                Path(temporary_name).unlink(missing_ok=True)
    return payload


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        record, brief_path = build_review_record(args)
        update_registry(args.registry, record, replace=args.replace_existing, apply=args.apply)
    except ReviewError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    public_review = project_weekly_editorial_review(record["review"]) or {}
    print(json.dumps({
        "ok": True,
        "status": "recorded" if args.apply else "dry_run",
        "week": record["week"],
        "brief_file": brief_path.name,
        "brief_fingerprint": record["brief_fingerprint"],
        "reviewer": {key: public_review.get(key) for key in ("name", "role", "institution") if public_review.get(key)},
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
