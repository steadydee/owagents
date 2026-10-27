#!/usr/bin/env python3
"""Fleet-wide, fail-closed HTTP permits; never performs HTTP.

Installer explicitly initializes ONE canonical absolute state path in a private
directory. Reserve immediately before EACH physical attempt, without refunds.
Runtime never creates/resets missing state. An initialization marker prevents
installer reruns from recreating a lost ledger with a fresh allowance. Keep this ledger across release
rollback: restoring an old database is unsupported. No secrets/content stored.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
import uuid

PROFILES = ("owlswatch", "hotel", "bailey-finance", "nomina")
CATEGORIES = ("balance", "recovery", "owner_alert")
POLICY = "quiet-monitor-v1"
DAY = 86_400_000
LIMITS = {
    "balance_24h": ("balance", DAY, 4), "recovery_24h": ("recovery", DAY, 2),
    "diagnostic_24h": ("diagnostic", DAY, 6), "diagnostic_30d": ("diagnostic", 30 * DAY, 180),
    "owner_alert_24h": ("owner_alert", DAY, 2), "owner_alert_30d": ("owner_alert", 30 * DAY, 60),
}


class BudgetError(Exception):
    pass


def timestamp(now_ms):
    value = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    if type(value) is not int or not 0 <= value < 2**53:
        raise BudgetError("clock_invalid")
    return value


def checked_path(value, *, missing=False, writable=False):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or path.resolve() != path:
        raise BudgetError("state_path_invalid")
    parent = path.parent.stat()
    required = stat.S_IRUSR | stat.S_IXUSR | (stat.S_IWUSR if writable else 0)
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise BudgetError("state_directory_unsafe")
    if parent.st_mode & required != required:
        raise BudgetError("state_unwritable" if writable else "state_unreadable")
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing:
            return path
        raise BudgetError("state_missing") from None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
        raise BudgetError("state_file_unsafe")
    required = stat.S_IRUSR | (stat.S_IWUSR if writable else 0)
    if info.st_mode & required != required:
        raise BudgetError("state_unwritable" if writable else "state_unreadable")
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            item = sidecar.lstat()
            if not stat.S_ISREG(item.st_mode) or item.st_uid != os.getuid() or item.st_mode & 0o077 or item.st_nlink != 1:
                raise BudgetError("state_file_unsafe")
    return path


def initialization_marker(path):
    return Path(str(path) + ".initialized")


def validate_marker(path):
    marker = initialization_marker(path)
    try:
        info = marker.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > 128:
            raise BudgetError("state_marker_invalid")
        if marker.read_bytes() != (POLICY + "\n").encode():
            raise BudgetError("state_marker_invalid")
    except FileNotFoundError:
        raise BudgetError("state_marker_missing") from None


def connect(path, *, readonly=False):
    db = sqlite3.connect(path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"),
                         uri=True, timeout=2, isolation_level=None)
    db.execute("PRAGMA trusted_schema=OFF")
    if not readonly:
        db.execute("PRAGMA synchronous=FULL")
    return db


def validate(db, now):
    if db.execute("PRAGMA quick_check").fetchone() != ("ok",) or db.execute("PRAGMA user_version").fetchone() != (1,):
        raise BudgetError("state_invalid")
    if db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type IN ('view','trigger')").fetchone() != (0,):
        raise BudgetError("state_invalid")
    if db.execute("PRAGMA journal_mode").fetchone() != ("delete",):
        raise BudgetError("state_invalid")
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if tables != {"metadata", "reservations"}:
        raise BudgetError("state_invalid")
    rows = db.execute("SELECT policy,created_ms,last_seen_ms,event_count FROM metadata").fetchall()
    if len(rows) != 1:
        raise BudgetError("state_invalid")
    policy, created, seen, total = rows[0]
    if policy != POLICY or any(type(x) is not int for x in (created, seen, total)) or not 0 <= created <= seen or total < 0:
        raise BudgetError("state_invalid")
    events = db.execute("SELECT seq,category,profile,reserved_ms FROM reservations ORDER BY seq").fetchall()
    if len(events) != total:
        raise BudgetError("state_invalid")
    previous = created
    for index, (seq, category, profile, at) in enumerate(events, 1):
        if seq != index or category not in CATEGORIES or profile not in PROFILES or type(at) is not int or not previous <= at <= seen:
            raise BudgetError("state_invalid")
        previous = at
    if now < seen:
        raise BudgetError("clock_rollback")
    return total, events


def unavailable(error):
    if isinstance(error, BudgetError):
        reason = str(error)
    elif isinstance(error, sqlite3.OperationalError) and ("locked" in str(error) or "busy" in str(error)):
        reason = "state_busy"
    else:
        reason = "state_invalid"
    return {"status": "budget_unavailable", "reason": reason}


def matching(category, scope):
    return category in ("balance", "recovery") if scope == "diagnostic" else category == scope


def usage(events, now):
    used = {name: sum(matching(category, scope) and at > now - window for _, category, _, at in events)
            for name, (scope, window, cap) in LIMITS.items()}
    return {"policy": POLICY, "used": used, "caps": {name: value[2] for name, value in LIMITS.items()},
            "remaining": {name: max(0, value[2] - used[name]) for name, value in LIMITS.items()}}


def initialize(state, *, now_ms=None):
    """Installer-only; idempotent for intact existing state, never resets it."""
    db = None
    try:
        now = timestamp(now_ms)
        path = checked_path(state, missing=True, writable=True)
        marker = initialization_marker(path)
        if not path.exists() and (marker.exists() or marker.is_symlink()):
            raise BudgetError("state_missing")
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            result = inspect(state, now_ms=now)
            return {**result, "status": "initialized", "created": False} if result["status"] == "ok" else result
        os.close(fd)
        marker_fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(marker_fd, "wb") as output:
            output.write((POLICY + "\n").encode())
            output.flush()
            os.fsync(output.fileno())
        db = connect(path)
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE metadata (policy TEXT NOT NULL, created_ms INTEGER NOT NULL, last_seen_ms INTEGER NOT NULL, event_count INTEGER NOT NULL)")
        db.execute("CREATE TABLE reservations (seq INTEGER PRIMARY KEY, reservation_id TEXT UNIQUE NOT NULL, category TEXT NOT NULL, profile TEXT NOT NULL, reserved_ms INTEGER NOT NULL)")
        db.execute("INSERT INTO metadata VALUES (?,?,?,0)", (POLICY, now, now))
        db.execute("PRAGMA user_version=1")
        db.execute("COMMIT")
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return {"status": "initialized", "created": True, **usage([], now)}
    except (OSError, ValueError, sqlite3.Error, BudgetError) as error:
        return unavailable(error)
    finally:
        if db is not None:
            db.close()


def inspect(state, *, now_ms=None):
    """Read-only local snapshot; never updates state or creates missing files."""
    db = None
    try:
        path = checked_path(state)
        validate_marker(path)
        db = connect(path, readonly=True)
        db.execute("BEGIN")
        db.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()  # Establish snapshot before sampling time.
        now = timestamp(now_ms)
        _, events = validate(db, now)
        return {"status": "ok", **usage(events, now)}
    except (OSError, ValueError, sqlite3.Error, BudgetError) as error:
        return unavailable(error)
    finally:
        if db is not None:
            db.close()


def reserve(state, category, profile, *, now_ms=None):
    """One committed permit per attempt; no release/refund/reuse operation."""
    db = None
    try:
        if category not in CATEGORIES or profile not in PROFILES:
            raise BudgetError("scope_invalid")
        path = checked_path(state, writable=True)
        validate_marker(path)
        db = connect(path)
        db.execute("BEGIN IMMEDIATE")
        now = timestamp(now_ms)  # Sample after the lock, not before waiting for it.
        total, events = validate(db, now)
        reached, retry_at = [], []
        for name, (scope, window, cap) in LIMITS.items():
            if not matching(category, scope):
                continue
            times = [at for _, kind, _, at in events if matching(kind, scope) and at > now - window]
            if len(times) >= cap:
                reached.append(name)
                retry_at.append(times[-cap] + window)
        db.execute("UPDATE metadata SET last_seen_ms=?", (now,))
        if reached:
            db.execute("COMMIT")
            return {"status": "budget_exhausted", "limits": reached, "retryAt": max(retry_at) / 1000, **usage(events, now)}
        identity = str(uuid.uuid4())
        db.execute("INSERT INTO reservations VALUES (?,?,?,?,?)", (total + 1, identity, category, profile, now))
        db.execute("UPDATE metadata SET event_count=?", (total + 1,))
        db.execute("COMMIT")  # FULL-sync commit before handing out permission.
        events.append((total + 1, category, profile, now))
        return {"status": "reserved", "reservationId": identity, "reservedAt": now / 1000, **usage(events, now)}
    except (OSError, ValueError, sqlite3.Error, BudgetError) as error:
        return unavailable(error)
    finally:
        if db is not None:
            db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "reserve", "status"))
    parser.add_argument("--state", required=True)
    parser.add_argument("--category", choices=CATEGORIES)
    parser.add_argument("--profile", choices=PROFILES)
    args = parser.parse_args()
    if args.command == "reserve":
        result = reserve(args.state, args.category, args.profile)
    elif args.category or args.profile:
        result = {"status": "budget_unavailable", "reason": "scope_invalid"}
    else:
        result = (initialize if args.command == "init" else inspect)(args.state)
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
