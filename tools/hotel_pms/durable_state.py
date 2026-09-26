"""Private, process-safe journals for irreversible Hotel operations.

Locks survive neither process death nor reboot; persisted claims do. Never clear
an uncertain claim automatically. These journals contain sensitive runtime data
and belong in the private workspace, outside git and backups of source files.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile


class StateError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".journal-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(data, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked_record(root, key):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = fingerprint(key)
    lock_path, record_path = root / (name + ".lock"), root / (name + ".json")
    with os.fdopen(os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600), "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise StateError("operation_in_progress", "Esta operación ya está en curso. Espera su resultado.") from exc
        try:
            try:
                record = json.loads(record_path.read_text()) if record_path.exists() else {}
                if not isinstance(record, dict):
                    raise ValueError()
            except (ValueError, OSError) as exc:
                raise StateError("journal_invalid", "El registro de recuperación necesita revisión del operador. No se repetirá la operación.") from exc
            yield record, lambda: atomic_json(record_path, record)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
