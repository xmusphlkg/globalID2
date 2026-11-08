"""Cancelled release commands cannot outlive their parent's output lock."""

import asyncio
import os
from pathlib import Path
import signal
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.generation.surveillance_lock import LOCKED_ENV, surveillance_output_lock
from src.services.data_release import process_runner as runner

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Real Linux process-group ownership checks')


CHILD = r'''
import json, os, pathlib, signal, subprocess, sys, time
root = pathlib.Path(sys.argv[1])
grandchild = r"""
import os, pathlib, signal, sys, time
root = pathlib.Path(sys.argv[1])
signal.signal(signal.SIGTERM, signal.SIG_IGN)
(root / 'grandchild.pid').write_text(str(os.getpid()))
while True:
    with (root / 'writes.log').open('a') as stream:
        stream.write(os.environ['GLOBALID_SURVEILLANCE_OUTPUT_LOCKED'] + '\n')
    time.sleep(.005)
"""
proc = subprocess.Popen([sys.executable, '-c', grandchild, str(root)])
(root / 'parent.pid').write_text(str(os.getpid()))
while not (root / 'writes.log').exists(): time.sleep(.005)
print('writer and grandchild ready', flush=True)
if sys.argv[2] == 'exit': sys.exit(0)
while True: time.sleep(1)
'''


async def _until(predicate, timeout=5):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError('Local subprocess fixture did not reach the expected state')
        await asyncio.sleep(.005)


def _running(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rpartition(')')[2].split()[0] not in {'Z', 'X'}
    except FileNotFoundError:
        return False


def _manager():
    return SimpleNamespace(add_workbook_entry=AsyncMock(), is_cancel_requested=AsyncMock(return_value=False), update_task_progress=AsyncMock())


async def _terminate(proc):
    await runner.terminate_process_tree(proc, grace_seconds=.05)


def _command(root, *, manager=None, terminate=_terminate, parent_exits=False):
    return runner.run_logged_command(
        'local-cancellation-fixture', title='Local writer',
        cmd=[sys.executable, '-c', CHILD, str(root), 'exit' if parent_exits else 'wait'],
        cwd=root, task_manager=manager or _manager(), logger=SimpleNamespace(warning=lambda *_: None),
        terminate=terminate, env={LOCKED_ENV: '1'}, timeout_seconds=20, cancel_poll_seconds=.02,
    )


async def _assert_stopped(root):
    parent = int((root / 'parent.pid').read_text())
    grandchild = int((root / 'grandchild.pid').read_text())
    assert not _running(parent)
    assert not _running(grandchild)
    assert not runner._process_group_running(parent)
    content = (root / 'writes.log').read_bytes()
    assert content and set(content.splitlines()) == {b'1'}
    await asyncio.sleep(.05)
    assert (root / 'writes.log').read_bytes() == content


async def _emergency_cleanup(task, root):
    # A failed assertion must never leave the deliberately stubborn fixture.
    if (root / 'parent.pid').exists():
        try:
            os.killpg(int((root / 'parent.pid').read_text()), signal.SIGKILL)
        except ProcessLookupError:
            pass
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('parent_exits', [False, True])
async def test_coroutine_cancellation_waits_for_parent_and_stubborn_grandchild(tmp_path, parent_exits):
    task = asyncio.create_task(_command(tmp_path, parent_exits=parent_exits))
    try:
        await _until(lambda: (tmp_path / 'writes.log').exists())
        if parent_exits:
            parent = int((tmp_path / 'parent.pid').read_text())
            await _until(lambda: not _running(parent))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await _assert_stopped(tmp_path)
    finally:
        await _emergency_cleanup(task, tmp_path)


async def test_output_or_task_store_exception_retains_original_error_and_stops_writers(tmp_path):
    manager = _manager()
    async def fail(_uuid):
        await _until(lambda: (tmp_path / 'writes.log').exists())
        raise LookupError('task-store fixture failure')
    manager.is_cancel_requested = fail
    task = asyncio.create_task(_command(tmp_path, manager=manager))
    try:
        with pytest.raises(LookupError, match='task-store fixture failure'):
            await task
        await _assert_stopped(tmp_path)
    finally:
        await _emergency_cleanup(task, tmp_path)


async def test_repeated_cancellation_cannot_release_output_lock_before_cleanup(tmp_path):
    cleaning = asyncio.Event()
    allow_cleanup = asyncio.Event()
    acquired = asyncio.Event()
    state, public = tmp_path / 'state', tmp_path / 'public'
    async def delayed_terminate(proc):
        cleaning.set()
        await allow_cleanup.wait()
        await _terminate(proc)
    async def owned_command():
        async with surveillance_output_lock(state, public):
            await _command(tmp_path, terminate=delayed_terminate)
    async def competing_writer():
        async with surveillance_output_lock(state, public):
            acquired.set()
    task = asyncio.create_task(owned_command())
    competitor = None
    try:
        await _until(lambda: (tmp_path / 'writes.log').exists())
        task.cancel()
        await cleaning.wait()
        task.cancel()
        competitor = asyncio.create_task(competing_writer())
        await asyncio.sleep(.03)
        assert not task.done()
        assert not acquired.is_set()
        allow_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await _assert_stopped(tmp_path)
        await asyncio.wait_for(competitor, 2)
        assert acquired.is_set()
    finally:
        allow_cleanup.set()
        await _emergency_cleanup(task, tmp_path)
        if competitor is not None:
            await competitor


async def test_cancellation_during_spawn_takes_ownership_before_stopping_group(monkeypatch, tmp_path):
    actual_spawn = asyncio.create_subprocess_exec
    spawned = asyncio.Event()
    allow_handle = asyncio.Event()
    async def delayed_handle(*args, **kwargs):
        proc = await actual_spawn(*args, **kwargs)
        spawned.set()
        await allow_handle.wait()
        return proc
    monkeypatch.setattr(runner.asyncio, 'create_subprocess_exec', delayed_handle)
    task = asyncio.create_task(_command(tmp_path))
    try:
        await spawned.wait()
        await _until(lambda: (tmp_path / 'writes.log').exists())
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done()
        allow_handle.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await _assert_stopped(tmp_path)
    finally:
        allow_handle.set()
        await _emergency_cleanup(task, tmp_path)
