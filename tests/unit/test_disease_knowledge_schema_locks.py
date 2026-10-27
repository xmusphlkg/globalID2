"""Schema checks must leave existing disease knowledge tables free of ALTER locks."""

import pytest

from src.core.db_schema import _add_missing_columns


class _Columns:
    def __init__(self, names):
        self.names = names

    def scalars(self):
        return self

    def all(self):
        return self.names


class _DB:
    def __init__(self, columns):
        self.columns = columns
        self.statements = []

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), params))
        return _Columns(self.columns)


@pytest.mark.asyncio
async def test_existing_columns_never_issue_alter_table():
    db = _DB(["definition", "clinical_features"])
    statements = [
        "ALTER TABLE disease_knowledge_briefs ADD COLUMN IF NOT EXISTS definition TEXT",
        "ALTER TABLE disease_knowledge_briefs ADD COLUMN IF NOT EXISTS clinical_features TEXT",
    ]

    await _add_missing_columns(db, "disease_knowledge_briefs", statements)

    assert len(db.statements) == 1
    assert "pg_attribute" in db.statements[0][0]
    assert db.statements[0][1] == {"table_name": "disease_knowledge_briefs"}


@pytest.mark.asyncio
async def test_only_missing_column_is_added():
    db = _DB(["definition"])
    statements = [
        "ALTER TABLE disease_knowledge_briefs ADD COLUMN IF NOT EXISTS definition TEXT",
        "ALTER TABLE disease_knowledge_briefs ADD COLUMN IF NOT EXISTS clinical_features TEXT",
    ]

    await _add_missing_columns(db, "disease_knowledge_briefs", statements)

    assert [sql for sql, _ in db.statements[1:]] == [statements[1]]
