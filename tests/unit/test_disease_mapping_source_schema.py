from __future__ import annotations

from src.core.db_schema import (
    ensure_disease_learning_suggestions_schema,
    ensure_disease_mapping_source_schema,
)


async def test_source_schema_evolution_is_additive_and_rekeys_indexes() -> None:
    class _DB:
        def __init__(self) -> None:
            self.statements: list[str] = []

        async def execute(self, statement):
            self.statements.append(" ".join(str(statement).split()))

    db = _DB()
    await ensure_disease_mapping_source_schema(db)
    sql = "\n".join(db.statements)

    assert "ADD COLUMN IF NOT EXISTS source_id VARCHAR(120)" in sql
    assert "NOT NULL DEFAULT '*'" in sql
    assert "ADD COLUMN IF NOT EXISTS series_id VARCHAR(160)" in sql
    assert "ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMP WITH TIME ZONE" in sql
    assert (
        "disease_id, country_code, source_id, local_name" in sql
    )
    assert "country_code, source_id, local_name" in sql


async def test_learning_suggestion_schema_evolution_restores_usage_timestamps() -> None:
    class _DB:
        def __init__(self) -> None:
            self.statements: list[str] = []

        async def execute(self, statement):
            self.statements.append(" ".join(str(statement).split()))

    db = _DB()
    await ensure_disease_learning_suggestions_schema(db)
    sql = "\n".join(db.statements)

    assert (
        "ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP"
        in sql
    )
    assert (
        "ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP"
        in sql
    )
    assert "ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP WITH TIME ZONE" in sql
    assert "ALTER COLUMN status SET DEFAULT 'pending'" in sql
    assert "SET status = 'pending' WHERE status IS NULL" in sql
