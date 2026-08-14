"""Subprocess execution boundary for release workflows."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import re
import signal
import shlex
from typing import Any, Awaitable, Callable, Optional, Protocol

from src.services.exceptions import TaskCancelledError


class WorkbookTaskManager(Protocol):
    async def add_workbook_entry(self, task_uuid: str, **kwargs: Any) -> Any: ...

    async def is_cancel_requested(self, task_uuid: str) -> bool: ...


TerminateProcess = Callable[..., Awaitable[None]]

OUTPUT_CHUNK_MAX_LINES = 40
OUTPUT_CHUNK_MAX_CHARS = 4000
MAX_PERSISTED_OUTPUT_CHUNKS = 2
OUTPUT_TAIL_MAX_LINES = 120
COMPLETION_TAIL_LINES = 12
ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def clean_output_line(value: str) -> str:
    """Remove terminal control sequences before persisting command output."""

    return ANSI_ESCAPE_RE.sub("", value).rstrip("\r\n")


async def terminate_process_tree(
    proc: asyncio.subprocess.Process,
    *,
    grace_seconds: float = 5,
) -> None:
    """Terminate a command and every subprocess in its dedicated session."""
    if proc.returncode is not None:
        return

    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:  # pragma: no cover - production release workers run on Linux.
            proc.terminate()
    except ProcessLookupError:
        pass

    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
    except asyncio.TimeoutError:
        pass

    try:
        if os.name == "posix":
            # The direct child can exit before one of its descendants.
            os.killpg(proc.pid, signal.SIGKILL)
        elif proc.returncode is None:  # pragma: no cover - production runs on Linux.
            proc.kill()
    except ProcessLookupError:
        pass
    if proc.returncode is None:
        await proc.wait()


async def run_capture(
    cmd: list[str],
    *,
    cwd: Path,
    env: Optional[dict[str, str]] = None,
    timeout: int = 30,
    terminate: TerminateProcess = terminate_process_tree,
) -> dict[str, Any]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            env=merged_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=os.name == "posix",
        )
    except FileNotFoundError as exc:
        return {"returncode": 127, "stdout": str(exc)}
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await terminate(proc)
        return {
            "returncode": 124,
            "stdout": (
                f"Command timed out after {timeout}s: "
                + " ".join(shlex.quote(part) for part in cmd)
            ),
        }
    return {
        "returncode": proc.returncode,
        "stdout": stdout.decode("utf-8", errors="replace").strip(),
    }


async def run_logged_command(
    task_uuid: str,
    *,
    title: str,
    cmd: list[str],
    cwd: Path,
    task_manager: WorkbookTaskManager,
    logger: Any,
    terminate: TerminateProcess,
    env: Optional[dict[str, str]] = None,
    metadata: Optional[dict[str, Any]] = None,
    timeout_seconds: float,
    cancel_poll_seconds: float,
) -> None:
    if timeout_seconds <= 0:
        raise ValueError("Logged command timeout_seconds must be greater than zero")

    metadata = dict(metadata or {})
    metadata["command"] = " ".join(shlex.quote(part) for part in cmd)
    metadata["timeout_seconds"] = timeout_seconds
    await task_manager.add_workbook_entry(
        task_uuid,
        entry_type="info",
        title=f"{title} Started",
        content=f"$ {metadata['command']}\nCWD: {cwd}",
        content_type="text",
        metadata=metadata,
    )

    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            env=merged_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=os.name == "posix",
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"{title} failed to start: {exc}") from exc

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    chunk_lines: list[str] = []
    chunk_index = 1
    output_tail: list[str] = []
    total_output_lines = 0
    persisted_output_chunks = 0
    suppressed_output_chunks = 0
    suppressed_output_lines = 0

    async def flush_chunk(force: bool = False) -> None:
        nonlocal chunk_lines, chunk_index, persisted_output_chunks
        nonlocal suppressed_output_chunks, suppressed_output_lines
        if not chunk_lines and not force:
            return
        if chunk_lines:
            if persisted_output_chunks < MAX_PERSISTED_OUTPUT_CHUNKS:
                await task_manager.add_workbook_entry(
                    task_uuid,
                    entry_type="info",
                    title=f"{title} Output #{chunk_index}",
                    content="\n".join(chunk_lines),
                    content_type="text",
                    metadata={
                        **metadata,
                        "chunk": chunk_index,
                        "event": metadata.get("event", "command_output"),
                    },
                )
                persisted_output_chunks += 1
            else:
                suppressed_output_chunks += 1
                suppressed_output_lines += len(chunk_lines)
            chunk_lines = []
            chunk_index += 1

    async def fail_on_timeout() -> None:
        await terminate(proc)
        await flush_chunk(force=True)
        timeout_text = f"{timeout_seconds:g} seconds"
        message = (
            f"{title} timed out after {timeout_text}; terminated process group "
            f"{proc.pid}. Command: {metadata['command']}"
        )
        try:
            await task_manager.add_workbook_entry(
                task_uuid,
                entry_type="error",
                title=f"{title} Timed Out",
                content=message,
                content_type="text",
                metadata={**metadata, "event": "command_timeout"},
            )
        except Exception as exc:  # pragma: no cover - lifecycle records the error.
            logger.warning("Failed to write command timeout workbook entry: %s", exc)
        raise RuntimeError(message)

    while True:
        if await task_manager.is_cancel_requested(task_uuid):
            await terminate(proc)
            raise TaskCancelledError(f"Cancellation requested while running: {title}")

        remaining_seconds = deadline - loop.time()
        if remaining_seconds <= 0:
            await fail_on_timeout()

        try:
            line = await asyncio.wait_for(
                proc.stdout.readline(),
                timeout=min(cancel_poll_seconds, remaining_seconds),
            )
        except asyncio.TimeoutError:
            if loop.time() >= deadline:
                await fail_on_timeout()
            continue
        if not line:
            break
        text = clean_output_line(line.decode("utf-8", errors="replace"))
        chunk_lines.append(text)
        output_tail.append(text)
        total_output_lines += 1
        if len(output_tail) > OUTPUT_TAIL_MAX_LINES:
            output_tail = output_tail[-OUTPUT_TAIL_MAX_LINES:]
        if (
            len(chunk_lines) >= OUTPUT_CHUNK_MAX_LINES
            or sum(len(item) for item in chunk_lines) >= OUTPUT_CHUNK_MAX_CHARS
        ):
            await flush_chunk()

    if proc.returncode is None:
        remaining_seconds = deadline - loop.time()
        if remaining_seconds <= 0:
            await fail_on_timeout()
        try:
            await asyncio.wait_for(proc.wait(), timeout=remaining_seconds)
        except asyncio.TimeoutError:
            await fail_on_timeout()

    returncode = proc.returncode
    await flush_chunk(force=True)

    if returncode != 0:
        raise RuntimeError(
            f"{title} failed with exit code {returncode}.\n" + "\n".join(output_tail[-40:])
        )

    completion_metadata = {
        **metadata,
        "total_output_lines": total_output_lines,
        "stored_output_chunks": persisted_output_chunks,
        "suppressed_output_chunks": suppressed_output_chunks,
        "suppressed_output_lines": suppressed_output_lines,
        "output_compacted": suppressed_output_chunks > 0,
    }
    completion_tail = "\n".join(output_tail[-COMPLETION_TAIL_LINES:])
    if suppressed_output_chunks:
        completion_content = (
            "Command completed successfully.\n"
            f"Output compacted: {total_output_lines} lines total; "
            f"{suppressed_output_lines} lines omitted from chunk records; "
            "the final output is retained below.\n"
            + (f"Final output:\n{completion_tail}" if completion_tail else "")
        ).rstrip()
    else:
        completion_content = completion_tail or "Command completed successfully."

    await task_manager.add_workbook_entry(
        task_uuid,
        entry_type="success",
        title=f"{title} Completed",
        content=completion_content,
        content_type="text",
        metadata=completion_metadata,
    )
