"""Runtime-only immutable preparation records and conservative effect journal.

A kernel lock prevents concurrent workers. An effect is durably marked before
external IO; an interrupted attempt can only be reconciled, never blindly retried.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid


class StateError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message
        self.retryable = False


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.path = self.root / 'journal.sqlite3'
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS records (
                    id TEXT PRIMARY KEY, source_key TEXT UNIQUE NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS effects (
                    record_id TEXT NOT NULL, stage TEXT NOT NULL, status TEXT NOT NULL,
                    result TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(record_id, stage));
            ''')
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('PRAGMA synchronous=FULL')
        return db

    @staticmethod
    def check_id(value):
        if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value):
            raise StateError('invalid_prepared_id', 'A server-issued preparedId/sourceId is required.')
        return value

    def save_source(self, payload):
        encoded = canonical(payload)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO sources(id,digest,payload) VALUES(?,?,?)',
                       (uuid.uuid4().hex, digest, encoded))
            return db.execute('SELECT id FROM sources WHERE digest=?', (digest,)).fetchone()[0]

    def source(self, identity):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM sources WHERE id=?', (self.check_id(identity),)).fetchone()
        if not row:
            raise StateError('source_not_found', 'Read the Gmail thread again to obtain a sourceId.')
        return json.loads(row[0])

    def prepare(self, source_key, payload):
        encoded = canonical(payload)
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO records(id,source_key,payload) VALUES(?,?,?)',
                       (uuid.uuid4().hex, source_key, encoded))
            identity, existing = db.execute('SELECT id,payload FROM records WHERE source_key=?', (source_key,)).fetchone()
        if existing != encoded:
            raise StateError('source_conflict', 'This source already has a different prepared record. Human reconciliation is required; no reissue was created.')
        return identity

    def prepared(self, identity):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM records WHERE id=?', (self.check_id(identity),)).fetchone()
        if not row:
            raise StateError('prepared_not_found', 'Prepare the source again; this preparedId is not known to this workspace.')
        return json.loads(row[0])

    @contextmanager
    def claim(self, identity):
        self.check_id(identity)
        path = self.root / (identity + '.lock')
        with path.open('a') as lock:
            os.chmod(path, 0o600)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise StateError('workflow_busy', 'Another worker is processing this preparation. Wait for its result before retrying.') from exc
            try:
                yield Journal(self, identity)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


class Journal:
    def __init__(self, store, identity):
        self.store, self.identity = store, identity

    def key(self, stage):
        return 'cobros-' + self.identity + '-' + stage

    def started(self, stage):
        with self.store.connect() as db:
            return db.execute("SELECT 1 FROM effects WHERE record_id=? AND stage=?", (self.identity, stage)).fetchone() is not None

    def result(self, stage):
        with self.store.connect() as db:
            row = db.execute('SELECT status,result FROM effects WHERE record_id=? AND stage=?', (self.identity, stage)).fetchone()
        if row and row[0] == 'done':
            return json.loads(row[1])
        return None

    def save(self, stage, result, status='done'):
        with self.store.connect() as db:
            db.execute('''INSERT INTO effects(record_id,stage,status,result) VALUES(?,?,?,?)
                ON CONFLICT(record_id,stage) DO UPDATE SET status=excluded.status,
                result=excluded.result,updated_at=CURRENT_TIMESTAMP''',
                (self.identity, stage, status, canonical(result)))
        return result

    def effect(self, stage, create, reconcile=None):
        result = self.result(stage)
        if result is not None:
            return result
        with self.store.connect() as db:
            exists = db.execute('SELECT 1 FROM effects WHERE record_id=? AND stage=?', (self.identity, stage)).fetchone()
        if exists:
            # Provider searches can be eventually consistent: absence is not
            # proof of absence. Keep the journal blocked if nothing is found.
            try:
                recovered = reconcile(self.key(stage)) if reconcile else None
            except Exception as exc:
                raise StateError('outcome_unknown', f'{stage} outcome is unknown; reconciliation failed. No new external write was attempted.') from exc
            if recovered is None:
                raise StateError('outcome_unknown', f'{stage} outcome is unknown. Human reconciliation is required; do not recreate or reset this record.')
            return self.save(stage, recovered)
        self.save(stage, None, 'attempting')
        try:
            result = create(self.key(stage))
            if result is None:
                raise ValueError('Missing external result')
        except Exception as exc:
            self.save(stage, None, 'unknown')
            raise StateError('outcome_unknown', f'{stage} may have completed externally. Retry only to reconcile; no blind recreation is allowed.') from exc
        return self.save(stage, result)
