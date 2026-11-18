"""Keep only the current managed publication using durable local references.

Publication sessions serialize all channels in a bucket. Upload the staged
closure, switch and verify its pointer, commit these references, then delete
unreferenced previous objects. Unmanaged bucket objects are never collected.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import hashlib
import sqlite3
from typing import Iterable

from .r2_object_index import R2ObjectIndex, _Result


class _LedgerDB:
    """DBAPI-shaped ledger connection for the explicit SQLite test backend or PG."""
    def __init__(self, index: R2ObjectIndex):
        self.backend = index.backend
        if self.backend == 'sqlite':
            self.raw, self.engine = sqlite3.connect(index.path, timeout=30, isolation_level=None), None
            self.raw.row_factory = sqlite3.Row
        else:
            engine = index.sync_engine
            if engine is None:
                raise ValueError('PostgreSQL publication ledger requires the shared SQLAlchemy engine')
            self.raw, self.engine = engine.connect(), engine
        self._transaction = False
    def execute(self, sql, params=()):
        if self.backend == 'sqlite':
            return self.raw.execute(sql, tuple(params))
        result = _Result(self.raw.exec_driver_sql(sql.replace('?', '%s'), tuple(params)))
        if not self._transaction:
            self.raw.commit()
        return result
    def executemany(self, sql, params):
        values = list(params)
        if self.backend == 'sqlite':
            return self.raw.executemany(sql, values)
        result = self.raw.exec_driver_sql(sql.replace('?', '%s'), values)
        if not self._transaction:
            self.raw.commit()
        return result
    def begin(self):
        if self.backend == 'sqlite':
            self.raw.execute('BEGIN IMMEDIATE')
            self._transaction = True
            return None
        if self.raw.get_transaction() is not None:
            self.raw.rollback()
        self._transaction = True
        return self.raw.begin()
    def commit(self, transaction=None):
        if self.backend == 'sqlite':
            self.raw.execute('COMMIT')
        else:
            transaction.commit()
        self._transaction = False
    def rollback(self, transaction=None):
        if self.backend == 'sqlite':
            self.raw.execute('ROLLBACK')
        else:
            transaction.rollback()
        self._transaction = False
    def close(self):
        self.raw.close()


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _key(value: str) -> str:
    if (not isinstance(value, str) or not value or value.startswith('/')
            or any(part in {'', '.', '..'} for part in value.split('/'))):
        raise ValueError('Invalid managed R2 object key')
    return value


class SingleVersionPublication:
    """One channel's reference ledger; use inside client.publication_session()."""

    def __init__(self, client, *, channel: str, pointer_key: str | None = None):
        index = getattr(client, 'object_index', None)
        if not isinstance(index, R2ObjectIndex):
            raise ValueError('Single-version publication requires a durable R2 object index')
        self.client, self.index, self.target = client, index, index.target
        self.channel = _key(channel)
        self.pointer_key = _key(pointer_key or f'{channel}/current.json')
        self.db = _LedgerDB(index)
        try:
            if self.db.backend == 'sqlite':
                self.db.execute('PRAGMA busy_timeout=30000')
                self.db.execute('PRAGMA journal_mode=WAL')
                self.db.execute('PRAGMA synchronous=FULL')
            with self._transaction():
                self.db.execute('''CREATE TABLE IF NOT EXISTS r2_publication_channels (
                    target TEXT NOT NULL, channel TEXT NOT NULL, pointer_key TEXT NOT NULL,
                    active_digest TEXT, PRIMARY KEY(target,channel), UNIQUE(target,pointer_key))''')
                self.db.execute('''CREATE TABLE IF NOT EXISTS r2_publication_releases (
                    target TEXT NOT NULL, channel TEXT NOT NULL, digest TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('staged','active')),
                    PRIMARY KEY(target,channel,digest))''')
                self.db.execute('''CREATE TABLE IF NOT EXISTS r2_publication_refs (
                    target TEXT NOT NULL, channel TEXT NOT NULL, digest TEXT NOT NULL,
                    key TEXT NOT NULL, PRIMARY KEY(target,channel,digest,key))''')
                self.db.execute('''CREATE INDEX IF NOT EXISTS r2_publication_refs_key
                    ON r2_publication_refs(target,key)''')
                self.db.execute('''CREATE TABLE IF NOT EXISTS r2_publication_garbage (
                    target TEXT NOT NULL, key TEXT NOT NULL, PRIMARY KEY(target,key))''')
                existing = self.db.execute('SELECT pointer_key FROM r2_publication_channels WHERE target=? AND channel=?',
                                           (self.target, self.channel)).fetchone()
                if existing and existing['pointer_key'] != self.pointer_key:
                    raise ValueError('Managed channel pointer cannot change')
                if self.db.backend == 'sqlite':
                    self.db.execute('INSERT OR IGNORE INTO r2_publication_channels(target,channel,pointer_key) VALUES(?,?,?)',
                                    (self.target, self.channel, self.pointer_key))
                else:
                    self.db.execute('INSERT INTO r2_publication_channels(target,channel,pointer_key) VALUES(?,?,?) ON CONFLICT DO NOTHING',
                                    (self.target, self.channel, self.pointer_key))
        except BaseException:
            self.db.close()
            raise

    @contextmanager
    def _transaction(self):
        transaction = self.db.begin()
        try:
            yield
            self.db.commit(transaction)
        except BaseException:
            self.db.rollback(transaction)
            raise

    def close(self) -> None:
        self.db.close()

    def stage(self, body: bytes, keys: Iterable[str]) -> str:
        """Persist all candidate references before its first remote write."""
        digest = _digest(body)
        names = {_key(key) for key in keys}
        names.add(self.pointer_key)
        with self._transaction():
            existing = self.db.execute('SELECT 1 FROM r2_publication_releases WHERE target=? AND channel=? AND digest=?',
                                      (self.target, self.channel, digest)).fetchone()
            if existing:
                recorded = {row['key'] for row in self.db.execute(
                    'SELECT key FROM r2_publication_refs WHERE target=? AND channel=? AND digest=?',
                    (self.target, self.channel, digest))}
                if recorded != names:
                    raise ValueError('Frozen publication references changed')
            else:
                self.db.execute('INSERT INTO r2_publication_releases VALUES(?,?,?,?)',
                                (self.target, self.channel, digest, 'staged'))
                self.db.executemany('INSERT INTO r2_publication_refs VALUES(?,?,?,?)',
                                    ((self.target, self.channel, digest, key) for key in sorted(names)))
        return digest

    def commit(self, body: bytes) -> None:
        """Verify the remote pointer, then retire this channel's older closures."""
        digest = _digest(body)
        if self.client.get_object(self.pointer_key) != body:
            raise ValueError('Managed publication pointer changed before retention commit')
        with self._transaction():
            if self.db.execute('SELECT 1 FROM r2_publication_releases WHERE target=? AND channel=? AND digest=?',
                               (self.target, self.channel, digest)).fetchone() is None:
                raise ValueError('Managed publication was not staged')
            if self.db.backend == 'sqlite':
                garbage_sql = '''INSERT OR IGNORE INTO r2_publication_garbage(target,key)
                    SELECT target,key FROM r2_publication_refs WHERE target=? AND channel=? AND digest<>?'''
            else:
                garbage_sql = '''INSERT INTO r2_publication_garbage(target,key)
                    SELECT target,key FROM r2_publication_refs WHERE target=? AND channel=? AND digest<>?
                    ON CONFLICT DO NOTHING'''
            self.db.execute(garbage_sql, (self.target, self.channel, digest))
            self.db.execute('DELETE FROM r2_publication_refs WHERE target=? AND channel=? AND digest<>?',
                            (self.target, self.channel, digest))
            self.db.execute('DELETE FROM r2_publication_releases WHERE target=? AND channel=? AND digest<>?',
                            (self.target, self.channel, digest))
            self.db.execute("UPDATE r2_publication_releases SET state='active' WHERE target=? AND channel=? AND digest=?",
                            (self.target, self.channel, digest))
            self.db.execute('UPDATE r2_publication_channels SET active_digest=? WHERE target=? AND channel=?',
                            (digest, self.target, self.channel))
            # A later retirement of another channel will enqueue its own keys.
            # Keeping referenced keys in the garbage queue serves no purpose.
            self.db.execute('''DELETE FROM r2_publication_garbage WHERE target=? AND EXISTS
                (SELECT 1 FROM r2_publication_refs r WHERE r.target=r2_publication_garbage.target
                 AND r.key=r2_publication_garbage.key)''', (self.target,))

    def _assert_active_pointers(self) -> None:
        for row in self.db.execute('SELECT pointer_key,active_digest FROM r2_publication_channels WHERE target=?',
                                   (self.target,)):
            if row['active_digest'] is None:
                continue
            current = self.client.get_object(row['pointer_key'])
            if current is None or _digest(current) != row['active_digest']:
                raise ValueError('A managed current pointer differs from its local references; cleanup stopped')

    def cleanup(self, *, batch_size: int = 1000) -> dict[str, int]:
        """Delete only queued keys without current or staged cross-channel refs.

        A failed/partial DELETE leaves its queue intact for the next committed
        retry. The R2 client invalidates uncertain object receipts itself.
        """
        if type(batch_size) is not int or not 1 <= batch_size <= 1000:
            raise ValueError('Cleanup batch size must be 1–1000')
        removed = 0
        while True:
            rows = self.db.execute('''SELECT g.key FROM r2_publication_garbage g WHERE g.target=?
                AND NOT EXISTS (SELECT 1 FROM r2_publication_refs r WHERE r.target=g.target AND r.key=g.key)
                AND NOT EXISTS (SELECT 1 FROM r2_publication_channels c WHERE c.target=g.target AND c.pointer_key=g.key)
                ORDER BY g.key LIMIT ?''', (self.target, batch_size)).fetchall()
            if not rows:
                break
            self._assert_active_pointers()
            names = [row['key'] for row in rows]
            self.client.delete_objects(names)
            with self._transaction():
                self.db.executemany('DELETE FROM r2_publication_garbage WHERE target=? AND key=?',
                                    ((self.target, key) for key in names))
            removed += len(names)
        pending = self.db.execute('SELECT COUNT(*) FROM r2_publication_garbage WHERE target=?', (self.target,)).fetchone()[0]
        return {'deleted_objects': removed, 'pending_cleanup_objects': pending}


@contextmanager
def managed_publication(client, *, channel: str):
    """Use one bucket publisher lock and ledger; legacy injected clients opt out."""
    session = getattr(client, 'publication_session', None)
    with session() if callable(session) else nullcontext():
        if not isinstance(getattr(client, 'object_index', None), R2ObjectIndex):
            yield None
            return
        publication = SingleVersionPublication(client, channel=channel)
        try:
            yield publication
        finally:
            publication.close()


def seed_active(client, *, channel: str, pointer_key: str, body: bytes, keys: Iterable[str]) -> None:
    """Bootstrap verified current references during the one-time migration.

    This records ownership only; it never deletes remote objects.
    """
    session = getattr(client, 'publication_session', None)
    with session() if callable(session) else nullcontext():
        publication = SingleVersionPublication(client, channel=channel, pointer_key=pointer_key)
        try:
            publication.stage(body, keys)
            publication.commit(body)
        finally:
            publication.close()
