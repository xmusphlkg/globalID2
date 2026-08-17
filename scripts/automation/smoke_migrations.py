#!/usr/bin/env python3
"""Validate the Alembic graph and round-trip the latest migration in a disposable DB."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
import sqlalchemy as sa
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.domain import Base  # noqa: E402


DISPOSABLE_DATABASE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*_migration_smoke$")


class MigrationSmokeError(RuntimeError):
    """A migration graph, safety, or round-trip error."""


def alembic_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return config


def validate_revision_graph(config: Config | None = None) -> dict[str, Any]:
    config = config or alembic_config()
    scripts = ScriptDirectory.from_config(config)
    heads = scripts.get_heads()
    bases = scripts.get_bases()
    revisions = list(scripts.walk_revisions())
    if len(heads) != 1:
        raise MigrationSmokeError(f"single_migration_head_required:{len(heads)}")
    if len(bases) != 1:
        raise MigrationSmokeError(f"single_migration_base_required:{len(bases)}")
    if any(revision.is_branch_point for revision in revisions):
        raise MigrationSmokeError("migration_branch_point_requires_merge_revision")
    if not revisions:
        raise MigrationSmokeError("migration_graph_empty")
    return {
        "head": heads[0],
        "base": bases[0],
        "revision_count": len(revisions),
    }


def validate_disposable_url(database_url_sync: str, environment: Mapping[str, str]) -> str:
    if environment.get("MIGRATION_SMOKE_ALLOW_DESTRUCTIVE") != "1":
        raise MigrationSmokeError("migration_smoke_opt_in_required")
    try:
        url = make_url(database_url_sync)
    except (TypeError, ValueError) as exc:
        raise MigrationSmokeError("invalid_migration_database_url") from exc
    if url.get_backend_name() not in {"postgresql", "postgres"}:
        raise MigrationSmokeError("postgresql_migration_database_required")
    database = str(url.database or "")
    if not DISPOSABLE_DATABASE_RE.fullmatch(database):
        raise MigrationSmokeError("disposable_migration_database_name_required")
    return database


def run_round_trip(database_url_sync: str, *, environment: Mapping[str, str]) -> dict[str, Any]:
    database = validate_disposable_url(database_url_sync, environment)
    graph = validate_revision_graph()
    config = alembic_config()
    config.set_main_option("sqlalchemy.url", database_url_sync.replace("%", "%%"))

    # migrations/env.py obtains this setting through the application's config,
    # so keep both Alembic and application views on the disposable database.
    previous_sync = os.environ.get("DATABASE_URL_SYNC")
    os.environ["DATABASE_URL_SYNC"] = database_url_sync
    engine = sa.create_engine(database_url_sync, poolclass=sa.pool.NullPool)
    try:
        with engine.connect() as connection:
            existing = sorted(
                table
                for table in sa.inspect(connection).get_table_names()
                if table != "alembic_version"
            )
        if existing:
            raise MigrationSmokeError(
                "migration_database_must_be_empty:" + ",".join(existing[:10])
            )

        # The repository's first managed revision is intentionally a baseline
        # for a pre-existing schema. Materialize the current model, stamp it,
        # then prove the newest migration can be downgraded and reapplied.
        Base.metadata.create_all(engine)
        command.stamp(config, graph["head"])
        scripts = ScriptDirectory.from_config(config)
        head_revision = scripts.get_revision(graph["head"])
        previous_revision = head_revision.down_revision if head_revision is not None else None
        if not isinstance(previous_revision, str):
            raise MigrationSmokeError("latest_migration_parent_required")
        command.downgrade(config, previous_revision)
        command.upgrade(config, graph["head"])
        command.check(config)
        with engine.connect() as connection:
            current = MigrationContext.configure(connection).get_current_revision()
        if current != graph["head"]:
            raise MigrationSmokeError("migration_head_not_reached")
    finally:
        engine.dispose()
        if previous_sync is None:
            os.environ.pop("DATABASE_URL_SYNC", None)
        else:
            os.environ["DATABASE_URL_SYNC"] = previous_sync
    return {"status": "passed", "database": database, **graph}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url-sync", default=os.getenv("DATABASE_URL_SYNC", ""))
    parser.add_argument("--graph-only", action="store_true")
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> int:
    args = parse_args(argv)
    env = os.environ if environment is None else environment
    try:
        if args.graph_only:
            result = {"status": "passed", **validate_revision_graph()}
        else:
            if not args.database_url_sync:
                raise MigrationSmokeError("migration_database_url_required")
            result = run_round_trip(args.database_url_sync, environment=env)
    except (MigrationSmokeError, sa.exc.SQLAlchemyError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
