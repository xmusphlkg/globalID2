import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from src.core.database_snapshot import export_read_snapshot, validate_snapshot_id


@pytest.mark.parametrize("value", ["", "abc", "00000003-0000001B-1'; SELECT 1 --", "00000003-0000001B-1\n"])
def test_snapshot_identifier_rejects_unsafe_or_invalid_literals(value):
    with pytest.raises(ValueError, match="snapshot identifier"):
        validate_snapshot_id(value)


async def test_snapshot_unsupported_backend_fails_closed():
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        with pytest.raises(RuntimeError, match="PostgreSQL"):
            async with export_read_snapshot(engine):
                pytest.fail("Must not silently export changing data")
    finally:
        await engine.dispose()
