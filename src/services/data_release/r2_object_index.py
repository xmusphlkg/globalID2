"""Durable R2 object receipts and conservative upload capacity reservations.

Only successful writes or verified remote metadata belong in this database.
An inventory is a complete bucket snapshot; it is never inferred from a local
build. Reservations survive process failure, so uncertain writes cannot silently
free capacity. Recover them only after fencing publishers and listing the bucket.
All publishers and collectors of a bucket must use the same local database.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Iterable, Mapping
import uuid

from sqlalchemy import create_engine

DEFAULT_MAX_TOTAL_BYTES = 8_000_000_000
class _Row:
    """Small mapping/indexable row shared by the SQLite and SQLAlchemy paths."""
    def __init__(self, values, keys):
        self._values = tuple(values)
        self._mapping = dict(zip(keys, self._values))
    def __getitem__(self, key):
        return self._values[key] if isinstance(key, int) else self._mapping[key]
    def __iter__(self):
        return iter(self._values)


class _Result:
    def __init__(self, result):
        try:
            keys = list(result.keys())
        except Exception:
            keys = []
        self._rows = [_Row(row, keys) for row in result.fetchall()] if keys else []
    def fetchone(self):
        return self._rows.pop(0) if self._rows else None
    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows


class _PostgresAdapter:
    """Synchronous SQLAlchemy wrapper used by publishing's sync R2 client."""
    def __init__(self, url: str):
        if not url or not url.startswith(("postgresql://", "postgresql+psycopg2://")):
            raise ValueError("R2 object index requires a PostgreSQL DATABASE_URL_SYNC")
        self.engine = create_engine(url, pool_pre_ping=True, future=True)
        self.connection = self.engine.connect()
        self._in_transaction = False
    def execute(self, sql: str, params=()):
        # The index uses qmark placeholders for its test SQLite backend. The
        # psycopg2 driver uses positional %s placeholders.
        sql = sql.replace("WHERE 1", "WHERE TRUE")
        result = _Result(self.connection.exec_driver_sql(sql.replace("?", "%s"), tuple(params)))
        # SQLAlchemy 2 starts an implicit transaction for a standalone SELECT.
        # Commit it immediately after consuming the result so the next writer
        # can explicitly begin a transaction without InvalidRequestError.
        if not self._in_transaction:
            self.connection.commit()
        return result
    def executemany(self, sql: str, params):
        values = list(params)
        if not values:
            return None
        sql = sql.replace("WHERE 1", "WHERE TRUE")
        result = self.connection.exec_driver_sql(sql.replace("?", "%s"), values)
        if not self._in_transaction:
            self.connection.commit()
        return result
    def begin(self):
        if self.connection.get_transaction() is not None:
            self.connection.rollback()
        self._in_transaction = True
        return self.connection.begin()
    def commit(self, transaction):
        transaction.commit()
        self._in_transaction = False
    def rollback(self, transaction):
        transaction.rollback()
        self._in_transaction = False
    def close(self):
        self.connection.close()
        self.engine.dispose()

class R2ObjectIndexError(RuntimeError):
    """The local index cannot safely authorize an operation."""


class R2StorageBudgetExceeded(R2ObjectIndexError):
    """The bucket plus reserved uploads would exceed its storage budget."""


class R2ReservationConflict(R2ObjectIndexError):
    """An upload is already pending or must be explicitly reconciled."""



def _size(value: int) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("R2 object size must be a non-negative integer")
    return value


def _etag(value: str) -> str:
    if not isinstance(value, str) or not value.strip('"'):
        raise ValueError("R2 object ETag is required")
    return value.strip('"')


def _header_parts(value: str | None, separator: str) -> tuple[str, ...]:
    """Split an HTTP field at separators outside quoted-string values."""
    if value is None:
        return ()
    if not isinstance(value, str):
        raise ValueError("HTTP metadata must be text")
    parts, start, quoted, escaped = [], 0, False, False
    for position, char in enumerate(value):
        if escaped:
            escaped = False
        elif quoted and char == "\\":
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif char == separator and not quoted:
            if part := value[start:position].strip():
                parts.append(part)
            start = position + 1
    if part := value[start:].strip():
        parts.append(part)
    return tuple(parts)


def _parameter(value: str, *, lowercase_value: bool = False) -> tuple[str, str | None]:
    name, separator, parameter = value.partition("=")
    name = name.strip().lower()
    if not separator:
        return name, None
    parameter = parameter.strip()
    if len(parameter) >= 2 and parameter.startswith('"') and parameter.endswith('"'):
        parameter = re.sub(r"\\(.)", r"\1", parameter[1:-1])
    if lowercase_value:
        parameter = parameter.lower()
    return name, parameter


def normalize_cache_control(value: str | None) -> tuple:
    # Directive names and ordering are case-insensitive; extension values are
    # not. Keep duplicate directives instead of silently accepting conflicts.
    return tuple(sorted((_parameter(part) for part in _header_parts(value, ",")), key=repr))


def normalize_content_type(value: str | None) -> tuple:
    parts = _header_parts(value, ";")
    if not parts:
        return ()
    parameters = []
    for part in parts[1:]:
        name, parameter = _parameter(part)
        if name == "charset" and parameter is not None:
            parameter = parameter.lower()
        parameters.append((name, parameter))
    # Boundary and extension parameter values are intentionally case-sensitive.
    return parts[0].lower(), tuple(sorted(parameters, key=repr))


def normalize_content_encoding(value: str | None) -> tuple[str, ...]:
    # Encoding order describes a stack of transforms and must not be sorted.
    return tuple(part.lower() for part in _header_parts(value, ","))


def metadata_matches(item: Mapping, expected: Mapping, *, compare_encoding: bool = True) -> bool:
    """Compare serving metadata using HTTP field semantics, retaining raw receipts."""
    return (normalize_content_type(item.get("content_type")) == normalize_content_type(expected.get("content_type"))
            and normalize_cache_control(item.get("cache_control")) == normalize_cache_control(expected.get("cache_control"))
            and (not compare_encoding or normalize_content_encoding(item.get("content_encoding"))
                 == normalize_content_encoding(expected.get("content_encoding"))))


def _row_mapping(row) -> dict:
    mapping = getattr(row, "_mapping", None)
    return dict(mapping) if mapping is not None else dict(row)


class R2ObjectIndex:
    def __init__(self, endpoint: str, bucket: str, *, path: Path | str | None = None,
                 max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
                 backend: str | None = None, database_url_sync: str | None = None):
        if not isinstance(endpoint, str) or not endpoint or not isinstance(bucket, str) or not bucket:
            raise ValueError("R2 endpoint and bucket are required")
        self.max_total_bytes = _size(max_total_bytes)
        # An explicit R2_OBJECT_INDEX_PATH is also a deliberate local-test
        # backend selection. Production has no such variable and therefore
        # always uses DATABASE_URL_SYNC/PostgreSQL.
        self.backend = backend or ("sqlite" if path is not None or os.environ.get("R2_OBJECT_INDEX_PATH") else "postgresql")
        if self.backend not in {"sqlite", "postgresql"}:
            raise ValueError("R2 object index backend must be sqlite or postgresql")
        self.path = Path(path) if path is not None else None
        if self.backend == "sqlite":
            self.path = self.path or Path(os.environ.get("R2_OBJECT_INDEX_PATH") or
                         Path(__file__).resolve().parents[3] / "data/cache/r2-object-index.sqlite3")
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.endpoint, self.bucket = endpoint.rstrip("/"), bucket
        self.target = hashlib.sha256(json.dumps([self.endpoint, bucket]).encode()).hexdigest()
        if self.backend == "postgresql":
            # R2S3Client uses ``path.parent`` only for a local process lock.
            # Receipts stay in PostgreSQL; this marker is never opened as a
            # database file.
            self.path = (Path(__file__).resolve().parents[3] / "data/cache" /
                         f"r2-postgres-{self.target}.lock")
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        if self.backend == "sqlite":
            self._db = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
        else:
            database_url_sync = database_url_sync or os.environ.get("DATABASE_URL_SYNC")
            if not database_url_sync:
                from src.core.config import get_config
                database_url_sync = get_config().database.url_sync
            self._db = _PostgresAdapter(database_url_sync)
        try:
            if self.backend == "sqlite":
                self._db.execute("PRAGMA busy_timeout=30000")
                self._db.execute("PRAGMA journal_mode=WAL")
                self._db.execute("PRAGMA synchronous=FULL")
            with self._transaction() as db:
                version = db.execute("PRAGMA user_version").fetchone()[0] if self.backend == "sqlite" else 0
                db.execute("""CREATE TABLE IF NOT EXISTS r2_targets (
                    target TEXT PRIMARY KEY, endpoint TEXT NOT NULL, bucket TEXT NOT NULL,
                    inventory_at REAL, stored_bytes INTEGER NOT NULL DEFAULT 0)""")
                db.execute("""CREATE TABLE IF NOT EXISTS r2_objects (
                    target TEXT NOT NULL, key TEXT NOT NULL, bytes INTEGER NOT NULL CHECK(bytes >= 0),
                    etag TEXT NOT NULL, content_type TEXT, content_encoding TEXT, cache_control TEXT,
                    metadata_known INTEGER NOT NULL, confirmed_at REAL NOT NULL, content_sha256 TEXT,
                    PRIMARY KEY(target, key))""")
                db.execute("""CREATE TABLE IF NOT EXISTS r2_reservations (
                    token TEXT PRIMARY KEY, target TEXT NOT NULL, key TEXT NOT NULL,
                    bytes INTEGER NOT NULL CHECK(bytes >= 0), growth INTEGER NOT NULL CHECK(growth >= 0),
                    created_at REAL NOT NULL, UNIQUE(target, key))""")
                if self.backend == "sqlite":
                    db.execute("INSERT OR IGNORE INTO r2_targets(target,endpoint,bucket) VALUES(?,?,?)",
                               (self.target, self.endpoint, self.bucket))
                else:
                    db.execute("INSERT INTO r2_targets(target,endpoint,bucket) VALUES(?,?,?) ON CONFLICT (target) DO NOTHING",
                               (self.target, self.endpoint, self.bucket))
                if self.backend == "sqlite" and version == 1:
                    db.execute("ALTER TABLE r2_objects ADD COLUMN content_sha256 TEXT")
                if self.backend == "sqlite":
                    db.execute("PRAGMA user_version=3")
        except BaseException:
            self._db.close()
            raise

    @contextmanager
    def _transaction(self):
        with self._lock:
            if self.backend == "sqlite":
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    yield self._db
                    self._db.execute("COMMIT")
                except BaseException:
                    self._db.execute("ROLLBACK")
                    raise
            else:
                transaction = self._db.begin()
                try:
                    yield self._db
                    self._db.commit(transaction)
                except BaseException:
                    self._db.rollback(transaction)
                    raise

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @property
    def sync_engine(self):
        """Shared SQLAlchemy engine for PostgreSQL publication ledgers.

        SQLite test backends intentionally expose ``None``; production callers
        should use this engine or the configured DATABASE_URL_SYNC, never open a
        second SQLite database for publication state.
        """
        return getattr(self._db, "engine", None)

    @property
    def lock_dir(self) -> Path:
        """Persistent local lock directory for this PostgreSQL or test index."""
        directory = self.path.parent if self.path is not None else Path(
            os.environ.get("R2_OBJECT_INDEX_LOCK_DIR") or
            Path(__file__).resolve().parents[3] / "data/cache/r2-object-index-locks")
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def get(self, key: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM r2_objects WHERE target=? AND key=?",
                                   (self.target, key)).fetchone()
        if row is None:
            return None
        result = _row_mapping(row)
        result.pop("target")
        result["metadata_known"] = bool(result["metadata_known"])
        return result

    def snapshot(self) -> dict[str, dict]:
        """Return a consistent local inventory without any remote requests."""
        with self._lock:
            rows = self._db.execute("SELECT * FROM r2_objects WHERE target=?", (self.target,)).fetchall()
        result = {}
        for row in rows:
            item = _row_mapping(row)
            item.pop("target")
            item["metadata_known"] = bool(item["metadata_known"])
            result[item.pop("key")] = item
        return result

    def matches(self, key: str, *, size: int, etag: str, content_type: str | None,
                content_encoding: str | None = None, cache_control: str | None = None) -> bool:
        item = self.get(key)
        return bool(item and item["metadata_known"] and item["bytes"] == _size(size)
                    and item["etag"] == _etag(etag) and metadata_matches(item,
                        {"content_type": content_type, "content_encoding": content_encoding, "cache_control": cache_control}))

    def matches_content(self, key: str, content_sha256: str, content_type: str | None,
                        cache_control: str | None) -> bool:
        """Reuse a verified decoded immutable object across compression recipes.

        Only immutable callers may use this proof. It must originate from bytes
        successfully uploaded or decoded and hashed after a remote GET.
        """
        if not isinstance(content_sha256, str) or re.fullmatch(r"[a-f0-9]{64}", content_sha256) is None:
            raise ValueError("Invalid decoded R2 content digest")
        item = self.get(key)
        return bool(item and item["metadata_known"] and item["content_sha256"] == content_sha256
                    and metadata_matches(item, {"content_type": content_type, "cache_control": cache_control}, compare_encoding=False))

    def _no_pending(self, db, keys: Iterable[str] | None = None) -> None:
        if keys is None:
            pending = db.execute("SELECT 1 FROM r2_reservations WHERE target=? LIMIT 1", (self.target,)).fetchone()
        else:
            pending = any(db.execute("SELECT 1 FROM r2_reservations WHERE target=? AND key=?",
                                     (self.target, key)).fetchone() for key in keys)
        if pending:
            raise R2ReservationConflict("R2 upload reservation is pending; reconcile it under a publisher fence")

    def _record(self, db, key: str, *, size: int, etag: str, content_type: str | None,
                content_encoding: str | None, cache_control: str | None, metadata_known: bool,
                content_sha256: str | None = None) -> None:
        if not isinstance(key, str) or not key:
            raise ValueError("R2 object key is required")
        suffix = " FOR UPDATE" if self.backend == "postgresql" else ""
        db.execute("SELECT target FROM r2_targets WHERE target=?" + suffix, (self.target,)).fetchone()
        size = _size(size)
        etag = _etag(etag)
        if content_sha256 is not None and (not isinstance(content_sha256, str)
                or re.fullmatch(r"[a-f0-9]{64}", content_sha256) is None):
            raise ValueError("Invalid decoded R2 content digest")
        previous = db.execute("SELECT bytes,etag,content_encoding,content_sha256 FROM r2_objects WHERE target=? AND key=?",
                              (self.target, key)).fetchone()
        if (content_sha256 is None and previous is not None and previous["bytes"] == size
                and previous["etag"] == etag
                and normalize_content_encoding(previous["content_encoding"]) == normalize_content_encoding(content_encoding)):
            content_sha256 = previous["content_sha256"]
        db.execute("""INSERT INTO r2_objects
            (target,key,bytes,etag,content_type,content_encoding,cache_control,metadata_known,confirmed_at,content_sha256)
            VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(target,key) DO UPDATE SET
            bytes=excluded.bytes, etag=excluded.etag, content_type=excluded.content_type,
            content_encoding=excluded.content_encoding, cache_control=excluded.cache_control,
            metadata_known=excluded.metadata_known, confirmed_at=excluded.confirmed_at, content_sha256=excluded.content_sha256""",
            (self.target, key, size, etag, content_type, content_encoding,
            cache_control, (bool(metadata_known) if self.backend == "postgresql" else int(metadata_known)),
            time.time(), content_sha256))
        db.execute("UPDATE r2_targets SET stored_bytes=stored_bytes+? WHERE target=?",
                   (size - (previous[0] if previous else 0), self.target))

    def record(self, key: str, *, size: int, etag: str, content_type: str | None = None,
               content_encoding: str | None = None, cache_control: str | None = None,
               metadata_known: bool = True, content_sha256: str | None = None) -> None:
        """Record an observed object, without implying a complete bucket inventory."""
        with self._transaction() as db:
            self._no_pending(db, [key])
            self._record(db, key, size=size, etag=etag, content_type=content_type,
                         content_encoding=content_encoding, cache_control=cache_control,
                         metadata_known=metadata_known, content_sha256=content_sha256)

    def delete(self, keys: str | Iterable[str]) -> None:
        """Forget objects only after the remote deletion has been confirmed."""
        names = [keys] if isinstance(keys, str) else list(set(keys))
        with self._transaction() as db:
            self._no_pending(db, names)
            suffix = " FOR UPDATE" if self.backend == "postgresql" else ""
            db.execute("SELECT target FROM r2_targets WHERE target=?" + suffix, (self.target,)).fetchone()
            removed = sum(row[0] for key in names
                          if (row := db.execute("SELECT bytes FROM r2_objects WHERE target=? AND key=?",
                                                (self.target, key)).fetchone()) is not None)
            db.executemany("DELETE FROM r2_objects WHERE target=? AND key=?", ((self.target, key) for key in names))
            db.execute("UPDATE r2_targets SET stored_bytes=stored_bytes-? WHERE target=?", (removed, self.target))

    def invalidate(self, key: str) -> None:
        """Discard uncertain existence and require a new complete inventory."""
        with self._transaction() as db:
            self._no_pending(db, [key])
            suffix = " FOR UPDATE" if self.backend == "postgresql" else ""
            db.execute("SELECT target FROM r2_targets WHERE target=?" + suffix, (self.target,)).fetchone()
            previous = db.execute("SELECT bytes FROM r2_objects WHERE target=? AND key=?", (self.target, key)).fetchone()
            db.execute("DELETE FROM r2_objects WHERE target=? AND key=?", (self.target, key))
            db.execute("UPDATE r2_targets SET inventory_at=NULL,stored_bytes=stored_bytes-? WHERE target=?",
                       (previous[0] if previous else 0, self.target))

    @property
    def inventory_checked_at(self) -> float | None:
        with self._lock:
            return self._db.execute("SELECT inventory_at FROM r2_targets WHERE target=?", (self.target,)).fetchone()[0]

    def inventory_is_fresh(self, max_age_seconds: float = 86400) -> bool:
        checked = self.inventory_checked_at
        return checked is not None and 0 <= time.time() - checked <= max_age_seconds

    def total_bytes(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT stored_bytes FROM r2_targets WHERE target=?", (self.target,)).fetchone()[0])

    def pending_bytes(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COALESCE(SUM(growth),0) FROM r2_reservations WHERE target=?", (self.target,)).fetchone()[0])

    def reservations(self, keys: Iterable[str] | None = None) -> list[dict]:
        """Return unresolved upload reservations for fenced reconciliation."""
        with self._lock:
            if keys is None:
                rows = self._db.execute("SELECT * FROM r2_reservations WHERE target=? ORDER BY created_at,token",
                                       (self.target,)).fetchall()
            else:
                names = list(dict.fromkeys(keys))
                if not names:
                    return []
                placeholders = ",".join("?" for _ in names)
                rows = self._db.execute(
                    f"SELECT * FROM r2_reservations WHERE target=? AND key IN ({placeholders}) ORDER BY created_at,token",
                    (self.target, *names)).fetchall()
        return [_row_mapping(row) for row in rows]

    def stats(self, *, max_age_seconds: float = 86400) -> dict:
        """Read aggregate status without materializing a full bucket snapshot."""
        with self._lock:
            row = self._db.execute("""SELECT inventory_at,stored_bytes,
                (SELECT COUNT(*) FROM r2_objects WHERE target=?) AS object_count,
                (SELECT COUNT(*) FROM r2_reservations WHERE target=?) AS pending_count,
                (SELECT COALESCE(SUM(growth),0) FROM r2_reservations WHERE target=?) AS reserved_bytes
                FROM r2_targets WHERE target=?""", (self.target,) * 4).fetchone()
        checked = row["inventory_at"]
        stored_bytes = int(row["stored_bytes"])
        reserved_bytes = int(row["reserved_bytes"])
        return {"endpoint": self.endpoint, "bucket": self.bucket, "object_count": int(row["object_count"]),
                "stored_bytes": stored_bytes, "reserved_bytes": reserved_bytes,
                "pending_count": int(row["pending_count"]), "inventory_checked_at": checked,
                "inventory_fresh": checked is not None and 0 <= time.time() - checked <= max_age_seconds,
                "max_total_bytes": self.max_total_bytes,
                "over_budget": stored_bytes + reserved_bytes > self.max_total_bytes}

    def replace_inventory(self, objects: Mapping[str, Mapping], *, checked_at: float | None = None,
                          recover_reservations: bool = False) -> None:
        """Install one complete remote snapshot, preserving verified matching metadata.

        The caller must prevent writes/deletes throughout remote enumeration and
        this transaction. Recovery additionally requires an external fence covering
        crashed or other publisher processes, not merely an old reservation age.
        """
        checked = time.time() if checked_at is None else checked_at
        if not isinstance(checked, (int, float)) or not 0 < checked <= time.time() + 1:
            raise ValueError("Invalid inventory timestamp")
        values = [(key, _size(item["bytes"]), _etag(item["etag"])) for key, item in objects.items()]
        if any(not isinstance(key, str) or not key for key, _, _ in values):
            raise ValueError("R2 object key is required")
        with self._transaction() as db:
            suffix = " FOR UPDATE" if self.backend == "postgresql" else ""
            db.execute("SELECT target FROM r2_targets WHERE target=?" + suffix, (self.target,)).fetchone()
            if checked_at is not None:
                latest = db.execute("SELECT inventory_at FROM r2_targets WHERE target=?" + suffix,
                                    (self.target,)).fetchone()[0]
                newer_object = db.execute("SELECT 1 FROM r2_objects WHERE target=? AND confirmed_at>? LIMIT 1",
                                          (self.target, checked)).fetchone()
                newer_pending = db.execute("SELECT 1 FROM r2_reservations WHERE target=? AND created_at>? LIMIT 1",
                                           (self.target, checked)).fetchone()
                if (latest is not None and latest > checked) or newer_object or newer_pending:
                    raise R2ObjectIndexError("Saved R2 inventory predates newer local observations or upload attempts")
            if recover_reservations:
                db.execute("DELETE FROM r2_reservations WHERE target=?", (self.target,))
            else:
                self._no_pending(db)
            # A temp table keeps this replacement bounded and avoids SQLite's
            # parameter limit for large inventories.
            db.execute("CREATE TEMP TABLE IF NOT EXISTS incoming_r2_objects (key TEXT PRIMARY KEY, bytes INTEGER, etag TEXT)")
            db.execute("DELETE FROM incoming_r2_objects")
            db.executemany("INSERT INTO incoming_r2_objects VALUES(?,?,?)", values)
            db.execute("""DELETE FROM r2_objects WHERE target=? AND key NOT IN
                (SELECT key FROM incoming_r2_objects)""", (self.target,))
            db.execute("""INSERT INTO r2_objects(target,key,bytes,etag,metadata_known,confirmed_at)
                SELECT ?,key,bytes,etag,FALSE,? FROM incoming_r2_objects WHERE 1
                ON CONFLICT(target,key) DO UPDATE SET
                content_type=CASE WHEN r2_objects.bytes=excluded.bytes AND r2_objects.etag=excluded.etag
                    THEN r2_objects.content_type ELSE NULL END,
                content_encoding=CASE WHEN r2_objects.bytes=excluded.bytes AND r2_objects.etag=excluded.etag
                    THEN r2_objects.content_encoding ELSE NULL END,
                cache_control=CASE WHEN r2_objects.bytes=excluded.bytes AND r2_objects.etag=excluded.etag
                    THEN r2_objects.cache_control ELSE NULL END,
                metadata_known=CASE WHEN r2_objects.bytes=excluded.bytes AND r2_objects.etag=excluded.etag
                    THEN r2_objects.metadata_known ELSE FALSE END,
                content_sha256=CASE WHEN r2_objects.bytes=excluded.bytes AND r2_objects.etag=excluded.etag
                    THEN r2_objects.content_sha256 ELSE NULL END,
                bytes=excluded.bytes, etag=excluded.etag, confirmed_at=excluded.confirmed_at""", (self.target, checked))
            db.execute("UPDATE r2_targets SET inventory_at=?,stored_bytes=? WHERE target=?",
                       (checked, sum(size for _, size, _ in values), self.target))
            db.execute("DELETE FROM incoming_r2_objects")

    def preflight(self, objects: Mapping[str, int], *, max_total_bytes: int | None = None) -> dict:
        """Reject a whole immutable candidate before it starts uploading.

        This check reserves nothing; individual PUT reservations enforce the
        limit if another publisher starts after this transaction.
        """
        budget = self.max_total_bytes if max_total_bytes is None else _size(max_total_bytes)
        values = {key: _size(size) for key, size in objects.items()}
        with self._transaction() as db:
            suffix = " FOR UPDATE" if self.backend == "postgresql" else ""
            target = db.execute("SELECT inventory_at,stored_bytes FROM r2_targets WHERE target=?" + suffix,
                                (self.target,)).fetchone()
            if target["inventory_at"] is None:
                raise R2ObjectIndexError("A complete R2 bucket inventory is required before uploads")
            known = {row[0] for row in db.execute("SELECT key FROM r2_objects WHERE target=?", (self.target,))}
            growth = sum(size for key, size in values.items() if key not in known)
            pending = db.execute("SELECT COALESCE(SUM(growth),0) FROM r2_reservations WHERE target=?", (self.target,)).fetchone()[0]
            used = target["stored_bytes"]
            if used + pending + growth > budget:
                raise R2StorageBudgetExceeded(
                    f"R2 storage budget exceeded: stored={used}, reserved={pending}, new={growth}, limit={budget}; "
                    "clean up retained objects before publishing")
            return {"stored_bytes": used, "reserved_bytes": pending, "new_bytes": growth,
                    "projected_bytes": used + pending + growth, "max_total_bytes": budget}

    def reserve(self, key: str, size: int, *, max_total_bytes: int | None = None) -> str:
        """Reserve growth before issuing a PUT; every attempt needs its own receipt."""
        size = _size(size)
        budget = self.max_total_bytes if max_total_bytes is None else _size(max_total_bytes)
        if not isinstance(key, str) or not key:
            raise ValueError("R2 object key is required")
        token = uuid.uuid4().hex
        with self._transaction() as db:
            suffix = " FOR UPDATE" if self.backend == "postgresql" else ""
            target = db.execute("SELECT inventory_at,stored_bytes FROM r2_targets WHERE target=?" + suffix,
                                (self.target,)).fetchone()
            if target["inventory_at"] is None:
                raise R2ObjectIndexError("A complete R2 bucket inventory is required before uploads")
            self._no_pending(db, [key])
            previous = db.execute("SELECT bytes FROM r2_objects WHERE target=? AND key=?", (self.target, key)).fetchone()
            growth = max(0, size - (previous[0] if previous else 0))
            used = target["stored_bytes"]
            pending = db.execute("SELECT COALESCE(SUM(growth),0) FROM r2_reservations WHERE target=?", (self.target,)).fetchone()[0]
            if used + pending + growth > budget:
                raise R2StorageBudgetExceeded(
                    f"R2 storage budget exceeded: stored={used}, reserved={pending}, new={growth}, limit={budget}; "
                    "clean up retained objects before publishing")
            db.execute("INSERT INTO r2_reservations(token,target,key,bytes,growth,created_at) VALUES(?,?,?,?,?,?)",
                       (token, self.target, key, size, growth, time.time()))
        return token

    def commit(self, token: str, *, etag: str, content_type: str | None = None,
               content_encoding: str | None = None, cache_control: str | None = None,
               content_sha256: str | None = None) -> None:
        """Confirm a successful PUT and consume its reservation atomically."""
        with self._transaction() as db:
            row = db.execute("SELECT * FROM r2_reservations WHERE target=? AND token=?", (self.target, token)).fetchone()
            if row is None:
                raise R2ReservationConflict("Unknown R2 upload reservation")
            self._record(db, row["key"], size=row["bytes"], etag=etag, content_type=content_type,
                         content_encoding=content_encoding, cache_control=cache_control,
                         metadata_known=True, content_sha256=content_sha256)
            db.execute("DELETE FROM r2_reservations WHERE token=?", (token,))

    def release(self, token: str) -> None:
        """Release only a request known not to have written remote bytes.

        A timeout is not proof of failure. Keep its reservation until a fenced
        inventory reconciliation or an exact remote metadata check resolves it.
        """
        with self._transaction() as db:
            db.execute("DELETE FROM r2_reservations WHERE target=? AND token=?", (self.target, token))

    def resolve(self, token: str, remote: dict | None) -> None:
        """Resolve an uncertain PUT from successful HEAD or confirmed 404.

        The verified observation and reservation removal are one transaction;
        another publisher cannot reserve the key between those operations.
        A failed HEAD must leave the reservation intact instead of calling here.
        """
        with self._transaction() as db:
            row = db.execute("SELECT key FROM r2_reservations WHERE target=? AND token=?", (self.target, token)).fetchone()
            if row is None:
                raise R2ReservationConflict("Unknown R2 upload reservation")
            key = row["key"]
            if remote is None:
                previous = db.execute("SELECT bytes FROM r2_objects WHERE target=? AND key=?", (self.target, key)).fetchone()
                db.execute("DELETE FROM r2_objects WHERE target=? AND key=?", (self.target, key))
                db.execute("UPDATE r2_targets SET stored_bytes=stored_bytes-? WHERE target=?",
                           (previous[0] if previous else 0, self.target))
            else:
                self._record(db, key, size=remote["size"], etag=remote["etag"],
                             content_type=remote.get("content_type"), content_encoding=remote.get("content_encoding"),
                             cache_control=remote.get("cache_control"), metadata_known=remote.get("metadata_known", True),
                             content_sha256=remote.get("content_sha256"))
            db.execute("DELETE FROM r2_reservations WHERE token=?", (token,))
