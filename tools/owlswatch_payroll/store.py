"""Durable SQLite storage. Final snapshots and financial events are append-only."""
import contextlib
import json
import os
from pathlib import Path
import sqlite3


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
INSERT OR IGNORE INTO metadata VALUES ('schema_version', 1);
INSERT OR IGNORE INTO metadata VALUES ('state_version', 0);
CREATE TABLE IF NOT EXISTS profiles (
 id INTEGER PRIMARY KEY AUTOINCREMENT, payee_id TEXT NOT NULL,
 effective_from TEXT NOT NULL, data TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS profiles_payee ON profiles(payee_id,effective_from,id);
CREATE TABLE IF NOT EXISTS loans (
 loan_id TEXT PRIMARY KEY, payee_id TEXT NOT NULL, data TEXT NOT NULL,
 actor TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS terms (
 id INTEGER PRIMARY KEY AUTOINCREMENT, loan_id TEXT NOT NULL REFERENCES loans(loan_id),
 start_period TEXT NOT NULL, installment INTEGER NOT NULL CHECK(installment>0),
 data TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS loan_events (
 event_id TEXT PRIMARY KEY, loan_id TEXT NOT NULL REFERENCES loans(loan_id),
 kind TEXT NOT NULL, amount INTEGER NOT NULL, data TEXT NOT NULL,
 actor TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS loan_events_loan ON loan_events(loan_id);
CREATE TABLE IF NOT EXISTS runs (
 run_id TEXT PRIMARY KEY, period TEXT NOT NULL, revision INTEGER NOT NULL,
 status TEXT NOT NULL, state_version INTEGER NOT NULL, adjustments TEXT NOT NULL,
 draft TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS active_period ON runs(period) WHERE status!='superseded';
CREATE TABLE IF NOT EXISTS snapshots (
 run_id TEXT PRIMARY KEY REFERENCES runs(run_id), data TEXT NOT NULL,
 actor TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reservations (
 run_id TEXT NOT NULL REFERENCES runs(run_id), payee_id TEXT NOT NULL,
 loan_id TEXT NOT NULL REFERENCES loans(loan_id), amount INTEGER NOT NULL CHECK(amount>=0),
 state TEXT NOT NULL, PRIMARY KEY(run_id,payee_id,loan_id)
);
CREATE TABLE IF NOT EXISTS payments (
 payment_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
 payee_id TEXT NOT NULL, data TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS payment_reversals (
 reversal_id TEXT PRIMARY KEY, payment_id TEXT NOT NULL UNIQUE REFERENCES payments(payment_id),
 data TEXT NOT NULL, actor TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pending (
 token TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, preview TEXT NOT NULL,
 payload_hash TEXT NOT NULL, version INTEGER NOT NULL, actor TEXT NOT NULL,
 expires_at REAL NOT NULL, result TEXT, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_reviews (
 token TEXT PRIMARY KEY REFERENCES pending(token), payload_hash TEXT NOT NULL,
 version INTEGER NOT NULL, actor_scope TEXT NOT NULL, reviewed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS requests (
 actor_scope TEXT NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
 result TEXT NOT NULL, PRIMARY KEY(actor_scope,request_id)
);
CREATE TABLE IF NOT EXISTS audit (
 event_id TEXT PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL,
 actor TEXT NOT NULL, created_at REAL NOT NULL
);
"""


class Store:
    def __init__(self, path):
        self.path = Path(path)
        if self.path.exists() and self.path.is_symlink():
            raise ValueError("Database must not be a symbolic link.")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Never chmod an existing parent: callers may intentionally pass a shared
        # parent directory. The server provides a dedicated private state dir.
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            version = connection.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0]
            if version != 1:
                raise ValueError("Unsupported payroll database version.")
            for table in ("profiles", "loans", "terms", "loan_events", "snapshots", "payments", "payment_reversals", "audit"):
                for operation in ("UPDATE", "DELETE"):
                    connection.execute(
                        "CREATE TRIGGER IF NOT EXISTS protect_%s_%s BEFORE %s ON %s "
                        "BEGIN SELECT RAISE(ABORT, 'immutable payroll history'); END"
                        % (table, operation.lower(), operation, table)
                    )
        os.chmod(str(self.path), 0o600)

    @contextlib.contextmanager
    def connection(self):
        connection = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        try:
            yield connection
        finally:
            connection.close()

    @contextlib.contextmanager
    def transaction(self):
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def version(connection):
        return connection.execute("SELECT value FROM metadata WHERE key='state_version'").fetchone()[0]

    @staticmethod
    def bump(connection):
        connection.execute("UPDATE metadata SET value=value+1 WHERE key='state_version'")
        return Store.version(connection)
