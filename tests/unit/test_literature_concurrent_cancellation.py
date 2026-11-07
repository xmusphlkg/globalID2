"""Provider failures must not leave collection requests running in later stages."""

import asyncio
from uuid import uuid4

import pytest

from src.literature.clients.openalex import OpenAlexClient
from src.literature.clients.unpaywall import UnpaywallClient
from src.services.ingestion.capture import capture_async, capture_sources


@pytest.mark.parametrize("provider", ["openalex", "unpaywall"])
async def test_provider_failure_cancels_and_drains_sibling_capture(monkeypatch, provider):
    started = asyncio.Event()
    finished = asyncio.Event()
    if provider == "openalex":
        client = OpenAlexClient(mailto="tests@example.org")
        options = {"batch_size": 1}
    else:
        client = UnpaywallClient(email="tests@example.org")
        options = {}

    with capture_sources(str(uuid4()), 1, "literature") as bound:
        async def blocked_write(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()

        async def get_json(_client, path, *, params):
            if "one" in (path + str(params)).lower():
                await asyncio.wait_for(started.wait(), timeout=2)
                raise RuntimeError("provider unavailable")
            await capture_async(b"raw", "https://fixture.test/data")
            return {"results": []}

        monkeypatch.setattr(bound.writer, "write", blocked_write)
        monkeypatch.setattr(client, "get_json", get_json)
        with pytest.raises(RuntimeError, match="provider unavailable"):
            await client.enrich_by_dois(
                ["10.1000/one", "10.1000/two"], concurrency=2,
                min_interval_seconds=0, **options,
            )
        assert finished.is_set()
        bound.stage = "organize"
        bound.verify()
