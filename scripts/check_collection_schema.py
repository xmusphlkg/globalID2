"""Read-only PostgreSQL schema contract for collection and model work tables."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine, inspect, text
from sqlalchemy.dialects import postgresql

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.domain.ai_work_item import AIWorkItem
from src.domain.collection import PublishDirty, RunStage, SourceArtifact

TABLES = tuple(
    model.__table__ for model in (RunStage, SourceArtifact, PublishDirty, AIWorkItem)
)


def normalized_default(value):
    if value is None:
        return None
    value = re.sub(
        r"::(?:character varying|jsonb?|text|integer|bigint)",
        "",
        str(value),
        flags=re.IGNORECASE,
    )
    return value.strip().strip("()").strip("'")


def normalized_predicate(value):
    if value is None:
        return None
    value = re.sub(
        r"::(?:character varying|jsonb?|text|integer|bigint)",
        "",
        str(value),
        flags=re.IGNORECASE,
    )
    return re.sub(r'[\s()"]', "", value).lower()


def check_signature(value):
    """Normalize PostgreSQL's IN-to-ANY deparsing for these enum contracts."""
    expression = re.sub(
        r"::(?:character varying|text|integer|bigint)(?:\[\])?",
        "",
        str(value),
        flags=re.IGNORECASE,
    ).lower()
    literals = tuple(sorted(re.findall(r"'([^']*)'", expression)))
    if literals:
        identifiers = set(
            re.findall(r"\b[a-z_][a-z_0-9]*\b", re.sub(r"'[^']*'", "", expression))
        )
        identifiers -= {"in", "any", "array", "not", "and", "or", "true", "false"}
        return (
            "enum",
            tuple(sorted(identifiers)),
            literals,
            "not" in expression.split(),
        )
    expression = re.sub(
        r"(\w+)\s+between\s+(\d+)\s+and\s+(\d+)", r"\1>=\2and\1<=\3", expression
    )
    return ("expression", normalized_predicate(expression))


def compare_table_metadata(expected, actual, dialect):
    """Compare fresh migration metadata with production ORM declarations."""
    differences = []
    if tuple(column.name for column in expected.primary_key) != tuple(
        column.name for column in actual.primary_key
    ):
        differences.append(f"{expected.name}: primary key differs")
    if set(expected.c.keys()) != set(actual.c.keys()):
        differences.append(f"{expected.name}: column names differ")
    for column in expected.c:
        if column.name not in actual.c:
            continue
        other = actual.c[column.name]
        if str(column.type.compile(dialect=dialect)) != str(
            other.type.compile(dialect=dialect)
        ):
            differences.append(f"{expected.name}.{column.name}: type differs")
        if column.nullable != other.nullable:
            differences.append(f"{expected.name}.{column.name}: nullability differs")
        left = column.server_default.arg if column.server_default is not None else None
        right = other.server_default.arg if other.server_default is not None else None
        if normalized_default(left) != normalized_default(right):
            differences.append(f"{expected.name}.{column.name}: server default differs")

    def checks(table):
        return {
            constraint.name: check_signature(constraint.sqltext)
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        }

    def unique(table):
        return {
            constraint.name: tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }

    def indexes(table):
        return {
            index.name: (
                tuple(column.name for column in index.columns),
                bool(index.unique),
                normalized_predicate(index.dialect_options["postgresql"].get("where")),
            )
            for index in table.indexes
        }

    for name, collector in [
        ("checks", checks),
        ("unique constraints", unique),
        ("indexes", indexes),
    ]:
        if collector(expected) != collector(actual):
            differences.append(f"{expected.name}: {name} differ")
    return differences


def compare_postgresql_schema(connection):
    inspector = inspect(connection)
    dialect = postgresql.dialect()
    differences = []
    for table in TABLES:
        if not inspector.has_table(table.name):
            differences.append(f"{table.name}: missing table")
            continue
        primary_key = inspector.get_pk_constraint(table.name)
        if tuple(primary_key["constrained_columns"]) != tuple(
            column.name for column in table.primary_key
        ):
            differences.append(f"{table.name}: primary key differs")
        columns = {
            column["name"]: column for column in inspector.get_columns(table.name)
        }
        if set(columns) != set(table.c.keys()):
            differences.append(f"{table.name}: column names differ")
        for column in table.c:
            actual = columns.get(column.name)
            if actual is None:
                continue
            expected_type = str(column.type.compile(dialect=dialect))
            actual_type = str(actual["type"].compile(dialect=dialect))
            if expected_type != actual_type:
                differences.append(
                    f"{table.name}.{column.name}: type {actual_type} != {expected_type}"
                )
            if column.nullable != actual["nullable"]:
                differences.append(f"{table.name}.{column.name}: nullability differs")
            if column.primary_key and column.autoincrement is not False:
                if not str(actual["default"] or "").startswith("nextval("):
                    differences.append(
                        f"{table.name}.{column.name}: missing sequence default"
                    )
                continue
            expected_default = (
                column.server_default.arg if column.server_default is not None else None
            )
            if normalized_default(expected_default) != normalized_default(
                actual["default"]
            ):
                differences.append(
                    f"{table.name}.{column.name}: server default differs"
                )
        checks = {
            constraint.name: check_signature(constraint.sqltext)
            for constraint in table.constraints
            if isinstance(constraint, CheckConstraint)
        }
        actual_checks = {
            constraint["name"]: check_signature(constraint["sqltext"])
            for constraint in inspector.get_check_constraints(table.name)
        }
        if checks != actual_checks:
            differences.append(f"{table.name}: check constraints differ")
        unique = {
            constraint.name: tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        actual_unique = {
            constraint["name"]: tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(table.name)
        }
        if unique != actual_unique:
            differences.append(f"{table.name}: unique constraints differ")
        indexes = {
            index.name: (
                tuple(column.name for column in index.columns),
                bool(index.unique),
                normalized_predicate(index.dialect_options["postgresql"].get("where")),
            )
            for index in table.indexes
        }
        actual_indexes = {
            index["name"]: (
                tuple(index["column_names"]),
                bool(index["unique"]),
                normalized_predicate(
                    index.get("dialect_options", {}).get("postgresql_where")
                ),
            )
            for index in inspector.get_indexes(table.name)
            if not index.get("duplicates_constraint")
        }
        if indexes != actual_indexes:
            differences.append(f"{table.name}: indexes differ")
    return differences


def main():
    from src.core.config import get_config

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-revision", default="0024_collection_jsonb")
    args = parser.parse_args()
    engine = create_engine(get_config().database.url_sync)
    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            differences = compare_postgresql_schema(connection)
            if revision != args.expected_revision:
                differences.append(
                    f"alembic revision {revision} != {args.expected_revision}"
                )
            connection.rollback()
        print(
            json.dumps(
                {
                    "revision": revision,
                    "expected_revision": args.expected_revision,
                    "tables": [table.name for table in TABLES],
                    "differences": differences,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return bool(differences)
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
