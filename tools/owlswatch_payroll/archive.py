"""Consistent encrypted recovery copies and an idempotent private Drive outbox.

The outbox is separate from payroll commits. A failed upload can never replay a
payment. Drive IDs are persisted before create, so uncertain creates are safe to
retry against the exact same ID. Credentials/backup key never enter an archive.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import zipfile

from reports import canonical, immutable_write
from security import BoundaryError, private_directory, private_file


def fernet_key(root, config):
    from cryptography.fernet import Fernet
    key_path = private_file(config["archive"]["backup_key_file"], root)
    try:
        return Fernet(key_path.read_bytes().strip())
    except (ValueError, TypeError):
        raise BoundaryError("INVALID_BACKUP_KEY", "The private backup key is invalid.")


class Archive:
    def __init__(self, root, config):
        self.root, self.config = Path(root), config
        self.state = private_directory(self.root / "state", self.root)
        path = self.state / "archive.sqlite3"
        if path.is_symlink():
            raise BoundaryError("UNSAFE_PATH", "Archive database must not be a symbolic link.")
        self.db = sqlite3.connect(path, timeout=10)
        os.chmod(path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            digest TEXT PRIMARY KEY, state_version INTEGER NOT NULL, created_at REAL NOT NULL,
            uploaded_at REAL, error_code TEXT);
        CREATE TABLE IF NOT EXISTS files (
            job_id TEXT NOT NULL REFERENCES jobs(digest), relative_path TEXT NOT NULL,
            sha256 TEXT NOT NULL, remote_id TEXT, uploaded INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(job_id, relative_path));
        """)
        self.db.commit()

    def enabled(self):
        return self.config.get("archive", {}).get("enabled") is True

    def close(self):
        self.db.close()

    def enqueue(self, database, state_version, report=None):
        if not self.enabled():
            return {"status": "not_configured", "message": "Private off-machine backup is not configured."}
        encryption = fernet_key(self.root, self.config)
        backup_dir = private_directory(self.root / "backups", self.root)
        fd, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=backup_dir)
        os.close(fd)
        try:
            source = sqlite3.connect(f"file:{Path(database).resolve()}?mode=ro", uri=True, timeout=10)
            target = sqlite3.connect(temporary)
            try:
                source.backup(target, pages=128, sleep=0.01)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise BoundaryError("BACKUP_INVALID", "Database backup failed integrity validation.")
                copied_version = target.execute("SELECT value FROM metadata WHERE key='state_version'").fetchone()
                if not copied_version:
                    raise BoundaryError("BACKUP_INVALID", "Backup is missing its payroll state version.")
                state_version = copied_version[0]
            finally:
                target.close()
                source.close()
            database_bytes = Path(temporary).read_bytes()
        finally:
            Path(temporary).unlink(missing_ok=True)
        db_hash = hashlib.sha256(database_bytes).hexdigest()
        manifest = {"schema_version": 1, "authority": "owlswatch-nomina", "state_version": state_version,
                    "database_sha256": db_hash, "report": report,
                    "restore_requires": "separately retained backup key and private runtime configuration"}
        job_id = hashlib.sha256(canonical(manifest)).hexdigest()
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("payroll.sqlite3", database_bytes)
            bundle.writestr("manifest.json", canonical(manifest))
        backup_file = backup_dir / (job_id + ".fernet")
        # Ciphertext has a random nonce: reuse existing authenticated bytes on retry.
        if not backup_file.exists():
            immutable_write(backup_file, encryption.encrypt(payload.getvalue()))
        elif backup_file.is_symlink():
            raise BoundaryError("UNSAFE_PATH", "Backup path must not be a symbolic link.")
        else:
            # A crash can publish ciphertext before recording the outbox row.
            # Authenticate reused bytes instead of giving corruption a new hash.
            try:
                with zipfile.ZipFile(io.BytesIO(encryption.decrypt(backup_file.read_bytes()))) as existing:
                    if existing.read("payroll.sqlite3") != database_bytes or json.loads(existing.read("manifest.json")) != manifest:
                        raise ValueError()
            except Exception:
                raise BoundaryError("BACKUP_INVALID", "Previously published backup failed authentication or content validation.")
        paths = [str(backup_file.relative_to(self.root))] + (report["files"] if report else [])
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO jobs(digest,state_version,created_at) VALUES(?,?,?)",
                            (job_id, state_version, time.time()))
            for relative in paths:
                path = private_file(relative, self.root)
                self.db.execute("INSERT OR IGNORE INTO files(job_id,relative_path,sha256) VALUES(?,?,?)",
                                (job_id, relative, hashlib.sha256(path.read_bytes()).hexdigest()))
        return {"status": "queued", "job_id": job_id, "state_version": state_version}

    def status(self, state_version):
        counts = {"queued": 0, "uploaded": 0, "failed": 0}
        for row in self.db.execute("SELECT uploaded_at,error_code FROM jobs"):
            counts["uploaded" if row["uploaded_at"] else "failed" if row["error_code"] else "queued"] += 1
        latest = self.db.execute("SELECT max(state_version) FROM jobs WHERE uploaded_at IS NOT NULL").fetchone()[0]
        local = self.db.execute("SELECT max(state_version) FROM jobs").fetchone()[0]
        return {"enabled": self.enabled(), "counts": counts, "current_state_version": state_version,
                "latest_encrypted_state_version": local, "latest_uploaded_state_version": latest,
                "off_machine_current": latest is not None and latest >= state_version,
                "needs_backup": local is None or local < state_version}

    def retry(self, service=None):
        if not self.enabled():
            raise BoundaryError("ARCHIVE_NOT_CONFIGURED", "Private Drive archive is not configured.")
        settings = self.config["archive"]
        if service is None:
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build
            import google_auth_httplib2
            import httplib2
            credentials = Credentials.from_service_account_file(
                str(private_file(settings["google_credentials_file"], self.root)),
                scopes=["https://www.googleapis.com/auth/drive.file"])
            service = build("drive", "v3", http=google_auth_httplib2.AuthorizedHttp(
                credentials, http=httplib2.Http(timeout=8)), cache_discovery=False)
        folder = service.files().get(fileId=settings["drive_folder_id"],
                                     fields="id,mimeType,trashed,capabilities(canAddChildren)",
                                     supportsAllDrives=True).execute(num_retries=0)
        if folder.get("trashed") or folder.get("mimeType") != "application/vnd.google-apps.folder" or not folder.get("capabilities", {}).get("canAddChildren"):
            raise BoundaryError("ARCHIVE_FOLDER_INVALID", "The configured archive folder is not writable.")
        # files.permissions is not populated for Shared Drive items. Inspect
        # permissions.list explicitly, including every page/inherited grant.
        permissions, page_token = [], None
        for _ in range(10):
            page = service.permissions().list(fileId=settings["drive_folder_id"],
                fields="nextPageToken,permissions(type)", supportsAllDrives=True,
                pageSize=100, pageToken=page_token).execute(num_retries=0)
            permissions.extend(page.get("permissions", []))
            page_token = page.get("nextPageToken")
            if not page_token:
                break
        if page_token or not permissions or any(p.get("type") not in ("user", "group") for p in permissions):
            raise BoundaryError("ARCHIVE_FOLDER_NOT_PRIVATE", "Archive folder privacy could not be verified. Remove public/domain access.")
        job = self.db.execute("SELECT * FROM jobs WHERE uploaded_at IS NULL ORDER BY created_at LIMIT 1").fetchone()
        if not job:
            return {"status": "idle"}
        try:
            from googleapiclient.http import MediaFileUpload
            # One bounded job (at most four files); no recursive retry loop.
            for row in self.db.execute("SELECT * FROM files WHERE job_id=? AND uploaded=0 ORDER BY relative_path", (job["digest"],)).fetchall():
                path = private_file(row["relative_path"], self.root)
                if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
                    raise BoundaryError("ARCHIVE_CONTENT_CHANGED", "Private archive file no longer matches its saved digest.")
                remote_id = row["remote_id"]
                if not remote_id:
                    remote_id = service.files().generateIds(count=1, space="drive", type="files").execute(num_retries=0)["ids"][0]
                    with self.db:
                        # Another worker may have reserved an ID while the network call ran.
                        self.db.execute("UPDATE files SET remote_id=? WHERE job_id=? AND relative_path=? AND remote_id IS NULL",
                                        (remote_id, job["digest"], row["relative_path"]))
                    remote_id = self.db.execute("SELECT remote_id FROM files WHERE job_id=? AND relative_path=?",
                                                (job["digest"], row["relative_path"])).fetchone()[0]
                try:
                    service.files().create(body={"id": remote_id, "name": job["digest"][:16] + "-" + path.name,
                        "parents": [settings["drive_folder_id"]], "appProperties": {"nomina_sha256": row["sha256"]}},
                        media_body=MediaFileUpload(str(path), resumable=False), fields="id", supportsAllDrives=True).execute(num_retries=0)
                except Exception as error:
                    if getattr(getattr(error, "resp", None), "status", None) != 409:
                        raise
                    existing = service.files().get(fileId=remote_id, fields="id,parents,trashed,appProperties,md5Checksum,size", supportsAllDrives=True).execute(num_retries=0)
                    local_bytes = path.read_bytes()
                    if existing.get("trashed") or settings["drive_folder_id"] not in existing.get("parents", []) or existing.get("appProperties", {}).get("nomina_sha256") != row["sha256"] or existing.get("md5Checksum") != hashlib.md5(local_bytes).hexdigest() or str(existing.get("size")) != str(len(local_bytes)):
                        raise BoundaryError("ARCHIVE_REMOTE_CONFLICT", "An existing Drive object does not match this archive.")
                with self.db:
                    self.db.execute("UPDATE files SET uploaded=1 WHERE job_id=? AND relative_path=?",
                                    (job["digest"], row["relative_path"]))
            with self.db:
                self.db.execute("UPDATE jobs SET uploaded_at=?,error_code=NULL WHERE digest=?", (time.time(), job["digest"]))
            return {"status": "uploaded", "job_id": job["digest"]}
        except Exception as error:
            code = error.code if isinstance(error, BoundaryError) else "DRIVE_UPLOAD_FAILED"
            with self.db:
                self.db.execute("UPDATE jobs SET error_code=? WHERE digest=?", (code, job["digest"]))
            raise BoundaryError(code, "Archive upload did not complete. Payroll is unchanged; retry the queued archive.", True)


def restore_backup(encrypted_path, key_path, destination):
    """Offline operator-only recovery into a NEW private directory; never overwrite."""
    from cryptography.fernet import Fernet
    destination = Path(destination)
    if destination.exists():
        raise BoundaryError("RESTORE_DESTINATION_EXISTS", "Restore requires a new empty destination path.")
    key_path = Path(key_path).resolve()
    if key_path.stat().st_mode & 0o077:
        raise BoundaryError("PRIVATE_FILE_REQUIRED", "Backup key must be private (mode 600).")
    try:
        clear = Fernet(key_path.read_bytes().strip()).decrypt(Path(encrypted_path).read_bytes())
        with zipfile.ZipFile(io.BytesIO(clear)) as bundle:
            if set(bundle.namelist()) != {"payroll.sqlite3", "manifest.json"}:
                raise ValueError()
            manifest = json.loads(bundle.read("manifest.json"))
            data = bundle.read("payroll.sqlite3")
        if manifest["schema_version"] != 1 or hashlib.sha256(data).hexdigest() != manifest["database_sha256"]:
            raise ValueError()
    except Exception:
        raise BoundaryError("BACKUP_INVALID", "Backup decryption or integrity validation failed.")
    destination.mkdir(mode=0o700, parents=False)
    path = destination / "payroll.sqlite3"
    immutable_write(path, data)
    database = sqlite3.connect(path)
    try:
        if database.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or database.execute("PRAGMA foreign_key_check").fetchall():
            raise BoundaryError("BACKUP_INVALID", "Restored database failed integrity validation.")
    finally:
        database.close()
    immutable_write(destination / "manifest.json", canonical(manifest) + b"\n")
    return {"status": "restored", "state_version": manifest["state_version"], "destination": str(destination)}
