"""Durable scheduling pauses shared by the API, scheduler and worker.

Deployment gates remain environment configuration. A pause stops new automatic
work; it neither cancels queued/running work nor blocks an explicit manual run.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from src.core.config import get_config

CHANNELS = ("surveillance", "situation")
LITERATURE_CHANNELS = ("literature",)


def _channel(value: str, channels=CHANNELS) -> str:
    if value not in channels:
        raise ValueError("Unknown realtime publication channel")
    return value


class ChannelControlStore:
    def __init__(self, path: Path, *, channels=CHANNELS):
        self.path = Path(path)
        self.channels = tuple(channels)

    def _defaults(self, *, paused: bool = False, error_code: str | None = None) -> dict:
        return {line: {"paused": paused, "updated_at": None, "error_code": error_code}
                for line in self.channels}

    def snapshot(self) -> dict[str, dict[str, Any]]:
        try:
            if self.path.stat().st_size > 64 * 1024:
                raise ValueError("Oversized control state")
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(payload, dict) or type(payload.get("schema_version")) is not int
                    or payload["schema_version"] != 1 or not isinstance(payload.get("channels"), dict)
                    or set(payload["channels"]) != set(self.channels)):
                raise ValueError("Invalid control state")
            result = self._defaults()
            for line, value in payload["channels"].items():
                _channel(line, self.channels)
                if not isinstance(value, dict) or type(value.get("paused")) is not bool:
                    raise ValueError("Invalid pause value")
                updated = value.get("updated_at")
                if updated is not None:
                    if not isinstance(updated, str) or datetime.fromisoformat(updated).tzinfo is None:
                        raise ValueError("Invalid control timestamp")
                result[line] = {"paused": value["paused"], "updated_at": updated, "error_code": None}
            return result
        except FileNotFoundError:
            return self._defaults()
        except (ValueError, TypeError, UnicodeError):
            return self._defaults(paused=True, error_code="control_state_invalid")
        except OSError:
            return self._defaults(paused=True, error_code="control_state_unavailable")

    @contextmanager
    def _write_lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(str(self.path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def set_paused(self, channel: str, paused: bool) -> dict[str, Any]:
        _channel(channel, self.channels)
        if type(paused) is not bool:
            raise ValueError("paused must be a boolean")
        with self._write_lock():
            state = self.snapshot()
            # Recover explicitly, while retaining a fail-closed pause on the
            # other channel if the former file could not be read or validated.
            updated = datetime.now(timezone.utc).isoformat()
            state[channel] = {"paused": paused, "updated_at": updated, "error_code": None}
            payload = {"schema_version": 1, "channels": {
                line: {"paused": value["paused"], "updated_at": value["updated_at"]}
                for line, value in state.items()}}
            descriptor, temporary = tempfile.mkstemp(prefix=".realtime-control-", dir=self.path.parent)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
                directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                Path(temporary).unlink(missing_ok=True)
            return state[channel]


def _store(channel: str | None = None) -> ChannelControlStore:
    if channel == "literature":
        return ChannelControlStore(get_config().data_dir / "literature-publishing-control.json", channels=LITERATURE_CHANNELS)
    return ChannelControlStore(get_config().data_dir / "realtime-publishing-control.json")


def get_realtime_controls() -> dict[str, dict[str, Any]]:
    return _store().snapshot()


async def set_realtime_control(channel: str, paused: bool) -> dict[str, Any]:
    return await asyncio.to_thread(_store(channel).set_paused, channel, paused)


def is_automatic_json_paused(channel: str) -> bool:
    store = _store(channel)
    return bool(store.snapshot()[_channel(channel, store.channels)]["paused"])
