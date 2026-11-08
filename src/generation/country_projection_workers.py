"""Bounded process workers for cold source-aware country projections."""
from __future__ import annotations

import asyncio
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor

from src.core.database_snapshot import SNAPSHOT_ENV, validate_snapshot_id
from .direct_download.runtime import _read_cgroup_memory_limit_bytes


def default_country_workers() -> int:
    limit = _read_cgroup_memory_limit_bytes()
    if not limit:
        return 1
    gib = 1024 ** 3
    # Reserve parent state/output memory, then at least four GiB per reader.
    return max(1, min(8, os.cpu_count() or 1, (limit - 4 * gib) // (4 * gib)))


def _project_country(request: dict) -> dict:
    """Each process owns its DB pool; only projected data crosses back."""
    from src.core.database import dispose_database
    from .site_data_export import _collect_country_export, _release_projected_country_rows
    snapshot = validate_snapshot_id(request["snapshot_id"])
    os.environ[SNAPSHOT_ENV] = snapshot

    async def run():
        try:
            result = await _collect_country_export(request["country"], **request["options"])
            return _release_projected_country_rows(result)
        finally:
            await dispose_database()
    return asyncio.run(run())


def _terminate(executor: ProcessPoolExecutor) -> None:
    # Python before 3.14 has no public terminate_workers method. Retain process
    # handles before shutdown clears them, and wait until readers are gone so
    # the parent's exported snapshot can be closed safely.
    processes = list((getattr(executor, "_processes", None) or {}).values())
    terminate = getattr(executor, "terminate_workers", None)
    if terminate is not None:
        terminate()
    else:
        for process in processes:
            if process.is_alive():
                process.terminate()
    for process in processes:
        process.join(timeout=0.5)
    for process in processes:
        if process.is_alive():
            process.kill()
    for process in processes:
        process.join()
    executor.shutdown(wait=False, cancel_futures=True)


async def project_countries(requests: list[dict], *, workers: int, on_result=None) -> list[dict]:
    """Drain all workers, including on cancellation, before returning control."""
    if not 1 <= workers <= 8:
        raise ValueError("Surveillance country workers must be between 1 and 8")
    snapshot = validate_snapshot_id(os.environ.get(SNAPSHOT_ENV, ""))
    executor = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"))
    futures = []
    try:
        for request in requests:
            future = executor.submit(_project_country, {**request, "snapshot_id": snapshot})
            futures.append(asyncio.wrap_future(future))
        results = []
        for completed in asyncio.as_completed(futures):
            result = await completed
            results.append(result)
            if on_result is not None:
                on_result(result, len(results))
        await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)
        return results
    except BaseException:
        for future in futures:
            future.cancel()
        _terminate(executor)
        raise
