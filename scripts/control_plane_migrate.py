"""Preflight checks for adopting managed control-plane migrations."""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import inspect

from src.core.database import get_engine

REQUIRED_TABLES = {
    "countries",
    "tasks",
    "task_workbook",
    "automation_jobs",
    "data_release_jobs",
}

REQUIRED_COLUMNS = {
    "automation_jobs": {
        "job_id",
        "country_code",
        "include_current_month",
        "revision_window_months",
    },
    "data_release_jobs": {"job_id", "auto_after_crawls", "include_cloudflare_deploy"},
    "tasks": {"task_uuid", "task_type", "status", "metadata"},
}


async def preflight() -> int:
    engine = get_engine()
    async with engine.connect() as connection:
        tables = set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))
    missing = sorted(REQUIRED_TABLES - tables)
    if missing:
        print("Control-plane migration preflight failed. Missing tables: " + ", ".join(missing))
        return 1
    missing_columns: list[str] = []
    async with engine.connect() as connection:
        for table_name, required in REQUIRED_COLUMNS.items():
            columns = set(
                await connection.run_sync(
                    lambda sync, name=table_name: {
                        item["name"] for item in inspect(sync).get_columns(name)
                    }
                )
            )
            missing_columns.extend(
                f"{table_name}.{column}" for column in sorted(required - columns)
            )
    if missing_columns:
        print(
            "Control-plane migration preflight failed. Missing columns: "
            + ", ".join(missing_columns)
        )
        return 1
    print("Control-plane migration preflight passed.")
    return 0


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "preflight"
    if command != "preflight":
        print("Usage: python scripts/control_plane_migrate.py preflight")
        return 2
    return asyncio.run(preflight())


if __name__ == "__main__":
    raise SystemExit(main())
