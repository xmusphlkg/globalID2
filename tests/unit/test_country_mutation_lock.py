from unittest.mock import AsyncMock

import pytest

from src.core.disease_mutation_lock import (
    acquire_country_data_mutation_lock,
    country_mutation_scope,
)


@pytest.mark.parametrize("code,expected", [("US", "US"), ("us-ak", "US"), (" JP-13 ", "JP"), ("ZZ", "ZZ")])
def test_country_partition_covers_parent_and_all_subdivisions(code, expected):
    assert country_mutation_scope(code) == expected


@pytest.mark.parametrize("code", ["", None, "*", "US;select", "US/AK", "USA", "US-", "US--AK"])
async def test_invalid_country_never_acquires_a_global_gate(code):
    db = AsyncMock()
    with pytest.raises(ValueError, match="Invalid disease mutation country scope"):
        await acquire_country_data_mutation_lock(db, code)
    db.execute.assert_not_awaited()


async def test_shared_maintenance_gate_is_acquired_before_national_partition():
    db = AsyncMock()
    await acquire_country_data_mutation_lock(db, "US-AK")
    statements = db.execute.await_args_list
    assert len(statements) == 2
    assert "pg_advisory_xact_lock_shared" in str(statements[0].args[0])
    assert "pg_advisory_xact_lock(" in str(statements[1].args[0])
    assert statements[1].args[1]["country_scope"] == "US"
