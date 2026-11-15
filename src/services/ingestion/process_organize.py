"""Keep CPU-heavy source parsing out of the control worker's interpreter."""
from __future__ import annotations

import asyncio
import multiprocessing
import os
import tempfile
from concurrent.futures import ProcessPoolExecutor
from weakref import WeakKeyDictionary

_limits = WeakKeyDictionary()


def _initialize(temporary: str) -> None:
    # The parent owns the root, including when terminating a CPU-bound parser
    # prevents that child's TemporaryDirectory finalizers from running.
    tempfile.tempdir = temporary


def _limit() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if loop not in _limits:
        workers = int(os.environ.get("GIDS_INGESTION_PROCESS_WORKERS", "2"))
        if not 1 <= workers <= 4:
            raise ValueError("GIDS_INGESTION_PROCESS_WORKERS must be between 1 and 4")
        _limits[loop] = asyncio.Semaphore(workers)
    return _limits[loop]


def _terminate(executor: ProcessPoolExecutor) -> None:
    # Python < 3.14 has no public terminate_workers method. Join before returning
    # so cancellation cannot leave a parser reading its temporary source files.
    processes = list((getattr(executor, "_processes", None) or {}).values())
    for process in processes:
        if process.is_alive():
            process.terminate()
    for process in processes:
        process.join(timeout=0.5)
        if process.is_alive():
            process.kill()
    for process in processes:
        process.join()
    executor.shutdown(wait=False, cancel_futures=True)


async def organize_in_process(function, request):
    """Run one offline parser with bounded concurrency and cancellation cleanup.

    Requests contain only durable artifact metadata and source options. No DB
    sessions, live clients, or database writes cross the spawn boundary.
    """
    async with _limit():
        with tempfile.TemporaryDirectory(prefix="globalid-organize-process-") as temporary:
            executor = ProcessPoolExecutor(
                max_workers=1, mp_context=multiprocessing.get_context("spawn"),
                initializer=_initialize, initargs=(temporary,),
            )
            try:
                result = await asyncio.wrap_future(executor.submit(function, request))
                await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)
                return result
            except BaseException:
                _terminate(executor)
                raise
