from __future__ import annotations

import pytest
from sqlalchemy import func, select

from src.core.situation_history_database import get_history_db, history_database_descriptor
from src.domain.situation_history import SituationHistorySignal, SituationHistorySnapshot
from src.services.situation_history_service import history_health, sync_history


@pytest.mark.asyncio
async def test_history_database_is_isolated_populated_and_idempotent() -> None:
    descriptor = history_database_descriptor()
    assert descriptor["isolated_from_primary"] is True

    before = await history_health()
    assert before["status"] == "healthy"
    assert before["snapshot_count"] > 0
    assert before["signal_count"] > 0

    result = await sync_history(mode="integration_test")
    assert result["snapshots_seen"] == before["snapshot_count"]
    assert result["snapshots_written"] == 0
    assert result["signals_written"] == 0

    async with get_history_db() as db:
        unique_snapshots = int(
            (await db.execute(select(func.count(SituationHistorySnapshot.snapshot_id.distinct())))).scalar_one()
        )
        snapshot_count = int(
            (await db.execute(select(func.count()).select_from(SituationHistorySnapshot))).scalar_one()
        )
        signal_count = int(
            (await db.execute(select(func.count()).select_from(SituationHistorySignal))).scalar_one()
        )
    assert unique_snapshots == snapshot_count
    assert signal_count >= before["signal_count"]
