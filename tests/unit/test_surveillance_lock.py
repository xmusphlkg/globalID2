import asyncio
import os
from pathlib import Path
import sys

import pytest

from src.generation.surveillance_lock import LOCKED_ENV, surveillance_output_lock


async def test_cancelled_holder_releases_output_pair(tmp_path):
    ready = asyncio.Event()
    state, public = tmp_path / "state", tmp_path / "public"

    async def owner():
        async with surveillance_output_lock(state, public):
            ready.set()
            await asyncio.Event().wait()

    work = asyncio.create_task(owner())
    await asyncio.wait_for(ready.wait(), 5)
    work.cancel()
    with pytest.raises(asyncio.CancelledError):
        await work
    async with asyncio.timeout(5):
        async with surveillance_output_lock(state, public):
            pass


async def test_cancelling_waiter_cannot_later_acquire_and_leak_lock(tmp_path, monkeypatch):
    # The primitive ignores this flag; only an explicit exporter wrapper may
    # skip its nested acquisition after a parent has already taken the lock.
    monkeypatch.setenv(LOCKED_ENV, "1")
    state, public = tmp_path / "state", tmp_path / "public"
    entered = asyncio.Event()

    async def waiter():
        async with surveillance_output_lock(state, public):
            entered.set()

    async with surveillance_output_lock(state, public):
        work = asyncio.create_task(waiter())
        await asyncio.sleep(0.12)
        assert not entered.is_set()
        work.cancel()
        with pytest.raises(asyncio.CancelledError):
            await work
    async with asyncio.timeout(5):
        async with surveillance_output_lock(state, public):
            assert not entered.is_set()


async def test_different_output_pairs_do_not_block_each_other(tmp_path):
    async with surveillance_output_lock(tmp_path / "state", tmp_path / "public"):
        async with asyncio.timeout(5):
            async with surveillance_output_lock(tmp_path / "other-state", tmp_path / "other-public"):
                pass


async def test_resolved_path_aliases_share_the_same_process_lock(tmp_path):
    state, public = tmp_path / "state", tmp_path / "public"
    state.mkdir()
    public.mkdir()
    (tmp_path / "state-alias").symlink_to(state, target_is_directory=True)
    entered = asyncio.Event()

    async def waiter():
        async with surveillance_output_lock(tmp_path / "state-alias", public):
            entered.set()

    async with surveillance_output_lock(state, public):
        work = asyncio.create_task(waiter())
        await asyncio.sleep(0.12)
        assert not entered.is_set()
    await asyncio.wait_for(work, 5)
    assert entered.is_set()


async def test_other_process_waits_until_owner_releases_and_process_exit_releases(tmp_path):
    state, public = tmp_path / "state", tmp_path / "public"
    script = """
import asyncio, sys
from pathlib import Path
from src.generation.surveillance_lock import surveillance_output_lock
async def main():
    print('STARTING', flush=True)
    async with surveillance_output_lock(Path(sys.argv[1]), Path(sys.argv[2])):
        print('ACQUIRED', flush=True)
        await asyncio.Event().wait()
asyncio.run(main())
"""
    process = None
    try:
        async with surveillance_output_lock(state, public):
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-u", "-c", script, str(state), str(public),
                cwd=Path(__file__).resolve().parents[2],
                env={**os.environ, LOCKED_ENV: "1"},
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            assert await asyncio.wait_for(process.stdout.readline(), 5) == b"STARTING\n"
            line = asyncio.create_task(process.stdout.readline())
            done, _ = await asyncio.wait({line}, timeout=0.3)
            assert not done, "Child entered while the parent still owns the output lock"
        assert await asyncio.wait_for(line, 5) == b"ACQUIRED\n"
        process.terminate()
        await asyncio.wait_for(process.wait(), 5)
        async with asyncio.timeout(5):
            async with surveillance_output_lock(state, public):
                pass
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
