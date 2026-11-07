import asyncio
from unittest.mock import AsyncMock

import pytest

from src.services import task_worker


@pytest.mark.asyncio
async def test_database_timeout_does_not_terminate_worker_claim_loop(monkeypatch):
    claim = AsyncMock(side_effect=[TimeoutError("database unavailable"), ("task-1", "crawl")])
    monkeypatch.setattr(task_worker, "_claim_next_task_uuid", claim)
    assert await task_worker._claim_next_task_safely("owner") is None
    assert await task_worker._claim_next_task_safely("owner") == ("task-1", "crawl")


@pytest.mark.asyncio
async def test_worker_claim_preserves_cancellation(monkeypatch):
    monkeypatch.setattr(task_worker, "_claim_next_task_uuid", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await task_worker._claim_next_task_safely("owner")
