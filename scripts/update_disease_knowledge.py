#!/usr/bin/env python3
"""
Update the source-grounded disease knowledge base.

Examples:
    python scripts/update_disease_knowledge.py --dry-run --disease-id D001
    python scripts/update_disease_knowledge.py --disease-id D028 --source who --source wikidata
    python scripts/update_disease_knowledge.py --force
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.core import get_database, init_database  # noqa: E402
from src.services.disease_knowledge_service import (  # noqa: E402
    DiseaseKnowledgeUpdateService,
    expand_sources,
    load_standard_diseases,
)
from src.knowledge.catalogue import should_generate_public_disease_page  # noqa: E402


async def async_main(args: argparse.Namespace) -> None:
    diseases = load_standard_diseases(args.disease_csv)

    if args.disease_id:
        wanted = {item.upper() for item in args.disease_id}
        diseases = [d for d in diseases if d["disease_id"].upper() in wanted]
    else:
        diseases = [d for d in diseases if should_generate_public_disease_page(d)]
    if args.limit:
        diseases = diseases[: args.limit]

    enabled_sources = expand_sources(args.source)

    async def run_one(disease: dict[str, Any]) -> dict[str, Any]:
        service = DiseaseKnowledgeUpdateService(disease_csv_path=args.disease_csv)
        return await service.update_disease(
            disease["disease_id"],
            enabled_sources=enabled_sources,
            force=args.force or args.dry_run,
            generator_mode=args.generator,
            dry_run=args.dry_run,
        )

    if not args.dry_run:
        await init_database()

    concurrency = max(1, args.concurrency)
    semaphore = asyncio.Semaphore(concurrency)

    async def run_with_limit(disease: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            try:
                return await run_one(disease)
            except Exception as exc:
                return {
                    "disease_id": disease["disease_id"],
                    "error": str(exc),
                    "fetched_sources": 0,
                    "total_sources": 0,
                    "brief_statuses": {},
                }

    tasks = [asyncio.create_task(run_with_limit(disease)) for disease in diseases]
    results = await asyncio.gather(*tasks)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for result in results:
            if result.get("error"):
                print(f"✗ {result['disease_id']}: {result['error']}")
                continue
            print(
                f"✓ {result['disease_id']}: "
                f"{result.get('fetched_sources', 0)} fetched, "
                f"{result.get('total_sources', 0)} total, "
                f"briefs={result.get('brief_statuses')}"
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Update source-grounded disease knowledge briefs")
    parser.add_argument("--disease-csv", type=Path, default=ROOT / "configs" / "standard_diseases.csv")
    parser.add_argument("--disease-id", action="append", help="Standard disease ID to update, e.g. D001")
    parser.add_argument("--source", action="append", choices=sorted(["who", "wikidata", "wikipedia", "pubmed", "msd"]), help="Source group to fetch")
    parser.add_argument("--force", action="store_true", help="Fetch sources even when existing rows are present")
    parser.add_argument(
        "--generator",
        choices=["ai", "auto", "template"],
        default="ai",
        help="Brief generator. ai/auto use the AI model center with template fallback; template is deterministic only.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Fetch and render without writing database rows")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of diseases")
    parser.add_argument("--concurrency", type=int, default=2, help="Parallel disease updates")
    parser.add_argument("--json", action="store_true", help="Print final JSON summary")
    return parser


def main() -> None:
    asyncio.run(async_main(build_parser().parse_args()))


if __name__ == "__main__":
    main()
