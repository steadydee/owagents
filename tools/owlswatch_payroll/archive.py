"""Consistent encrypted recovery copies and an idempotent private Drive outbox.

The outbox is separate from payroll commits. A failed upload can never replay a
payment. Drive IDs are persisted before create, so uncertain creates are safe to
retry against the exact same ID. Credentials/backup key never enter an archive.
"""
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import time
import zipfile

from reports import canonical, immutable_write
from security import BoundaryError, private_directory, private_file

REPORT_PATH = re.compile(r"exports/[a-f0-9]{64}/(?:payroll\.(?:json|html)|payments\.csv)")
REMOTE_ID = re.compile(r"[A-Za-z0-9_-]{1,200}")
REPORT_LINK_LIMIT = 30


def validate_database(database, expected_version=None):
    """Validate the copied ledger, never the changing source or runtime config."""
    try:
        if database.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or database.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError()
        metadata = dict(database.execute("SELECT key,value FROM metadata WHERE key IN ('schema_version','state_version')"))
        version = metadata.get("state_version")
        if type(metadata.get("schema_version")) is not int or metadata["schema_version"] != 1 or type(version) is not int or version < 0:
            raise ValueError()
        if expected_version is not None and version != expected_version:
            raise ValueError()
        return version
    except (sqlite3.DatabaseError, ValueError):
        raise BoundaryError("BACKUP_INVALID", "Backup database integrity or payroll version validation failed.") from None


def read_bundle(clear):
    with zipfile.ZipFile(io.BytesIO(clear)) as bundle:
        members = bundle.infolist()
        if len(members) != 2 or {entry.filename for entry in members} != {"payroll.sqlite3", "manifest.json"}:
            raise ValueError()
        manifest = json.loads(bundle.read("manifest.json"))
        data = bundle.read("payroll.sqlite3")
    if (not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] != 1 or manifest.get("authority") != "owlswatch-nomina"
            or type(manifest.get("state_version")) is not int or manifest["state_version"] < 0
            or hashlib.sha256(data).hexdigest() != manifest.get("database_sha256")):
        raise ValueError()
    return manifest, data


def fernet_key(root, config):
    from cryptography.fernet import Fernet
    key_path = private_file(config["archive"]["backup_key_file"], root)
    try:
        return Fernet(key_path.read_bytes().strip())
    except (ValueError, TypeError):
        raise BoundaryError("INVALID_BACKUP_KEY", "The private backup key is invalid.")


class Archive:
    def __init__(self, root, config):
        self.root, self.config = Path(root).resolve(), config
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
        report_paths = report.get("files") if isinstance(report, dict) else []
        if report is not None and (not isinstance(report, dict) or not isinstance(report_paths, list)
                or not 1 <= len(report_paths) <= 3
                or any(not isinstance(path, str) or not REPORT_PATH.fullmatch(path) for path in report_paths)
                or len(set(report_paths)) != len(report_paths)):
            raise BoundaryError("ARCHIVE_REPORT_INVALID", "Only the three private export report formats may be archived.")
        for relative in report_paths:
            private_file(relative, self.root)
        encryption = fernet_key(self.root, self.config)
        backup_dir = private_directory(self.root / "backups", self.root)
        fd, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=backup_dir)
        os.close(fd)
        try:
            source = sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
            target = sqlite3.connect(temporary)
            try:
                deadline = time.monotonic() + 10

                def progress(status, remaining, total):
                    if time.monotonic() >= deadline:
                        raise BoundaryError("BACKUP_BUSY", "Database snapshot timed out; retry backup later.", True)

                source.backup(target, pages=128, progress=progress, sleep=0.01)
                state_version = validate_database(target)
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
            try:
                immutable_write(backup_file, encryption.encrypt(payload.getvalue()))
            except BoundaryError as error:
                if error.code != "ARCHIVE_CONFLICT":
                    raise
                # Another enqueue may publish the same snapshot with a different nonce.
        backup_file = private_file(backup_file, self.root)
        try:
            existing_manifest, existing_data = read_bundle(encryption.decrypt(backup_file.read_bytes()))
            if existing_data != database_bytes or existing_manifest != manifest:
                raise ValueError()
        except Exception:
            raise BoundaryError("BACKUP_INVALID", "Previously published backup failed authentication or content validation.") from None
        paths = [str(backup_file.relative_to(self.root))] + report_paths
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO jobs(digest,state_version,created_at) VALUES(?,?,?)",
                            (job_id, state_version, time.time()))
            for relative in paths:
                path = private_file(relative, self.root)
                self.db.execute("INSERT OR IGNORE INTO files(job_id,relative_path,sha256) VALUES(?,?,?)",
                                (job_id, relative, hashlib.sha256(path.read_bytes()).hexdigest()))
        return {"status": "queued", "job_id": job_id, "state_version": state_version}

    def status(self, state_version):
        """Local-only status. Times are Unix seconds; age is None until a job completes.

        report_links contains at most 30 unique uploaded export paths, newest
        state first. Links require existing Drive access and never change sharing.
        Partially uploaded jobs may expose only their already-uploaded reports.
        """
        counts = {"queued": 0, "uploaded": 0, "failed": 0}
        for row in self.db.execute("SELECT uploaded_at,error_code FROM jobs"):
            counts["uploaded" if row["uploaded_at"] is not None else "failed" if row["error_code"] else "queued"] += 1
        latest = self.db.execute("SELECT max(state_version) FROM jobs WHERE uploaded_at IS NOT NULL").fetchone()[0]
        local = self.db.execute("SELECT max(state_version) FROM jobs").fetchone()[0]
        uploaded_at = self.db.execute("SELECT max(uploaded_at) FROM jobs").fetchone()[0]
        links, seen = [], set()
        for row in self.db.execute("""SELECT files.*,jobs.state_version FROM files
                JOIN jobs ON jobs.digest=files.job_id WHERE files.uploaded=1 AND files.remote_id IS NOT NULL
                AND files.relative_path LIKE 'exports/%'
                ORDER BY jobs.state_version DESC,jobs.created_at DESC,jobs.digest,files.relative_path"""):
            relative, remote_id = row["relative_path"], row["remote_id"]
            if relative in seen or not REPORT_PATH.fullmatch(relative) or not REMOTE_ID.fullmatch(remote_id):
                continue
            seen.add(relative)
            links.append({"job_id": row["job_id"], "state_version": row["state_version"],
                          "relative_path": relative, "file_id": remote_id,
                          "url": "https://drive.google.com/file/d/" + remote_id + "/view"})
            if len(links) > REPORT_LINK_LIMIT:
                break
        return {"enabled": self.enabled(), "counts": counts, "current_state_version": state_version,
                "latest_encrypted_state_version": local, "latest_uploaded_state_version": latest,
                "off_machine_current": latest is not None and latest >= state_version,
                "needs_backup": local is None or local < state_version,
                "last_uploaded_at": uploaded_at,
                "last_upload_age_seconds": None if uploaded_at is None else max(0, int(time.time() - uploaded_at)),
                "report_links": links[:REPORT_LINK_LIMIT], "report_links_truncated": len(links) > REPORT_LINK_LIMIT}

    def retry(self, service=None):
        """Attempt one job (one backup, up to three reports), with no SDK retries.

        Returns uploaded/job_id, idle, or busy; busy performs no network work.
        Callers own scheduling/backoff and must respect BoundaryError.retryable.
        Use one Archive instance per worker. The process lock is released on exit
        or crash, without an expiring lease that could allow overlapping uploads.
        """
        if not self.enabled():
            raise BoundaryError("ARCHIVE_NOT_CONFIGURED", "Private Drive archive is not configured.")
        lock_path = self.state / "archive-upload.lock"
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        except OSError:
            raise BoundaryError("ARCHIVE_LOCK_UNAVAILABLE", "The private archive upload lock is unavailable.") from None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
                raise BoundaryError("PRIVATE_FILE_REQUIRED", "Archive upload lock must be a private regular file.")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"status": "busy"}
            job = self.db.execute("SELECT * FROM jobs WHERE uploaded_at IS NULL ORDER BY created_at,digest LIMIT 1").fetchone()
            if not job:
                return {"status": "idle"}
            try:
                return self._upload(job, service)
            except Exception as error:
                code = error.code if isinstance(error, BoundaryError) else "DRIVE_UPLOAD_FAILED"
                http_status = getattr(getattr(error, "resp", None), "status", None)
                retryable = error.retryable if isinstance(error, BoundaryError) else (
                    http_status in (408, 429, 500, 502, 503, 504) or isinstance(error, (ConnectionError, TimeoutError)))
                with self.db:
                    self.db.execute("UPDATE jobs SET error_code=? WHERE digest=?", (code, job["digest"]))
                raise BoundaryError(code, "Archive upload did not complete. Payroll is unchanged.", retryable) from None
        finally:
            os.close(fd)

    def _upload(self, job, service):
        rows = self.db.execute("SELECT * FROM files WHERE job_id=? ORDER BY relative_path", (job["digest"],)).fetchall()
        backup_path = "backups/" + job["digest"] + ".fernet"
        if (not 1 <= len(rows) <= 4 or sum(row["relative_path"] == backup_path for row in rows) != 1
                or any(row["relative_path"] != backup_path and not REPORT_PATH.fullmatch(row["relative_path"]) for row in rows)):
            raise BoundaryError("ARCHIVE_JOB_INVALID", "Archive job must contain one backup and at most three export reports.")
        settings = self.config["archive"]
        readers = settings.get("allowed_reader_emails")
        if not isinstance(readers, list) or not readers or any(not isinstance(value, str) or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value) for value in readers):
            raise BoundaryError("ARCHIVE_READERS_REQUIRED", "Configure the exact private archive reader email allowlist.")
        allowed_readers = {value.lower() for value in readers}
        if service is None:
            from google.oauth2.service_account import Credentials as ServiceCredentials
            from google.oauth2.credentials import Credentials as UserCredentials
            from googleapiclient.discovery import build
            import google_auth_httplib2
            import httplib2
            credential_path = private_file(settings["google_credentials_file"], self.root)
            credential_type = json.loads(credential_path.read_text()).get("type")
            scopes = ["https://www.googleapis.com/auth/drive.file"]
            if credential_type == "service_account":
                credentials = ServiceCredentials.from_service_account_file(str(credential_path), scopes=scopes)
            elif credential_type == "authorized_user":
                credentials = UserCredentials.from_authorized_user_file(str(credential_path), scopes=scopes)
            else:
                raise BoundaryError("ARCHIVE_CREDENTIAL_INVALID", "Use a dedicated Google service account or owner OAuth credential.")
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
                fields="nextPageToken,permissions(type,emailAddress)", supportsAllDrives=True,
                pageSize=100, pageToken=page_token).execute(num_retries=0)
            permissions.extend(page.get("permissions", []))
            page_token = page.get("nextPageToken")
            if not page_token:
                break
        if page_token or not permissions or any(p.get("type") != "user" or str(p.get("emailAddress", "")).lower() not in allowed_readers for p in permissions):
            raise BoundaryError("ARCHIVE_FOLDER_NOT_PRIVATE", "Archive access must contain only the explicitly allowed individual readers.")
        from googleapiclient.http import MediaIoBaseUpload
        # One bounded job (at most four files); no recursive retry loop.
        for row in rows:
            if row["uploaded"]:
                continue
            path = private_file(row["relative_path"], self.root)
            local_bytes = path.read_bytes()
            if hashlib.sha256(local_bytes).hexdigest() != row["sha256"]:
                raise BoundaryError("ARCHIVE_CONTENT_CHANGED", "Private archive file no longer matches its saved digest.")
            remote_id = row["remote_id"]
            if not remote_id:
                remote_id = service.files().generateIds(count=1, space="drive", type="files").execute(num_retries=0)["ids"][0]
                if not isinstance(remote_id, str) or not REMOTE_ID.fullmatch(remote_id):
                    raise BoundaryError("ARCHIVE_REMOTE_CONFLICT", "Drive did not reserve a valid archive file ID.")
                with self.db:
                    self.db.execute("UPDATE files SET remote_id=? WHERE job_id=? AND relative_path=? AND remote_id IS NULL",
                                    (remote_id, job["digest"], row["relative_path"]))
                remote_id = self.db.execute("SELECT remote_id FROM files WHERE job_id=? AND relative_path=?",
                                            (job["digest"], row["relative_path"])).fetchone()[0]
            if not REMOTE_ID.fullmatch(remote_id):
                raise BoundaryError("ARCHIVE_REMOTE_CONFLICT", "The reserved archive file ID is invalid.")
            try:
                service.files().create(body={"id": remote_id, "name": job["digest"][:16] + "-" + path.name,
                    "parents": [settings["drive_folder_id"]], "appProperties": {"nomina_sha256": row["sha256"]}},
                    media_body=MediaIoBaseUpload(io.BytesIO(local_bytes), mimetype={
                        ".json": "application/json", ".csv": "text/csv", ".html": "text/html"
                    }.get(path.suffix, "application/octet-stream"), resumable=False),
                    fields="id", supportsAllDrives=True).execute(num_retries=0)
            except Exception as error:
                if getattr(getattr(error, "resp", None), "status", None) != 409:
                    raise
                existing = service.files().get(fileId=remote_id, fields="id,parents,trashed,appProperties,md5Checksum,size", supportsAllDrives=True).execute(num_retries=0)
                if existing.get("trashed") or settings["drive_folder_id"] not in existing.get("parents", []) or existing.get("appProperties", {}).get("nomina_sha256") != row["sha256"] or existing.get("md5Checksum") != hashlib.md5(local_bytes).hexdigest() or str(existing.get("size")) != str(len(local_bytes)):
                    raise BoundaryError("ARCHIVE_REMOTE_CONFLICT", "An existing Drive object does not match this archive.")
            with self.db:
                self.db.execute("UPDATE files SET uploaded=1 WHERE job_id=? AND relative_path=?",
                                (job["digest"], row["relative_path"]))
        with self.db:
            self.db.execute("UPDATE jobs SET uploaded_at=?,error_code=NULL WHERE digest=?", (time.time(), job["digest"]))
        return {"status": "uploaded", "job_id": job["digest"]}


def restore_backup(encrypted_path, key_path, destination):
    """Offline operator-only recovery into a NEW private directory; never overwrite."""
    from cryptography.fernet import Fernet
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise BoundaryError("RESTORE_DESTINATION_EXISTS", "Restore requires a new empty destination path.")
    key_path = Path(key_path).absolute()
    key_path = private_file(key_path.name, key_path.parent.resolve())
    try:
        clear = Fernet(key_path.read_bytes().strip()).decrypt(Path(encrypted_path).read_bytes())
        manifest, data = read_bundle(clear)
    except Exception:
        raise BoundaryError("BACKUP_INVALID", "Backup decryption or integrity validation failed.") from None
    # Validate in a private sibling before reserving the destination. Invalid
    # SQLite, manifest mismatches and publication failures leave no partial restore.
    try:
        with tempfile.TemporaryDirectory(prefix=".restore-", dir=destination.parent) as temporary:
            staged = Path(temporary)
            path = staged / "payroll.sqlite3"
            immutable_write(path, data)
            database = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
            try:
                validate_database(database, manifest["state_version"])
            finally:
                database.close()
            immutable_write(staged / "manifest.json", canonical(manifest) + b"\n")
            try:
                destination.mkdir(mode=0o700, parents=False)
            except FileExistsError:
                raise BoundaryError("RESTORE_DESTINATION_EXISTS", "Restore requires a new empty destination path.") from None
            published = []
            try:
                for name in ("payroll.sqlite3", "manifest.json"):
                    os.link(staged / name, destination / name)
                    published.append(destination / name)
                for directory in (destination, destination.parent):
                    fd = os.open(directory, os.O_RDONLY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
            except OSError:
                for path in published:
                    path.unlink()
                destination.rmdir()
                raise
    except OSError:
        raise BoundaryError("RESTORE_FAILED", "Restore could not be published to a new private directory.") from None
    return {"status": "restored", "state_version": manifest["state_version"], "destination": str(destination)}
