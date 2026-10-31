"""Run-local raw-response capture shared by async and blocking connectors.

The transport calls this before decoding JSON, XML, HTML, or PDF. Parsers get
bytes reread from the immutable artifact, so the persisted digest describes
exactly the input they consumed. Context propagates through asyncio.to_thread;
explicit adapter executors must use copy_context().run too.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .artifacts import ArtifactWriter, read_artifact, run_artifact_io

_current: ContextVar[SourceCapture | None] = ContextVar("source_capture", default=None)


class SourceCapture:
    def __init__(self, task_uuid: str, attempt: int, line: str, *, writer=None):
        self.writer = writer or ArtifactWriter(task_uuid, attempt, line)
        self.loop = asyncio.get_running_loop()
        self.line = line
        self.error: BaseException | None = None
        self.stage = "collect"
        self.lock = asyncio.Lock()

    async def capture(self, payload: bytes, url: str, media_type: str = "") -> bytes:
        try:
            if asyncio.get_running_loop() is not self.loop:
                raise RuntimeError(
                    "SourceCapture must be awaited on its owning event loop"
                )
            if self.stage != "collect":
                raise RuntimeError(f"Source access is forbidden during {self.stage}")
            kind = {
                "literature": "literature_payload",
                "situation": "situation_feed",
                "knowledge": "knowledge_payload",
            }.get(self.line)
            if kind is None:
                hint = (media_type + " " + url.split("?", 1)[0]).lower()
                kind = next(
                    (
                        kind
                        for token, kind in (
                            ("json", "source_json"),
                            ("csv", "csv_page"),
                            ("spreadsheet", "source_xlsx"),
                            (".xlsx", "source_xlsx"),
                            (".xls", "source_xls"),
                            ("pdf", "source_pdf"),
                            ("zip", "source_zip"),
                        )
                        if token in hint
                    ),
                    "html_report",
                )
            async with self.lock:
                parsed = urlsplit(url)
                safe_query = [
                    (key, value)
                    for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                    if key.lower()
                    not in {"api_key", "apikey", "key", "token", "access_token"}
                ]
                safe_url = urlunsplit(
                    (
                        parsed.scheme,
                        parsed.netloc,
                        parsed.path,
                        urlencode(safe_query),
                        "",
                    )
                )
                artifact = await self.writer.write(
                    payload,
                    source_url=safe_url,
                    kind=kind,
                    metadata={"media_type": media_type},
                )
            return await run_artifact_io(read_artifact, artifact)
        except BaseException as exc:
            if self.error is None:
                self.error = exc
            raise

    def verify(self) -> None:
        if self.error is not None:
            raise RuntimeError("Source artifact persistence failed") from self.error


@contextmanager
def capture_sources(task_uuid: str, attempt: int, line: str):
    capture = SourceCapture(task_uuid, attempt, line)
    token = _current.set(capture)
    try:
        yield capture
        capture.verify()
    finally:
        _current.reset(token)


@contextmanager
def capture_writer(writer: ArtifactWriter, line: str = "surveillance"):
    """Bind an existing runner-owned writer for transport response capture."""
    capture = SourceCapture(writer.task_uuid, writer.attempt, line, writer=writer)
    token = _current.set(capture)
    try:
        yield capture
        capture.verify()
    finally:
        _current.reset(token)


def assert_source_access() -> None:
    capture = _current.get()
    if capture is not None and capture.stage != "collect":
        raise RuntimeError(f"Source access is forbidden during {capture.stage}")


def is_capturing() -> bool:
    return _current.get() is not None


async def capture_async(payload: bytes, url: str, media_type: str = "") -> bytes:
    capture = _current.get()
    return await capture.capture(payload, url, media_type) if capture else payload


def capture_sync(payload: bytes, url: str, media_type: str = "") -> bytes:
    """Capture from a blocking worker thread while the owning loop is running.

    Event-loop threads must await capture_async instead. Context must propagate
    to workers (asyncio.to_thread does this automatically).
    """
    capture = _current.get()
    if capture is None:
        return payload
    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError(
                "capture_sync cannot run on an event-loop thread; "
                "await capture_async or use asyncio.to_thread"
            )
        if capture.loop.is_closed() or not capture.loop.is_running():
            raise RuntimeError("SourceCapture owning event loop is not running")
        # Wait for both durable bytes and committed metadata before parsing.
        coroutine = capture.capture(payload, url, media_type)
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, capture.loop)
        except BaseException:
            coroutine.close()
            raise
        return future.result()
    except BaseException as exc:
        if capture.error is None:
            capture.error = exc
        raise
