#!/usr/bin/env python3
"""Operator-owned maintenance, never a model tool or a payroll scheduler."""
import argparse
from contextlib import closing
import fcntl
import hashlib
import json
import os
import shutil
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request

from archive import Archive
from security import BoundaryError, private_directory, private_file, runtime_config


def rotate_logs(profile_dir, limit=5 * 1024 * 1024):
    logs = Path(profile_dir) / "logs"
    if not logs.is_dir():
        return
    if logs.is_symlink() or logs.stat().st_mode & 0o077:
        raise BoundaryError("PRIVATE_DIRECTORY_REQUIRED", "Private log directory required.")
    for name in ("gateway.stderr.log", "maintenance.stdout.log", "maintenance.stderr.log"):
        path = logs / name
        if not path.exists():
            continue
        private_file(path, Path(profile_dir))
        if path.stat().st_size <= limit:
            continue
        for index in range(3, 0, -1):
            target = logs / f"{name}.{index}"
            if target.is_symlink():
                raise BoundaryError("PRIVATE_FILE_REQUIRED", "Log rotation target must not be a symlink.")
            if index > 1:
                source = logs / f"{name}.{index - 1}"
                if source.exists():
                    private_file(source, Path(profile_dir))
                    os.replace(source, target)
        # Keep the active inode: launchd holds its descriptor open. Bounded
        # retention is best-effort; a concurrent final line can be lost here.
        with path.open("rb") as source, (logs / f"{name}.1").open("wb") as target:
            os.chmod(target.name, 0o600)
            shutil.copyfileobj(source, target)
        with path.open("r+b") as stream:
            stream.truncate(0)


def health_probe(executable, profile_config):
    try:
        process = subprocess.run([executable, "--profile", "nomina", "channels", "status", "--json"],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "OPENCLAW_CONFIG_PATH": str(profile_config)})
        # CLI startup notices can precede the JSON object; never print that output.
        output = process.stdout
        start = output.find('{')
        data = json.loads(output[start:]) if start >= 0 else {}
        accounts = data.get("channelAccounts", {}).get("telegram", [])
        account = next((item for item in accounts if item.get("accountId") == "default"), {})
        return process.returncode == 0 and account.get("running") is True and account.get("connected") is True
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        return False


def telegram_notice(profile, config, text):
    """One fixed private route, fixed safe text, no arbitrary recipient API."""
    tg = config["telegram"]
    channel = profile.get("channels", {}).get("telegram", {})
    if len(tg["allowed_routes"]) != 1 or tg["account_id"] != "default" or channel.get("enabled") is not True:
        return "not_configured"
    if {str(value) for value in channel.get("allowFrom", [])} != set(tg["allowed_sender_ids"]):
        return "not_configured"
    token = channel.get("botToken", "")
    if not isinstance(token, str) or not token or ":" not in token:
        return "not_configured"
    route = tg["allowed_routes"][0]
    if route["chat_id"].startswith("-"):
        group = channel.get("groups", {}).get(route["chat_id"])
        if channel.get("groupPolicy") != "allowlist" or not isinstance(group, dict) or group.get("enabled") is False:
            return "not_configured"
    body = {"chat_id": route["chat_id"], "text": text, "disable_notification": True}
    if route["thread_id"]:
        body["message_thread_id"] = int(route["thread_id"])
    request = urllib.request.Request("https://api.telegram.org/bot" + token + "/sendMessage",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            result = json.load(response)
        return "sent" if result.get("ok") is True and result.get("result", {}).get("message_id") else "unverified"
    except urllib.error.HTTPError as error:
        return "retryable" if error.code == 429 else "rejected"
    except Exception:
        # sendMessage has no idempotency key. A timeout may have delivered it.
        return "unverified"


class Maintenance:
    def __init__(self, root, config, now=time.time):
        self.root, self.config, self.now = Path(root), config, now
        state = private_directory(self.root / "state", self.root)
        path = state / "maintenance.sqlite3"
        if path.is_symlink():
            raise BoundaryError("UNSAFE_PATH", "Maintenance state cannot be a symbolic link.")
        self.db = sqlite3.connect(path, timeout=5)
        os.chmod(path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS notices (id TEXT PRIMARY KEY, message TEXT NOT NULL,
            created_at REAL NOT NULL, attempted_at REAL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0);
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, json.dumps(value)))

    def notice(self, identity, text):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO notices(id,message,created_at) VALUES(?,?,?)",
                (hashlib.sha256(identity.encode()).hexdigest(), text, self.now()))

    def delivery_check(self):
        path = self.root / "state/delivery.sqlite3"
        if not path.exists():
            return 0
        private_file(path, self.root)
        db = sqlite3.connect(path, timeout=5)
        try:
            rows = db.execute("SELECT id,account_id,chat_id,thread_id,sender_id FROM deliveries WHERE status!='delivered' AND received_at<? AND alerted_at IS NULL", (self.now() - 600,)).fetchall()
            tg = self.config["telegram"]
            authorized = [row[0] for row in rows if row[1] == tg["account_id"] and row[4] in tg["allowed_sender_ids"] and
                {"chat_id": row[2], "thread_id": row[3]} in tg["allowed_routes"]]
            if authorized and self.now() - self.get("delivery_alert_at", -3600) >= 3600:
                # One safe grouped warning, not one message per failure. No
                # request is replayed: its financial outcome could be uncertain.
                self.notice("delivery:" + ",".join(sorted(authorized)),
                    "Nómina: no pude verificar la respuesta a una o más solicitudes. Pídeme el estado actual antes de repetir cambios o pagos. No repetí ninguna operación automáticamente.")
                self.set("delivery_alert_at", self.now())
                with db:
                    db.executemany("UPDATE deliveries SET alerted_at=? WHERE id=?", [(self.now(), key) for key in authorized])
            return len(authorized)
        finally:
            db.close()

    def channel_check(self, healthy):
        if healthy:
            self.set("channel_failed_since", None)
            return
        since = self.get("channel_failed_since")
        if since is None:
            self.set("channel_failed_since", self.now())
        elif self.now() - since >= 900:
            self.notice("channel:" + str(since), "Nómina necesita atención: la conexión de Telegram o el gateway lleva más de 15 minutos sin verificarse. Este control no ejecuta ni repite pagos. La recuperación del canal sigue a cargo de OpenClaw.")

    def backup(self):
        database = self.root / "state/payroll.sqlite3"
        if not database.exists():
            return {"status": "no_payroll_data"}
        private_file(database, self.root)
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
            version = db.execute("SELECT value FROM metadata WHERE key='state_version'").fetchone()[0]
        archive = Archive(self.root, self.config)
        try:
            if not archive.enabled():
                self.notice("backup_not_configured", "Nómina: falta activar la copia privada fuera del Mac mini. Los registros locales no tienen aún recuperación ante pérdida de esta máquina.")
                return {"status": "not_configured"}
            settings_hash = hashlib.sha256(json.dumps(self.config["archive"], sort_keys=True).encode()).hexdigest()
            if self.get("backup_config") != settings_hash:
                self.set("backup_retry_at", 0)
                self.set("backup_config", settings_hash)
            status = archive.status(version)
            if status["needs_backup"]:
                archive.enqueue(database, version)
            if self.now() < self.get("backup_retry_at", 0):
                return {"status": "backoff"}
            for _ in range(2):
                result = archive.retry()
                if result["status"] != "uploaded":
                    break
            self.set("backup_failures", 0)
            self.set("backup_error_since", None)
            return {"status": "current" if archive.status(version)["off_machine_current"] else "pending"}
        except BoundaryError as error:
            failures = self.get("backup_failures", 0) + 1
            self.set("backup_failures", failures)
            self.set("backup_retry_at", self.now() + (min(3600, 300 * (2 ** min(failures - 1, 4))) if error.retryable else 3600))
            since = self.get("backup_error_since")
            if since is None:
                since = self.now()
                self.set("backup_error_since", since)
            if not error.retryable or self.now() - since >= 1800:
                self.notice("backup:" + str(since), "Nómina: la copia privada fuera del Mac mini necesita atención. La nómina guardada no cambió. Se conservó la copia pendiente; no se repitió ningún pago.")
            return {"status": "failed", "code": error.code}
        finally:
            archive.close()

    def deliver_notices(self, send):
        row = self.db.execute("SELECT * FROM notices WHERE status='pending' OR (status='retryable' AND attempted_at<? AND attempts<3) ORDER BY created_at LIMIT 1", (self.now() - 3600,)).fetchone()
        if not row:
            return "idle"
        # Claim before network I/O. A crash or unknown send does not spam/replay.
        with self.db:
            self.db.execute("UPDATE notices SET status='unverified',attempted_at=?,attempts=attempts+1 WHERE id=?", (self.now(), row["id"]))
        status = send(row["message"])
        with self.db:
            self.db.execute("UPDATE notices SET status=? WHERE id=?", (status, row["id"]))
        return status


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-config", required=True)
    parser.add_argument("--openclaw", required=True)
    args = parser.parse_args()
    root, config = runtime_config()
    if not (root / "maintenance.enabled").is_file():
        print('{"status":"disabled"}')
        return
    private_file("maintenance.enabled", root)
    profile_path = Path(args.profile_config).expanduser().resolve()
    if profile_path.stat().st_mode & 0o077 or profile_path.stat().st_uid != os.getuid():
        raise BoundaryError("PRIVATE_FILE_REQUIRED", "Private profile configuration required.")
    profile = json.loads(profile_path.read_text())
    agents = profile.get("agents", {}).get("list", [])
    if len(agents) != 1 or agents[0].get("id") != "nomina" or Path(agents[0].get("workspace", "")).expanduser().resolve() != root:
        raise BoundaryError("WRONG_PROFILE", "Maintenance requires the isolated Nomina profile.")
    state = private_directory(root / "state", root)
    with os.fdopen(os.open(state / "maintenance.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "r+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        worker = Maintenance(root, config)
        try:
            rotate_logs(profile_path.parent)
            health = health_probe(args.openclaw, profile_path)
            worker.channel_check(health)
            pending = worker.delivery_check()
            backup = worker.backup()
            delivered = worker.deliver_notices(lambda text: telegram_notice(profile, config, text))
            worker.set("last_check_at", time.time())
            print(json.dumps({"healthy": health, "unverified_requests": pending, "backup": backup, "notice": delivered}))
        finally:
            worker.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print('{"status":"maintenance_failed"}')
        raise SystemExit(1) from None
