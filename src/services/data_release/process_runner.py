"""Subprocess execution boundary for release workflows."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import re
import signal
import shlex
from typing import Any, Awaitable, Callable, Optional, Protocol

from sqlalchemy.exc import DBAPIError, TimeoutError as SQLAlchemyTimeoutError

from src.services.exceptions import TaskCancelledError


class WorkbookTaskManager(Protocol):
    async def add_workbook_entry(self, task_uuid: str, **kwargs: Any) -> Any: ...

    async def is_cancel_requested(self, task_uuid: str) -> bool: ...

    async def update_task_progress(self, task_uuid: str, percent: int) -> Any: ...


TerminateProcess = Callable[..., Awaitable[None]]

OUTPUT_CHUNK_MAX_LINES = 40
OUTPUT_CHUNK_MAX_CHARS = 4000
MAX_PERSISTED_OUTPUT_CHUNKS = 2
OUTPUT_TAIL_MAX_LINES = 120
COMPLETION_TAIL_LINES = 12
FAILURE_TAIL_LINES = 40
OUTPUT_READ_CHUNK_BYTES = 8192
TASK_CANCEL_CHECK_INTERVAL_SECONDS = 5
TASK_CANCEL_STORE_GRACE_SECONDS = 60
COMMAND_PROGRESS_INTERVAL_SECONDS = 60
ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
COUNTRY_PROGRESS_RE = re.compile(r"^SITE_EXPORT_COUNTRIES (\d+)/(\d+) country=([A-Z0-9-]+)$")


class ReleaseCommandError(RuntimeError):
    """A failed release subprocess with machine-readable stage context."""

    def __init__(
        self,
        message: str,
        *,
        release_stage: str | None,
        title: str,
        returncode: int | None = None,
        timed_out: bool = False,
    ) -> None:
        super().__init__(message)
        self.release_stage = release_stage
        self.title = title
        self.returncode = returncode
        self.timed_out = timed_out


def clean_output_line(value: str) -> str:
    """Remove terminal control sequences before persisting command output."""

    return ANSI_ESCAPE_RE.sub("", value).rstrip("\r\n")


def _process_group_running(group_id: int) -> bool:
    """Linux descendants can outlive the leader; zombies can no longer write."""
    procfs = Path('/proc')
    if not procfs.is_dir():  # pragma: no cover - production workers use Linux.
        return False
    for entry in procfs.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / 'stat').read_text().rpartition(')')[2].split()
            if len(fields) > 2 and int(fields[2]) == group_id and fields[0] not in {'Z', 'X'}:
                return True
        except (OSError, ValueError):
            continue
    return False


async def _wait_owned_task(task: asyncio.Task) -> tuple[Any, bool]:
    """Wait for owned subprocess work despite repeated caller cancellation."""
    cancelled = False
    while True:
        try:
            return await asyncio.shield(task), cancelled
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True


async def _terminate_before_exit(proc: asyncio.subprocess.Process, terminate: TerminateProcess) -> None:
    _, cancelled = await _wait_owned_task(asyncio.create_task(terminate(proc)))
    if cancelled:
        raise asyncio.CancelledError


async def terminate_process_tree(
    proc: asyncio.subprocess.Process,
    *,
    grace_seconds: float = 5,
) -> None:
    """Terminate a command and every subprocess in its dedicated session."""
    if proc.returncode is not None and os.name != "posix":
        return

    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:  # pragma: no cover - production release workers run on Linux.
            proc.terminate()
    except ProcessLookupError:
        pass

    if proc.returncode is None:
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
    if os.name == "posix":
        # SIGKILL delivery is asynchronous. Keep snapshot/output locks until
        # every surviving group member has exited, even if its leader exited
        # earlier. An exited zombie needs no further wait to stop file writes.
        while _process_group_running(proc.pid):
            await asyncio.sleep(0.01)


def subprocess_environment(overrides: dict[str, str | None] | None) -> dict[str, str]:
    """None removes an inherited setting while preserving parser defaults."""
    result = os.environ.copy()
    for key, value in (overrides or {}).items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = value
    return result


async def run_capture(
    cmd: list[str],
    *,
    cwd: Path,
    env: Optional[dict[str, str | None]] = None,
    timeout: int = 30,
    terminate: TerminateProcess = terminate_process_tree,
) -> dict[str, Any]:
    merged_env = subprocess_environment(env)
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
    env: Optional[dict[str, str | None]] = None,
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

    merged_env = subprocess_environment(env)
    spawn_task = asyncio.create_task(asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        env=merged_env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=os.name == "posix",
    ))
    try:
        proc = await asyncio.shield(spawn_task)
    except FileNotFoundError as exc:
        raise RuntimeError(f"{title} failed to start: {exc}") from exc
    except BaseException:
        # Cancellation during spawn must not lose the new process handle.
        # Finish taking ownership, then stop it before the caller's locks exit.
        try:
            proc, _ = await _wait_owned_task(spawn_task)
        except Exception:
            pass
        else:
            await _terminate_before_exit(proc, terminate)
        raise

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    started_at = loop.time()
    next_progress_report = started_at + COMMAND_PROGRESS_INTERVAL_SECONDS
    next_cancel_check = 0.0
    cancel_store_unavailable_since: float | None = None
    chunk_lines: list[str] = []
    chunk_index = 1
    output_tail: list[str] = []
    total_output_lines = 0
    persisted_output_chunks = 0
    suppressed_output_chunks = 0
    suppressed_output_lines = 0
    pending_output_fragment = ""
    latest_progress = 22

    def record_output_text(value: str) -> None:
        nonlocal output_tail, total_output_lines
        text = clean_output_line(value)
        if not text:
            return
        chunk_lines.append(text)
        output_tail.append(text)
        total_output_lines += 1
        if len(output_tail) > OUTPUT_TAIL_MAX_LINES:
            output_tail = output_tail[-OUTPUT_TAIL_MAX_LINES:]

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

    async def report_progress() -> None:
        elapsed = int(loop.time() - started_at)
        latest_line = output_tail[-1] if output_tail else "No output yet"
        await task_manager.add_workbook_entry(
            task_uuid,
            entry_type="info",
            title=f"{title} Progress",
            content=f"Running for {elapsed}s; {total_output_lines} output lines. Latest: {latest_line}",
            content_type="text",
            metadata={
                **metadata,
                "event": "command_progress",
                "command_event": metadata.get("event"),
                "elapsed_seconds": elapsed,
                "output_lines": total_output_lines,
            },
        )

    stopped = False

    async def stop_process() -> None:
        nonlocal stopped
        if not stopped:
            await terminate(proc)
            stopped = True

    async def fail_on_timeout() -> None:
        if pending_output_fragment:
            record_output_text(pending_output_fragment)
        await stop_process()
        await flush_chunk(force=True)
        timeout_text = f"{timeout_seconds:g} seconds"
        failure_tail = "\n".join(output_tail[-FAILURE_TAIL_LINES:])
        message = (
            f"{title} timed out after {timeout_text}; terminated process group "
            f"{proc.pid}. Command: {metadata['command']}"
        )
        if failure_tail:
            message += f"\nRecent output ({total_output_lines} lines total):\n{failure_tail}"
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
            logger.warning("Failed to write command timeout workbook entry: {}", exc)
        raise ReleaseCommandError(
            message,
            release_stage=str(metadata.get("event") or "").strip() or None,
            title=title,
            returncode=proc.returncode,
            timed_out=True,
        )

    try:
        while True:
            now = loop.time()
            if now >= next_progress_report:
                try:
                    await report_progress()
                except Exception as exc:
                    logger.warning("{} progress entry unavailable: {}", title, exc)
                next_progress_report = loop.time() + COMMAND_PROGRESS_INTERVAL_SECONDS
            if now >= next_cancel_check:
                next_cancel_check = now + TASK_CANCEL_CHECK_INTERVAL_SECONDS
                try:
                    cancel_requested = await task_manager.is_cancel_requested(task_uuid)
                    cancel_store_unavailable_since = None
                except (TimeoutError, OSError, DBAPIError, SQLAlchemyTimeoutError) as exc:
                    if cancel_store_unavailable_since is None:
                        cancel_store_unavailable_since = now
                        logger.warning("{} cancellation check temporarily unavailable: {}", title, type(exc).__name__)
                    if now - cancel_store_unavailable_since >= TASK_CANCEL_STORE_GRACE_SECONDS:
                        await stop_process()
                        raise ReleaseCommandError(
                            f"{title} stopped: task-store cancellation checks failed for "
                            f"{TASK_CANCEL_STORE_GRACE_SECONDS} seconds ({type(exc).__name__})",
                            release_stage=str(metadata.get("event") or "").strip() or None,
                            title=title,
                            timed_out=True,
                        ) from exc
                    cancel_requested = False
                if cancel_requested:
                    if pending_output_fragment:
                        record_output_text(pending_output_fragment)
                        pending_output_fragment = ""
                    await stop_process()
                    if metadata.get("event") == "generate_site_data":
                        try:
                            await flush_chunk()
                            await task_manager.add_workbook_entry(
                                task_uuid,
                                entry_type="warning",
                                title=f"{title} Cancelled",
                                content="\n".join(output_tail[-FAILURE_TAIL_LINES:]) or "No output was captured.",
                                content_type="text",
                                metadata={**metadata, "event": "command_cancelled"},
                            )
                        except Exception as exc:
                            logger.warning("{} cancellation output unavailable: {}", title, exc)
                    raise TaskCancelledError(f"Cancellation requested while running: {title}")

            remaining_seconds = deadline - loop.time()
            if remaining_seconds <= 0:
                await fail_on_timeout()

            try:
                chunk = await asyncio.wait_for(
                    proc.stdout.read(OUTPUT_READ_CHUNK_BYTES),
                    timeout=min(cancel_poll_seconds, remaining_seconds),
                )
            except asyncio.TimeoutError:
                if loop.time() >= deadline:
                    await fail_on_timeout()
                continue
            if not chunk:
                break

            decoded = pending_output_fragment + chunk.decode("utf-8", errors="replace")
            lines = decoded.splitlines(keepends=True)
            pending_output_fragment = ""
            for line in lines:
                if line.endswith(("\n", "\r")):
                    record_output_text(line)
                    if metadata.get("event") == "generate_site_data":
                        match = COUNTRY_PROGRESS_RE.match(clean_output_line(line))
                        if match and int(match.group(2)) > 0:
                            country_progress = 22 + 6 * int(match.group(1)) // int(match.group(2))
                            if country_progress > latest_progress:
                                latest_progress = country_progress
                                try:
                                    await task_manager.update_task_progress(task_uuid, latest_progress)
                                except Exception as exc:
                                    logger.warning("{} country progress unavailable: {}", title, exc)
                else:
                    pending_output_fragment = line

            if pending_output_fragment and len(pending_output_fragment) >= OUTPUT_CHUNK_MAX_CHARS:
                record_output_text(pending_output_fragment)
                pending_output_fragment = ""

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
        if pending_output_fragment:
            record_output_text(pending_output_fragment)
        await flush_chunk(force=True)

        if returncode != 0:
            failure_tail = "\n".join(output_tail[-FAILURE_TAIL_LINES:])
            failure_metadata = {
                **metadata,
                "event": "command_failed",
                "command_event": metadata.get("event"),
                "returncode": returncode,
                "total_output_lines": total_output_lines,
                "stored_output_chunks": persisted_output_chunks,
                "suppressed_output_chunks": suppressed_output_chunks,
                "suppressed_output_lines": suppressed_output_lines,
                "output_compacted": suppressed_output_chunks > 0,
            }
            failure_content = (
                f"Command failed with exit code {returncode}.\n"
                f"Output: {total_output_lines} lines total; "
                f"{suppressed_output_lines} lines omitted from chunk records.\n"
                + (
                    f"Final output:\n{failure_tail}"
                    if failure_tail
                    else "No output was captured."
                )
            )
            try:
                await task_manager.add_workbook_entry(
                    task_uuid,
                    entry_type="error",
                    title=f"{title} Failed",
                    content=failure_content,
                    content_type="text",
                    metadata=failure_metadata,
                )
            except Exception as exc:  # pragma: no cover - lifecycle records the error.
                logger.warning("Failed to write command failure workbook entry: {}", exc)
            raise ReleaseCommandError(
                f"{title} failed with exit code {returncode}.\n{failure_tail}".rstrip(),
                release_stage=str(metadata.get("event") or "").strip() or None,
                title=title,
                returncode=returncode,
            )

        completion_metadata = {
            **metadata,
            "elapsed_seconds": round(loop.time() - started_at, 3),
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
    except BaseException:
        # Async cancellation, output failures and task-store errors all stop
        # the complete group before outer snapshot/output locks can release.
        await _terminate_before_exit(proc, lambda _proc: stop_process())
        raise
