import pytest

from src.core.db_schema import ensure_task_type_enum_schema
from src.domain import TaskType


class _FakeDialect:
    name = "postgresql"


class _FakeBind:
    dialect = _FakeDialect()


class _FakeSession:
    def __init__(self) -> None:
        self.bind = _FakeBind()
        self.statements: list[str] = []
        self.commits = 0

    async def execute(self, statement):  # pragma: no cover - exercised via test
        self.statements.append(str(statement))

    async def commit(self):  # pragma: no cover - exercised via test
        self.commits += 1


@pytest.mark.asyncio
async def test_ensure_task_type_enum_schema_adds_name_and_value_labels():
    session = _FakeSession()

    await ensure_task_type_enum_schema(session)  # type: ignore[arg-type]

    joined = "\n".join(session.statements)
    assert "UPDATE_DISEASE_KNOWLEDGE" in joined
    assert "update_disease_knowledge" in joined
    assert "REFRESH_DISEASE_KNOWLEDGE_SOURCES" in joined
    assert "refresh_disease_knowledge_sources" in joined
    assert session.commits == 1
    assert any(f"'{TaskType.UPDATE_DISEASE_KNOWLEDGE.name}'" in stmt for stmt in session.statements)
