"""One cancellable writer for a surveillance state/public output pair."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import errno
import fcntl
import hashlib
from pathlib import Path


LOCKED_ENV = "GLOBALID_SURVEILLANCE_OUTPUT_LOCKED"


@asynccontextmanager
async def surveillance_output_lock(state_dir: Path, public_dir: Path):
    """Hold the cross-process output lock until export and freezing finish.

    Acquisition polls LOCK_NB in this coroutine, so cancelling a waiter cannot
    leave a background thread that later acquires and leaks the lock. A parent
    may pass LOCKED_ENV to its child exporter; this helper deliberately never
    skips acquisition based on process environment. The CLI/API wrapper owns
    that explicit parent-child protocol.
    """
    state, public = Path(state_dir).resolve(), Path(public_dir).resolve()
    key = hashlib.sha256((str(state) + "\0" + str(public)).encode()).hexdigest()
    directory = state.parent / ".surveillance-output-locks"
    directory.mkdir(parents=True, exist_ok=True)
    # Never unlink lock files: another waiting process may already hold the
    # inode open, and replacing it would create a second independent lock.
    with (directory / key).open("a") as descriptor:
        acquired = False
        try:
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError as error:
                    if error.errno not in {errno.EACCES, errno.EAGAIN}:
                        raise
                    await asyncio.sleep(0.05)
            yield
        finally:
            if acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
