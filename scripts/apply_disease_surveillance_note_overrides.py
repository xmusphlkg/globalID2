#!/usr/bin/env python3
"""Apply reviewed source-data notes to existing disease knowledge briefs."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from src.core import get_database
from src.domain import DiseaseKnowledgeBrief
from src.knowledge.surveillance_note_overrides import (
    apply_surveillance_note_override,
    load_surveillance_note_overrides,
)


async def run(*, apply: bool) -> dict:
    document = load_surveillance_note_overrides()
    disease_ids = sorted(document.get("notes", {}))
    changed: list[str] = []
    missing: list[str] = []
    async with get_database() as db:
        rows = list(
            (
                await db.execute(
                    select(DiseaseKnowledgeBrief).where(
                        DiseaseKnowledgeBrief.disease_id.in_(disease_ids)
                    )
                )
            ).scalars().all()
        )
        by_identity = {(row.disease_id, row.language): row for row in rows}
        for disease_id in disease_ids:
            for language in ("en", "zh"):
                row = by_identity.get((disease_id, language))
                if row is None:
                    missing.append(f"{disease_id}:{language}")
                    continue
                payload = apply_surveillance_note_override(
                    {
                        "disease_id": row.disease_id,
                        "language": row.language,
                        "surveillance_note": row.surveillance_note,
                        "metadata": row.metadata_ or {},
                    }
                )
                if (
                    row.surveillance_note != payload.get("surveillance_note")
                    or (row.metadata_ or {}) != payload.get("metadata")
                ):
                    row.surveillance_note = payload.get("surveillance_note")
                    row.metadata_ = payload.get("metadata") or {}
                    changed.append(f"{disease_id}:{language}")
        if apply:
            await db.commit()
        else:
            await db.rollback()
    return {
        "mode": "applied" if apply else "rehearsed_rollback",
        "review_version": document.get("review_version"),
        "configured_diseases": len(disease_ids),
        "changed_briefs": changed,
        "missing_briefs": missing,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--rehearse", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(apply=args.apply)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
