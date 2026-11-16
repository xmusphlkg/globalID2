"""The explicit view repair must not run as an unreviewed startup migration."""
import asyncio
from types import SimpleNamespace

import pytest

from scripts import install_effective_mapping_view as install


class FakeConnection:
    def __init__(self):
        self.statements = []
        self.commits = 0
        self.rollbacks = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, statement, params=None):
        query = str(statement)
        self.statements.append(query)
        return SimpleNamespace(
            scalar_one_or_none=lambda: "existing view definition",
            scalar_one=lambda: "installed dynamic_holding definition",
        )

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class FakeEngine:
    def __init__(self, connection):
        self.connection = connection

    def connect(self):
        return self.connection

    async def dispose(self):
        pass


def setup_run(monkeypatch, *, apply=False):
    connection = FakeConnection()
    monkeypatch.setattr(install, "create_async_engine", lambda _: FakeEngine(connection))
    monkeypatch.setattr(install, "get_config", lambda: SimpleNamespace(database=SimpleNamespace(url="unused")))
    monkeypatch.setattr(install, "source_digest", lambda: "reviewed-source")
    args = SimpleNamespace(
        apply=apply,
        expected_existing_view_sha256=install.digest("existing view definition"),
        expected_definition_sha256=install.digest(install.effective_mapping_view_sql()),
        expected_source_sha256="reviewed-source",
    )
    return connection, args


def test_default_audit_is_read_only_and_does_not_install(monkeypatch):
    db, args = setup_run(monkeypatch)
    report = asyncio.run(install.run(args))
    assert db.statements[0] == "SET TRANSACTION READ ONLY"
    assert not any("CREATE OR REPLACE VIEW" in statement for statement in db.statements)
    assert db.commits == 0 and db.rollbacks == 1
    assert report["installed"] is False


@pytest.mark.parametrize("field", [
    "expected_existing_view_sha256", "expected_definition_sha256", "expected_source_sha256",
])
def test_drift_in_any_reviewed_hash_prevents_view_ddl(monkeypatch, field):
    db, args = setup_run(monkeypatch, apply=True)
    setattr(args, field, "changed")
    with pytest.raises(ValueError, match="review hash"):
        asyncio.run(install.run(args))
    assert not any("CREATE OR REPLACE VIEW" in statement for statement in db.statements)
    assert db.commits == 0


def test_explicit_verified_apply_only_replaces_view(monkeypatch):
    db, args = setup_run(monkeypatch, apply=True)
    report = asyncio.run(install.run(args))
    ddl = [statement for statement in db.statements if "CREATE OR REPLACE VIEW" in statement]
    assert ddl == [install.effective_mapping_view_sql()]
    assert not any(statement.lstrip().startswith(("UPDATE ", "INSERT ", "DELETE ", "DROP ")) for statement in db.statements)
    assert db.commits == 1
    assert report["installed"] is True


def test_source_drift_after_ddl_prevents_commit(monkeypatch):
    db, args = setup_run(monkeypatch, apply=True)
    hashes = iter(["reviewed-source", "reviewed-source", "changed-during-install"])
    monkeypatch.setattr(install, "source_digest", lambda: next(hashes))
    with pytest.raises(ValueError, match="definition/source"):
        asyncio.run(install.run(args))
    assert db.commits == 0
