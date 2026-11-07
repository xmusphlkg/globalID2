"""Read large public releases without retaining the article catalogue in RAM.

Every record passes the existing public validator. Cross-record identifiers,
blocker deduplication and signal/gap membership live in a temporary SQLite file.
The returned snapshot contains counts, the release date and one weekly brief.
"""
from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, BinaryIO

import ijson
from ijson.common import ObjectBuilder

from .health_check_utils import _blocker_categories
from .release_validation import validate_public_research_payload

_MAX_STREAM_BYTES = 2 * 1024 * 1024 * 1024
_MAX_RECORD_BYTES = 8 * 1024 * 1024
_MAX_STRING_BYTES = 2 * 1024 * 1024
_MAX_DEPTH = 128
_STRING_MARKS = re.compile(rb'["\\]')
_COLLECTIONS = {"articles", "preprints", "weekly_briefs", "integrity_alerts"}
_TARGETS = {
    **{f"{key}.item": (key, 3) for key in _COLLECTIONS},
    "surveillance_evidence.signals.item": ("signals", 4),
    "surveillance_evidence.evidence_gaps.item": ("evidence_gaps", 4),
}


class _ReadLimit(ValueError):
    pass


class _InvalidShape(ValueError):
    pass


class _BoundedReader:
    """Reject oversized string tokens before handing their chunks to the parser."""

    def __init__(self, source: BinaryIO) -> None:
        self.source = source
        self.total = 0
        self.in_string = False
        self.escaped = False
        self.string_bytes = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self.source.read(min(size, 65536) if size >= 0 else 65536)
        self.total += len(chunk)
        if self.total > _MAX_STREAM_BYTES:
            raise _ReadLimit
        previous = 0
        for match in _STRING_MARKS.finditer(chunk):
            index = match.start()
            if self.in_string:
                self.string_bytes += index - previous + 1
                if self.string_bytes > _MAX_STRING_BYTES:
                    raise _ReadLimit
                if index > previous:
                    self.escaped = False
                if self.escaped:
                    self.escaped = False
                elif chunk[index] == 92:
                    self.escaped = True
                else:
                    self.in_string = False
            elif chunk[index] == 34:
                self.in_string = True
                self.string_bytes = 0
                self.escaped = False
            previous = index + 1
        if self.in_string:
            self.string_bytes += len(chunk) - previous
            if len(chunk) > previous:
                self.escaped = False
            if self.string_bytes > _MAX_STRING_BYTES:
                raise _ReadLimit
        return chunk


def _fingerprint(stat: os.stat_result) -> tuple[int, int, int, int]:
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def read_streamed_release(path: Path) -> tuple[dict[str, Any] | None, str]:
    """Return trusted, compact health inputs only after validating the whole file."""
    try:
        with path.open("rb") as source, tempfile.TemporaryDirectory(prefix="research-health-") as directory:
            before = os.fstat(source.fileno())
            if before.st_size > _MAX_STREAM_BYTES:
                return None, "too_large"
            with sqlite3.connect(str(Path(directory) / "validation.sqlite")) as db:
                db.execute("PRAGMA cache_size=-2048")
                db.execute("CREATE TABLE seen (kind TEXT, value TEXT, PRIMARY KEY(kind,value)) WITHOUT ROWID")
                db.execute("CREATE TABLE blockers (message TEXT PRIMARY KEY) WITHOUT ROWID")
                metrics = _scan(source, db)
                if _fingerprint(before) != _fingerprint(os.fstat(source.fileno())) or _fingerprint(before) != _fingerprint(path.stat()):
                    return None, "changed_during_read"
                return metrics, "ok"
    except FileNotFoundError:
        return None, "missing"
    except _ReadLimit:
        return None, "record_too_large"
    except (OSError, sqlite3.Error):
        return None, "unreadable"
    except (ijson.JSONError, UnicodeDecodeError):
        return None, "invalid_json"
    except (TypeError, ValueError, AttributeError, OverflowError):
        return None, "invalid_shape"


def _scan(source: BinaryIO, db: sqlite3.Connection) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    last_updated: Any = None
    latest_brief: dict[str, Any] | None = None
    previous_date: str | None = None

    def seen(kind: str, value: str) -> bool:
        return db.execute("INSERT OR IGNORE INTO seen VALUES (?, ?)", (kind, value)).rowcount == 0

    def block(message: str) -> None:
        db.execute("INSERT OR IGNORE INTO blockers VALUES (?)", (message,))

    def consume(kind: str, record: dict[str, Any]) -> None:
        nonlocal latest_brief, previous_date
        index = counts[kind]
        counts[kind] += 1
        if kind in {"articles", "preprints"}:
            identity = str(record.get("article_id") or "")
            doi = str(record.get("doi") or "").strip().lower()
            if identity and seen("article", identity):
                block(f"duplicate public article_id: {identity}")
            if doi and seen("doi", doi):
                block(f"duplicate public DOI: {doi}")
            summary = record.get("summary") or {}
            counts["bilingual"] += bool(summary.get("en")) and bool(summary.get("zh"))
            if kind == "articles":
                published = str(record.get("published_at") or "")
                if previous_date is not None and previous_date < published:
                    block("main public catalogue is not sorted by published_at descending")
                previous_date = published
            payload = {kind: [record]}
        elif kind == "signals":
            identity = str(record.get("signal_id") or "")
            if not record.get("exact_articles"):
                seen("signal_without_exact", identity)
            # Missing-gap validation is deferred until all gaps have been read.
            payload = {"surveillance_evidence": {"signals": [record], "evidence_gaps": [{"signal_id": identity}]}}
        elif kind == "evidence_gaps":
            seen("gap", str(record.get("signal_id") or ""))
            return
        else:
            payload = {kind: [record]}
            if kind == "weekly_briefs" and latest_brief is None:
                # Only these fields are needed by the digest freshness/provenance check.
                latest_brief = {key: record[key] for key in ("end_date", "brief_status", "byline") if key in record}
                latest_brief["cited_finding_count"] = len(record.get("cited_findings") or [])
        for message in validate_public_research_payload(payload):
            if kind in {"articles", "preprints"} and not record.get("article_id"):
                message = message.replace(f"{kind}[0]", f"{kind}[{index}]")
            block(message)

    depth = 0
    builder: ObjectBuilder | None = None
    capture_kind = ""
    capture_depth = 0
    record_bytes = 0
    structure_keys: dict[str, set[str]] = {"": set(), "surveillance_evidence": set()}
    first = True
    for prefix, event, value in ijson.parse(_BoundedReader(source), use_float=True):
        if first:
            first = False
            if (prefix, event) != ("", "start_map"):
                raise _InvalidShape
        if event in {"start_map", "start_array"}:
            depth += 1
            if depth > _MAX_DEPTH:
                raise _ReadLimit
        if event == "map_key" and prefix in structure_keys and depth == (1 if not prefix else 2):
            keys = structure_keys[prefix]
            if value in keys or len(keys) >= 256:
                raise _InvalidShape
            keys.add(value)
        if builder is None:
            if prefix in _COLLECTIONS and event not in {"start_array", "end_array", "map_key", "null"}:
                raise _InvalidShape
            if prefix == "surveillance_evidence" and event not in {"start_map", "end_map", "map_key", "null"}:
                raise _InvalidShape
            if prefix in {"surveillance_evidence.signals", "surveillance_evidence.evidence_gaps"} and event not in {"start_array", "end_array", "map_key", "null"}:
                raise _InvalidShape
            if prefix == "last_updated" and depth == 1 and event != "map_key":
                if event not in {"string", "null"}:
                    raise _InvalidShape
                last_updated = value
            if prefix in _TARGETS and event not in {"end_map", "end_array", "map_key"}:
                kind, expected_depth = _TARGETS[prefix]
                if event != "start_map" or depth != expected_depth:
                    raise _InvalidShape
                builder = ObjectBuilder()
                capture_kind, capture_depth = kind, depth
                record_bytes = 0
        if builder is not None:
            record_bytes += len(value.encode("utf-8")) if isinstance(value, str) else 16
            if record_bytes > _MAX_RECORD_BYTES:
                raise _ReadLimit
            builder.event(event, value)
            if event == "end_map" and depth == capture_depth:
                consume(capture_kind, builder.value)
                builder = None
        if event in {"end_map", "end_array"}:
            depth -= 1
    if first or depth or builder is not None:
        raise _InvalidShape
    for (identity,) in db.execute("SELECT value FROM seen WHERE kind='signal_without_exact' EXCEPT SELECT value FROM seen WHERE kind='gap'"):
        block(f"signal without exact evidence has no explicit gap: {identity}")
    categories: Counter[str] = Counter()
    blocker_count = 0
    for (message,) in db.execute("SELECT message FROM blockers"):
        categories.update(_blocker_categories([message]))
        blocker_count += 1
    return {
        "article_count": counts["articles"] + counts["preprints"],
        "bilingual_count": counts["bilingual"],
        "preprint_count": counts["preprints"],
        "integrity_alert_count": counts["integrity_alerts"],
        "blocker_count": blocker_count,
        "blocker_categories": dict(sorted(categories.items())),
        "last_updated": last_updated,
        "latest_brief": latest_brief,
    }
