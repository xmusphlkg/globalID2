"""Additional model dispatcher sharing the durable queue and route limits.

The main worker owns task and lease recovery. This process must never call
global lease resets: other dispatchers may own live work. PostgreSQL
route reservation locks and work-row locks enforce shared capacity and prevent
two dispatchers from claiming the same model call.
"""
from __future__ import annotations

import asyncio
import signal

from src.ai.runtime import AIWorkDispatcher
from src.core.database import dispose_database
from src.core.logging import get_logger
from src.services.model_dispatch_ready import worker_ready

logger = get_logger(__name__)


async def run() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    try:
        while not stop.is_set() and not worker_ready():
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
        if stop.is_set():
            return
        logger.info("Auxiliary shared-queue model dispatcher started")
        await AIWorkDispatcher().run(stop)
    finally:
        await dispose_database()
        logger.info("Auxiliary shared-queue model dispatcher stopped")


if __name__ == "__main__":
    asyncio.run(run())
