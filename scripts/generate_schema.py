"""Generate and optionally apply SQL DDL from SQLAlchemy metadata.

This script:
- imports `src.domain.Base` (models)
- connects to the database using `config.database.url_sync`
- runs `Base.metadata.create_all(bind=engine)` to apply schema
- captures executed DDL statements and writes `schema.sql` in the project root

Usage: python scripts/generate_schema.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

# Ensure project root is on PYTHONPATH
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.config import get_config
from src.domain import Base

OUT_FILE = ROOT / "schema.sql"


def capture_ddl(engine: Engine) -> List[str]:
    """Run create_all and capture executed DDL statements."""
    captured: List[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        s = statement.strip()
        # Capture only DDL-like statements (ignore internal SELECT checks used by SQLAlchemy)
        if s:
            upper = s.upper()
            if upper.startswith(("CREATE", "ALTER", "COMMENT", "DROP", "GRANT")):
                if not s.endswith(";"):
                    s = s + ";"
                # filter out pg_catalog internal checks
                if "PG_CATALOG" in upper:
                    return
                captured.append(s)

    # Apply metadata (this will create types, tables, indexes...)
    Base.metadata.create_all(bind=engine)

    # remove listener
    event.remove(engine, "before_cursor_execute", before_cursor_execute)

    return captured


def render_metadata_sql(metadata, dialect):
    """Generate SQL statements from SQLAlchemy metadata for given dialect.

    Produces:
    - CREATE TYPE ... AS ENUM ... (for Python Enum columns when using native_enum)
    - CREATE TABLE ...;
    - CREATE INDEX ...;
    """
    from sqlalchemy.schema import CreateTable, CreateIndex
    from sqlalchemy.sql.sqltypes import Enum as SAEnum

    statements: List[str] = []

    # Collect enum types first to avoid duplicates
    enum_defs = {}
    for table in metadata.sorted_tables:
        for col in table.columns:
            col_type = getattr(col, 'type', None)
            if isinstance(col_type, SAEnum) and col_type.native_enum:
                vals = list(col_type.enums)
                name = col_type.name or f"{table.name}_{col.name}_enum"
                if name not in enum_defs:
                    quoted = ', '.join(f"'{v}'" for v in vals)
                    enum_defs[name] = f"CREATE TYPE {name} AS ENUM ({quoted});"

    # Add enum defs first
    for v in enum_defs.values():
        statements.append(v)

    # Add CREATE TABLE and CREATE INDEX statements
    for table in metadata.sorted_tables:
        statements.append(str(CreateTable(table).compile(dialect=dialect)) + ";")
        for idx in sorted(table.indexes, key=lambda i: i.name or ""):
            statements.append(str(CreateIndex(idx).compile(dialect=dialect)) + ";")

    return statements


def main():
    cfg = get_config()
    db_url = cfg.database.url_sync

    engine = create_engine(db_url)

    print(f"Connecting to database: {db_url.split('@')[-1]}")

    try:
        statements = capture_ddl(engine)
    except Exception as e:
        print("ERROR: failed to create schema:", e)
        raise

    header = (
        "-- Generated schema from SQLAlchemy metadata\n"
        "-- Command: Base.metadata.create_all()\n"
        "-- NOTE: The file below contains deterministic DDL (types, tables, indexes) as produced by SQLAlchemy.\n"
        "-- Extensions (TimescaleDB / pgvector) and hypertable conversion are NOT applied automatically.\n"
        "-- Suggested extension and hypertable commands (commented examples are included below):\n"
        "--   -- CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;\n"
        "--   -- SELECT create_hypertable('disease_records', 'time', chunk_time_interval => INTERVAL '1 month');\n"
        "--   -- CREATE EXTENSION IF NOT EXISTS pgvector;\n"
        "\n"
    )

    EXTENSION_NOTES = (
        "-- == Optional DB Extensions / Additional Operations ==\n"
        "-- To enable time-series features (recommended for production):\n"
        "--   CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;\n"
        "--   SELECT create_hypertable('disease_records', 'time', chunk_time_interval => INTERVAL '1 month');\n"
        "-- To enable native vector type (optional):\n"
        "--   CREATE EXTENSION IF NOT EXISTS pgvector;\n"
        "-- If you migrate JSON -> JSONB for better indexing, consider adding GIN indexes:\n"
        "--   CREATE INDEX idx_countries_crawler_config_gin ON countries USING gin (crawler_config jsonb_path_ops);\n"
        "--   CREATE INDEX idx_reports_key_findings_gin ON reports USING gin (key_findings jsonb_path_ops);\n"
        "\n"
    )

    # Generate deterministic SQL from metadata (CREATE TYPE / CREATE TABLE / CREATE INDEX)
    rendered = render_metadata_sql(Base.metadata, engine.dialect)

    with OUT_FILE.open("w", encoding="utf-8") as f:
        f.write(header)
        f.write("-- == Metadata-based DDL (deterministic) ==\n")
        for stmt in rendered:
            f.write(stmt + "\n")

        if statements:
            f.write("\n-- == DDL statements captured during `create_all()` execution (if any) ==\n")
            for stmt in statements:
                f.write(stmt + "\n")

    print(f"Wrote {len(rendered)} metadata statements (+{len(statements)} captured) to {OUT_FILE}")
    print("Done.")


if __name__ == "__main__":
    main()
