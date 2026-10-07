from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import warnings
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from archive import Archive, REPORT_LINK_LIMIT, restore_backup
from reports import export_run, immutable_write
from security import BoundaryError

try:
    from cryptography.fernet import Fernet
    from googleapiclient.http import MediaFileUpload
    ARCHIVE_DEPS = True
except ImportError:
    ARCHIVE_DEPS = False


class Request:
    def __init__(self, handler):
        self.handler = handler

    def execute(self, num_retries=0):
        if num_retries != 0:
            raise AssertionError("Archive requests must not enable SDK retries")
        return self.handler()


class Conflict(Exception):
    resp = type("Response", (), {"status": 409})()


class FakeDrive:
    """Commit-then-disconnect exercises real retry identity without a network."""
    def __init__(self):
        self.objects = {}
        self.generated = 0
        self.creates = []
        self.uncertain_once = False
        self.permission_entries = [{"type": "user", "emailAddress": "owner@example.com"}]
        self.permission_pages = None
        self.permission_calls = 0
        self.on_create = None
        self.folder_error = None

    def files(self):
        return self

    def permissions(self):
        return self

    def list(self, pageToken=None, **kwargs):
        def execute():
            self.permission_calls += 1
            if self.permission_pages is None:
                return {"permissions": self.permission_entries}
            index = int(pageToken or 0)
            return {"permissions": self.permission_pages[index],
                    **({"nextPageToken": str(index + 1)} if index + 1 < len(self.permission_pages) else {})}
        return Request(execute)

    def get(self, fileId, **kwargs):
        def execute():
            if fileId == "private-folder":
                if self.folder_error:
                    raise self.folder_error
                return {"id": fileId, "mimeType": "application/vnd.google-apps.folder", "trashed": False,
                        "capabilities": {"canAddChildren": True}}
            obj = self.objects[fileId]
            return {**obj["body"], "trashed": False, "size": str(len(obj["bytes"])),
                    "md5Checksum": hashlib.md5(obj["bytes"]).hexdigest(),
                    "sha256Checksum": hashlib.sha256(obj["bytes"]).hexdigest()}
        return Request(execute)

    def generateIds(self, **kwargs):
        def execute():
            self.generated += 1
            return {"ids": ["remote-" + str(self.generated)]}
        return Request(execute)

    def create(self, body, media_body, **kwargs):
        def execute():
            remote_id = body["id"]
            self.creates.append(remote_id)
            if self.on_create:
                self.on_create(body)
            if remote_id in self.objects:
                raise Conflict()
            self.objects[remote_id] = {"body": body, "bytes": media_body.getbytes(0, media_body.size())}
            if self.uncertain_once:
                self.uncertain_once = False
                raise ConnectionResetError("synthetic uncertain upload")
            return {"id": remote_id}
        return Request(execute)


@unittest.skipUnless(ARCHIVE_DEPS, "Optional archive dependencies unavailable")
class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.key = self.root / "backup.key"
        self.key.write_bytes(Fernet.generate_key())
        self.key.chmod(0o600)
        self.config = {"archive": {"enabled": True, "backup_key_file": "backup.key", "drive_folder_id": "private-folder", "allowed_reader_emails": ["owner@example.com"]}}
        self.path = self.root / "payroll.sqlite3"
        self.source = sqlite3.connect(self.path)
        self.source.execute("PRAGMA journal_mode=WAL")
        self.source.executescript("CREATE TABLE metadata(key TEXT PRIMARY KEY,value INTEGER NOT NULL);"
                                 "INSERT INTO metadata VALUES('schema_version',1),('state_version',7);"
                                 "CREATE TABLE entries(id INTEGER PRIMARY KEY, amount INTEGER NOT NULL);")
        self.source.execute("INSERT INTO entries VALUES(1,125)")
        self.source.commit()
        self.path.chmod(0o600)
        self.archive = Archive(self.root, self.config)

    def tearDown(self):
        self.archive.close()
        self.source.close()
        self.temp.cleanup()

    def backup_file(self, queued):
        return self.root / "backups" / (queued["job_id"] + ".fernet")

    def report(self, revision=1):
        return export_run(self.root, {"status": "finalized", "period": "2026-10-H1", "run_id": "synthetic",
            "revision": revision, "rows": [], "totals": {"net": 0, "employees": 0, "contractors": 0, "loans": 0}})

    def repack(self, backup, manifest_changes=None, database=None, extra=None):
        encryption = Fernet(self.key.read_bytes())
        with zipfile.ZipFile(io.BytesIO(encryption.decrypt(backup.read_bytes()))) as bundle:
            manifest = json.loads(bundle.read("manifest.json"))
            data = bundle.read("payroll.sqlite3") if database is None else database
        manifest["database_sha256"] = hashlib.sha256(data).hexdigest()
        manifest.update(manifest_changes or {})
        output = io.BytesIO()
        with warnings.catch_warnings(), zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
            warnings.simplefilter("ignore", UserWarning)
            bundle.writestr("payroll.sqlite3", data)
            bundle.writestr("manifest.json", json.dumps(manifest))
            if extra:
                bundle.writestr(*extra)
        backup.write_bytes(encryption.encrypt(output.getvalue()))

    def assert_invalid_restore(self, backup, destination):
        with self.assertRaises(BoundaryError) as caught:
            restore_backup(backup, self.key, destination)
        self.assertEqual(caught.exception.code, "BACKUP_INVALID")
        self.assertFalse(caught.exception.retryable)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob(".restore-*")), [])

    def test_online_backup_contains_committed_wal_and_round_trips_without_credentials(self):
        self.assertTrue(Path(str(self.path) + "-wal").exists())
        queued = self.archive.enqueue(self.path, 7)
        backup = self.backup_file(queued)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        clear = Fernet(self.key.read_bytes()).decrypt(backup.read_bytes())
        with zipfile.ZipFile(io.BytesIO(clear)) as bundle:
            self.assertEqual(set(bundle.namelist()), {"payroll.sqlite3", "manifest.json"})
        destination = self.root / "restored"
        result = restore_backup(backup, self.key, destination)
        self.assertEqual(result["state_version"], 7)
        self.assertEqual({path.name for path in destination.iterdir()}, {"payroll.sqlite3", "manifest.json"})
        restored = sqlite3.connect(destination / "payroll.sqlite3")
        try:
            self.assertEqual(restored.execute("SELECT amount FROM entries").fetchall(), [(125,)])
        finally:
            restored.close()
        self.assertEqual(destination.stat().st_mode & 0o777, 0o700)
        self.assertEqual((destination / "payroll.sqlite3").stat().st_mode & 0o777, 0o600)
        self.assertEqual((destination / "manifest.json").stat().st_mode & 0o777, 0o600)
        self.assertFalse((destination / "backup.key").exists())

    def test_uploaded_backup_roundtrip_preserves_snapshot_despite_later_source_changes(self):
        self.source.executescript("CREATE TABLE audit(id TEXT PRIMARY KEY, event TEXT NOT NULL);"
                                 "INSERT INTO audit VALUES('event-1','synthetic payment');"
                                 "CREATE TRIGGER immutable_audit BEFORE DELETE ON audit "
                                 "BEGIN SELECT RAISE(ABORT, 'immutable'); END;")
        queued = self.archive.enqueue(self.path, 7, self.report())
        drive = FakeDrive()
        self.archive.retry(service=drive)
        remote = next(obj["bytes"] for obj in drive.objects.values() if obj["body"]["name"].endswith(".fernet"))
        downloaded = self.root / "downloaded.fernet"
        downloaded.write_bytes(remote)
        self.assertEqual(remote, self.backup_file(queued).read_bytes())
        self.source.execute("UPDATE entries SET amount=999")
        self.source.execute("UPDATE metadata SET value=8 WHERE key='state_version'")
        self.source.commit()
        self.source.execute("INSERT INTO entries VALUES(2,456)")
        destination = self.root / "remote-restore"
        restore_backup(downloaded, self.key, destination)
        restored = sqlite3.connect(destination / "payroll.sqlite3")
        try:
            self.assertEqual(restored.execute("SELECT * FROM entries").fetchall(), [(1, 125)])
            self.assertEqual(restored.execute("SELECT * FROM audit").fetchall(), [("event-1", "synthetic payment")])
            self.assertEqual(restored.execute("SELECT value FROM metadata WHERE key='state_version'").fetchone()[0], 7)
            with self.assertRaises(sqlite3.IntegrityError):
                restored.execute("DELETE FROM audit")
        finally:
            restored.close()
            self.source.rollback()
        self.assertFalse(self.archive.status(8)["off_machine_current"])

    def test_snapshot_excludes_uncommitted_wal_rows(self):
        self.source.execute("INSERT INTO entries VALUES(2,456)")
        queued = self.archive.enqueue(self.path, 7)
        destination = self.root / "restored"
        restore_backup(self.backup_file(queued), self.key, destination)
        restored = sqlite3.connect(destination / "payroll.sqlite3")
        try:
            self.assertEqual(restored.execute("SELECT * FROM entries").fetchall(), [(1, 125)])
        finally:
            restored.close()
            self.source.rollback()

    def test_backup_records_copied_database_version_not_stale_caller_version(self):
        queued = self.archive.enqueue(self.path, 6)
        self.assertEqual(queued["state_version"], 7)
        restored = restore_backup(self.backup_file(queued), self.key, self.root / "restored")
        self.assertEqual(restored["state_version"], 7)

    def test_enqueue_retries_share_job_and_ciphertext(self):
        first = self.archive.enqueue(self.path, 7)
        encrypted = self.backup_file(first).read_bytes()
        second = self.archive.enqueue(self.path, 7)
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertEqual(self.backup_file(second).read_bytes(), encrypted)
        self.assertEqual(self.archive.status(7)["counts"]["queued"], 1)

    def test_concurrent_enqueue_authenticates_winning_ciphertext(self):
        barrier = threading.Barrier(2)

        def publish(path, data):
            barrier.wait(timeout=5)
            immutable_write(path, data)

        def enqueue():
            archive = Archive(self.root, self.config)
            try:
                return archive.enqueue(self.path, 7)
            finally:
                archive.close()

        with patch("archive.immutable_write", side_effect=publish), ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(enqueue) for _ in range(2)]
            results = [future.result(timeout=10) for future in futures]
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.archive.status(7)["counts"]["queued"], 1)
        restore_backup(self.backup_file(results[0]), self.key, self.root / "restored")

    def test_backup_progress_deadline_is_retryable_and_cleans_snapshot(self):
        with patch("archive.time.monotonic", side_effect=[0, 11]), self.assertRaises(BoundaryError) as caught:
            self.archive.enqueue(self.path, 7)
        self.assertEqual(caught.exception.code, "BACKUP_BUSY")
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(list((self.root / "backups").iterdir()), [])

    def test_non_report_and_oversized_report_jobs_rejected_before_backup(self):
        valid = self.report()["files"]
        for report in ({"files": ["backup.key"]}, {"files": valid + valid}, {"files": [valid[0], valid[0]]},
                       {"files": ["exports/../backup.key"]}, {"files": []}, {}):
            with self.subTest(report=report), self.assertRaises(BoundaryError) as caught:
                self.archive.enqueue(self.path, 7, report)
            self.assertEqual(caught.exception.code, "ARCHIVE_REPORT_INVALID")
        self.assertFalse((self.root / "backups").exists())

    def test_corrupt_existing_ciphertext_is_not_accepted_as_recoverable_copy(self):
        queued = self.archive.enqueue(self.path, 7)
        self.backup_file(queued).write_bytes(b"corrupt bytes")
        with self.assertRaises(BoundaryError):
            self.archive.enqueue(self.path, 7)

    def test_tampered_backup_wrong_key_and_existing_destination_fail_closed(self):
        queued = self.archive.enqueue(self.path, 7)
        backup = self.backup_file(queued)
        wrong_key = self.root / "wrong.key"
        wrong_key.write_bytes(Fernet.generate_key())
        wrong_key.chmod(0o600)
        with self.assertRaises(BoundaryError):
            restore_backup(backup, wrong_key, self.root / "wrong-restore")
        self.assertFalse((self.root / "wrong-restore").exists())
        restore_backup(backup, self.key, self.root / "restored")
        with self.assertRaises(BoundaryError):
            restore_backup(backup, self.key, self.root / "restored")
        backup.write_bytes(backup.read_bytes()[:-2] + b"xx")
        with self.assertRaises(BoundaryError):
            restore_backup(backup, self.key, self.root / "tampered-restore")
        self.assertFalse((self.root / "tampered-restore").exists())

    def test_authenticated_damaged_database_leaves_no_restore_destination(self):
        backup = self.backup_file(self.archive.enqueue(self.path, 7))
        original = backup.read_bytes()
        for data in (b"not a database", b"", b"SQLite format 3\x00" + b"\x00" * 100):
            with self.subTest(data=data[:16]):
                backup.write_bytes(original)
                self.repack(backup, database=data)
                self.assert_invalid_restore(backup, self.root / "damaged-restore")

    def test_restore_validates_manifest_authority_versions_digest_and_exact_members(self):
        backup = self.backup_file(self.archive.enqueue(self.path, 7))
        original = backup.read_bytes()
        changes = [{"authority": "other-ledger"}, {"schema_version": True}, {"schema_version": 2},
                   {"state_version": 8}, {"state_version": True}, {"state_version": -1},
                   {"state_version": "7"}, {"database_sha256": "0" * 64}]
        for change in changes:
            with self.subTest(change=change):
                backup.write_bytes(original)
                self.repack(backup, manifest_changes=change)
                self.assert_invalid_restore(backup, self.root / "invalid-manifest")
        for name in ("manifest.json", "../unexpected", "backup.key"):
            with self.subTest(member=name):
                backup.write_bytes(original)
                self.repack(backup, extra=(name, "{}"))
                self.assert_invalid_restore(backup, self.root / "invalid-members")

    def test_enqueue_and_restore_reject_invalid_foreign_keys(self):
        backup = self.backup_file(self.archive.enqueue(self.path, 7))
        self.source.executescript("CREATE TABLE child(id INTEGER REFERENCES entries(id)); INSERT INTO child VALUES(999);")
        with self.assertRaises(BoundaryError) as caught:
            self.archive.enqueue(self.path, 7)
        self.assertEqual(caught.exception.code, "BACKUP_INVALID")
        damaged = self.root / "bad.sqlite3"
        target = sqlite3.connect(damaged)
        self.source.backup(target)
        target.close()
        self.repack(backup, database=damaged.read_bytes())
        self.assert_invalid_restore(backup, self.root / "invalid-foreign-key")

    def test_restore_rejects_unsupported_database_schema(self):
        backup = self.backup_file(self.archive.enqueue(self.path, 7))
        database = self.root / "wrong-schema.sqlite3"
        target = sqlite3.connect(database)
        self.source.backup(target)
        target.execute("UPDATE metadata SET value=2 WHERE key='schema_version'")
        target.commit()
        target.close()
        self.repack(backup, database=database.read_bytes())
        self.assert_invalid_restore(backup, self.root / "wrong-schema-restore")

    def test_restore_rejects_unsafe_key_and_dangling_destination(self):
        backup = self.backup_file(self.archive.enqueue(self.path, 7))
        destination = self.root / "restored"
        symlink = self.root / "linked.key"
        symlink.symlink_to(self.key)
        with self.assertRaises(BoundaryError) as caught:
            restore_backup(backup, symlink, destination)
        self.assertEqual(caught.exception.code, "PRIVATE_FILE_REQUIRED")
        self.key.chmod(0o644)
        with self.assertRaises(BoundaryError) as caught:
            restore_backup(backup, self.key, destination)
        self.assertEqual(caught.exception.code, "PRIVATE_FILE_REQUIRED")
        self.key.chmod(0o600)
        destination.symlink_to(self.root / "absent")
        with self.assertRaises(BoundaryError) as caught:
            restore_backup(backup, self.key, destination)
        self.assertEqual(caught.exception.code, "RESTORE_DESTINATION_EXISTS")
        self.assertTrue(destination.is_symlink())
        self.assertFalse((self.root / "absent").exists())

    def test_restore_publication_failure_removes_only_its_partial_destination(self):
        backup = self.backup_file(self.archive.enqueue(self.path, 7))
        destination = self.root / "restored"
        link = os.link

        def fail_manifest(source, target):
            if Path(target) == destination / "manifest.json":
                raise OSError("synthetic disk failure")
            link(source, target)

        with patch("archive.os.link", side_effect=fail_manifest), self.assertRaises(BoundaryError) as caught:
            restore_backup(backup, self.key, destination)
        self.assertEqual(caught.exception.code, "RESTORE_FAILED")
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob(".restore-*")), [])
        self.assertEqual(restore_backup(backup, self.key, destination)["status"], "restored")

    def test_restore_never_overwrites_destination_created_during_validation(self):
        backup = self.backup_file(self.archive.enqueue(self.path, 7))
        destination = self.root / "restored"

        def competing_destination(path, data):
            immutable_write(path, data)
            if Path(path).name == "manifest.json":
                destination.mkdir(mode=0o700)
                (destination / "keep").write_text("existing operator content")

        with patch("archive.immutable_write", side_effect=competing_destination), self.assertRaises(BoundaryError) as caught:
            restore_backup(backup, self.key, destination)
        self.assertEqual(caught.exception.code, "RESTORE_DESTINATION_EXISTS")
        self.assertEqual((destination / "keep").read_text(), "existing operator content")
        self.assertEqual(list(self.root.glob(".restore-*")), [])

    def test_uncertain_create_retries_same_reserved_drive_id(self):
        self.archive.enqueue(self.path, 7)
        drive = FakeDrive()
        drive.uncertain_once = True
        with self.assertRaises(BoundaryError) as caught:
            self.archive.retry(service=drive)
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(self.archive.status(7)["counts"]["failed"], 1)
        self.archive.close()
        self.archive = Archive(self.root, self.config)
        result = self.archive.retry(service=drive)
        self.assertEqual(result["status"], "uploaded")
        self.assertEqual(drive.generated, 1)
        self.assertEqual(drive.creates, ["remote-1", "remote-1"])
        self.assertTrue(self.archive.status(7)["off_machine_current"])
        self.assertEqual(self.archive.retry(service=drive)["status"], "idle")

    def test_idle_retry_never_loads_credentials_or_accesses_drive(self):
        self.assertEqual(self.archive.retry(), {"status": "idle"})
        self.assertEqual(self.archive.retry(service=object()), {"status": "idle"})

    def test_process_overlap_returns_busy_without_network_or_extra_ids(self):
        self.archive.enqueue(self.path, 7)
        drive = FakeDrive()

        def overlap(body):
            result = subprocess.run([sys.executable, "-B", "-c",
                "import json,sys; from archive import Archive; "
                "archive=Archive(sys.argv[1],json.loads(sys.argv[2])); "
                "print(json.dumps(archive.retry(service=object()))); archive.close()",
                str(self.root), json.dumps(self.config)], cwd=Path(__file__).resolve().parents[1],
                capture_output=True, text=True, check=True, timeout=5)
            self.assertEqual(json.loads(result.stdout), {"status": "busy"})

        drive.on_create = overlap
        self.assertEqual(self.archive.retry(service=drive)["status"], "uploaded")
        self.assertEqual(drive.creates, ["remote-1"])
        self.assertEqual(drive.generated, 1)
        self.assertEqual(self.archive.retry(service=object()), {"status": "idle"})

    def test_worker_crash_releases_upload_lock_without_a_stale_lease(self):
        self.archive.enqueue(self.path, 7)
        subprocess.run([sys.executable, "-B", "-c",
            "import json,os,sys; from archive import Archive; "
            "archive=Archive(sys.argv[1],json.loads(sys.argv[2])); "
            "archive._upload=lambda job,service: os._exit(0); archive.retry(service=object())",
            str(self.root), json.dumps(self.config)], cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, check=True, timeout=5)
        self.assertEqual(self.archive.retry(service=FakeDrive())["status"], "uploaded")

    def test_upload_lock_rejects_symlinks_and_unsafe_permissions(self):
        self.archive.enqueue(self.path, 7)
        lock = self.root / "state" / "archive-upload.lock"
        lock.symlink_to(self.key)
        original_key = self.key.read_bytes()
        with self.assertRaises(BoundaryError) as caught:
            self.archive.retry(service=object())
        self.assertEqual(caught.exception.code, "ARCHIVE_LOCK_UNAVAILABLE")
        self.assertEqual(self.key.read_bytes(), original_key)
        lock.unlink()
        lock.touch(mode=0o600)
        lock.chmod(0o644)
        with self.assertRaises(BoundaryError) as caught:
            self.archive.retry(service=object())
        self.assertEqual(caught.exception.code, "PRIVATE_FILE_REQUIRED")
        lock.chmod(0o600)
        self.assertEqual(self.archive.retry(service=FakeDrive())["status"], "uploaded")

    def test_retry_attempts_one_job_and_at_most_four_files(self):
        self.archive.enqueue(self.path, 7, self.report())
        self.archive.enqueue(self.path, 7, self.report(revision=2))
        drive = FakeDrive()
        self.archive.retry(service=drive)
        self.assertEqual(len(drive.creates), 4)
        self.assertEqual(self.archive.status(7)["counts"], {"queued": 1, "uploaded": 1, "failed": 0})
        self.archive.retry(service=drive)
        self.assertEqual(len(drive.creates), 8)

    def test_malformed_legacy_outbox_job_is_not_uploaded(self):
        queued = self.archive.enqueue(self.path, 7)
        with self.archive.db:
            self.archive.db.execute("INSERT INTO files(job_id,relative_path,sha256) VALUES(?,?,?)",
                                    (queued["job_id"], "backup.key", "0" * 64))
        with self.assertRaises(BoundaryError) as caught:
            self.archive.retry(service=object())
        self.assertEqual(caught.exception.code, "ARCHIVE_JOB_INVALID")
        self.assertFalse(caught.exception.retryable)

    def test_only_transient_upload_errors_are_retryable_and_preflight_failure_is_saved(self):
        self.archive.enqueue(self.path, 7)
        drive = FakeDrive()
        for status in (400, 401, 403, 404, 408, 429, 500, 503):
            with self.subTest(status=status):
                error = Exception("private upstream details must not escape")
                error.resp = type("Response", (), {"status": status})()
                drive.folder_error = error
                with self.assertRaises(BoundaryError) as caught:
                    self.archive.retry(service=drive)
                self.assertEqual(caught.exception.retryable, status in (408, 429, 500, 503))
                self.assertNotIn("upstream details", str(caught.exception))
                self.assertEqual(self.archive.status(7)["counts"]["failed"], 1)
        drive.folder_error = None
        self.assertEqual(self.archive.retry(service=drive)["status"], "uploaded")
        self.assertEqual(self.archive.status(7)["counts"]["failed"], 0)

    def test_upload_uses_exact_bytes_verified_before_provider_call(self):
        queued = self.archive.enqueue(self.path, 7)
        backup = self.backup_file(queued)
        expected = backup.read_bytes()
        drive = FakeDrive()
        drive.on_create = lambda body: backup.write_bytes(b"changed after validation")
        self.archive.retry(service=drive)
        self.assertEqual(drive.objects["remote-1"]["bytes"], expected)

    def test_status_upload_timestamp_age_and_empty_report_links(self):
        status = self.archive.status(7)
        self.assertIsNone(status["last_uploaded_at"])
        self.assertIsNone(status["last_upload_age_seconds"])
        self.assertEqual(status["report_links"], [])
        self.archive.enqueue(self.path, 7)
        with patch("archive.time.time", return_value=0):
            self.archive.retry(service=FakeDrive())
        with patch("archive.time.time", return_value=65.9):
            status = self.archive.status(8)
        self.assertEqual(status["last_uploaded_at"], 0)
        self.assertEqual(status["last_upload_age_seconds"], 65)
        self.assertEqual(status["counts"]["uploaded"], 1)
        self.assertTrue(status["needs_backup"])
        self.assertFalse(status["off_machine_current"])
        self.assertEqual(status["report_links"], [])
        with patch("archive.time.time", return_value=-10):
            self.assertEqual(self.archive.status(7)["last_upload_age_seconds"], 0)

    def test_status_only_links_confirmed_report_files_after_partial_upload_and_restart(self):
        report = self.report()
        self.archive.enqueue(self.path, 7, report)
        self.assertEqual(self.archive.status(7)["report_links"], [])
        drive = FakeDrive()

        def fail_html(body):
            if body["name"].endswith(".html"):
                raise ConnectionResetError("synthetic disconnect")

        drive.on_create = fail_html
        with self.assertRaises(BoundaryError):
            self.archive.retry(service=drive)
        status = self.archive.status(7)
        self.assertIsNone(status["last_uploaded_at"])
        self.assertFalse(status["off_machine_current"])
        self.assertEqual([link["relative_path"] for link in status["report_links"]],
                         [path for path in report["files"] if path.endswith(".csv")])
        self.archive.close()
        self.archive = Archive(self.root, self.config)
        drive.on_create = None
        self.archive.retry(service=drive)
        links = self.archive.status(7)["report_links"]
        self.assertEqual({link["relative_path"] for link in links}, set(report["files"]))
        for link in links:
            self.assertEqual(link["url"], "https://drive.google.com/file/d/" + link["file_id"] + "/view")
            self.assertTrue(link["relative_path"].startswith("exports/"))
            self.assertNotIn(".fernet", json.dumps(link))
            self.assertIn(link["file_id"], drive.objects)
        self.assertEqual(drive.generated, 4)
        self.assertEqual(drive.creates.count("remote-1"), 1)
        self.assertEqual(drive.creates.count("remote-2"), 1)

    def test_status_report_links_are_bounded_deduplicated_and_reject_malformed_ids(self):
        report = self.report()
        self.archive.enqueue(self.path, 7, report)
        drive = FakeDrive()
        self.archive.retry(service=drive)
        self.source.execute("UPDATE metadata SET value=8 WHERE key='state_version'")
        self.source.commit()
        queued = self.archive.enqueue(self.path, 8, report)
        self.archive.retry(service=drive)
        self.assertEqual(len(self.archive.status(8)["report_links"]), 3)
        self.assertTrue(all(link["state_version"] == 8 for link in self.archive.status(8)["report_links"]))
        with self.archive.db:
            for index in range(REPORT_LINK_LIMIT + 1):
                relative = "exports/" + format(index, "064x") + "/payroll.html"
                self.archive.db.execute("INSERT INTO files VALUES(?,?,?,?,1)",
                                        (queued["job_id"], relative, "0" * 64, "file-" + str(index)))
            self.archive.db.execute("INSERT INTO files VALUES(?,?,?,?,1)",
                                    (queued["job_id"], "exports/" + "0" * 64 + "/payments.csv", "0" * 64, "../unsafe?id"))
        status = self.archive.status(8)
        self.assertEqual(len(status["report_links"]), REPORT_LINK_LIMIT)
        self.assertTrue(status["report_links_truncated"])
        self.assertNotIn("unsafe", json.dumps(status["report_links"]))

    def test_drive_conflict_checks_bytes_not_mutable_custom_hash_metadata(self):
        self.archive.enqueue(self.path, 7)
        drive = FakeDrive()
        drive.uncertain_once = True
        with self.assertRaises(BoundaryError):
            self.archive.retry(service=drive)
        drive.objects["remote-1"]["bytes"] = b"remote content changed while metadata stayed unchanged"
        with self.assertRaises(BoundaryError) as caught:
            self.archive.retry(service=drive)
        self.assertFalse(caught.exception.retryable)
        self.assertFalse(self.archive.status(7)["off_machine_current"])

    def test_public_drive_folder_is_rejected_before_upload(self):
        self.archive.enqueue(self.path, 7)
        drive = FakeDrive()
        drive.permission_entries = [{"type": "anyone"}]
        with self.assertRaises(BoundaryError):
            self.archive.retry(service=drive)
        self.assertEqual(drive.creates, [])

    def test_staff_group_unknown_user_and_missing_permission_identity_are_denied(self):
        self.archive.enqueue(self.path, 7)
        for permission in ({"type": "group", "emailAddress": "owner@example.com"},
                           {"type": "user", "emailAddress": "staff@example.com"}, {"type": "user"}):
            drive = FakeDrive()
            drive.permission_entries = [permission]
            with self.subTest(permission=permission), self.assertRaises(BoundaryError) as caught:
                self.archive.retry(service=drive)
            self.assertEqual(caught.exception.code, "ARCHIVE_FOLDER_NOT_PRIVATE")
            self.assertEqual(drive.creates, [])

    def test_reader_allowlist_required_before_provider_access(self):
        self.archive.enqueue(self.path, 7)
        del self.config["archive"]["allowed_reader_emails"]
        drive = FakeDrive()
        with self.assertRaises(BoundaryError) as caught:
            self.archive.retry(service=drive)
        self.assertEqual(caught.exception.code, "ARCHIVE_READERS_REQUIRED")
        self.assertEqual(drive.permission_calls, 0)

    def test_dedicated_owner_oauth_uses_only_drive_file_scope(self):
        self.archive.enqueue(self.path, 7)
        path = self.root / "oauth.json"
        path.write_text('{"type":"authorized_user"}')
        path.chmod(0o600)
        self.config["archive"]["google_credentials_file"] = "oauth.json"
        with patch("google.oauth2.credentials.Credentials.from_authorized_user_file") as load, \
             patch("google_auth_httplib2.AuthorizedHttp"), \
             patch("googleapiclient.discovery.build", return_value=FakeDrive()):
            self.assertEqual(self.archive.retry()["status"], "uploaded")
        self.assertEqual(load.call_args.kwargs["scopes"], ["https://www.googleapis.com/auth/drive.file"])

    def test_privacy_pagination_is_bounded_and_fails_closed(self):
        self.archive.enqueue(self.path, 7)
        drive = FakeDrive()
        drive.permission_pages = [[{"type": "user"}]] * 11
        with self.assertRaises(BoundaryError) as caught:
            self.archive.retry(service=drive)
        self.assertEqual(caught.exception.code, "ARCHIVE_FOLDER_NOT_PRIVATE")
        self.assertEqual(drive.permission_calls, 10)
        self.assertEqual(drive.creates, [])

    def test_inherited_public_permission_on_later_page_blocks_upload(self):
        self.archive.enqueue(self.path, 7)
        drive = FakeDrive()
        drive.permission_pages = [[{"type": "user"}], [{"type": "domain"}]]
        with self.assertRaises(BoundaryError):
            self.archive.retry(service=drive)
        self.assertEqual(drive.creates, [])


class ReportTests(unittest.TestCase):
    def test_private_reports_escape_external_text_and_remain_immutable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            row = {"payee_id": "synthetic-1", "display_name": "=SUM(1,2)<script>", "kind": "employee",
                   "salary": 100, "allowance": 0, "gross": 100, "health": 0, "pension": 0,
                   "other_deduction": 0, "extra_earnings": 0, "extra_deductions": 0,
                   "loan_deductions": [], "net": 100,
                   "profile": {"payment_destination": {"institution": "Synthetic", "account": "@synthetic"}}}
            snapshot = {"status": "finalized", "period": "2026-10-H1", "run_id": "run-synthetic", "revision": 1,
                        "rows": [row], "totals": {"net": 100, "employees": 100, "contractors": 0, "loans": 0}}
            result = export_run(root, snapshot)
            self.assertEqual(export_run(root, snapshot), result)
            csv = next(root / path for path in result["files"] if path.endswith(".csv")).read_text()
            rendered = next(root / path for path in result["files"] if path.endswith(".html")).read_text()
            self.assertIn("'=SUM", csv)
            self.assertIn("'@synthetic", csv)
            self.assertNotIn("<script>", rendered)
            self.assertIn("&lt;script&gt;", rendered)
            for relative in result["files"]:
                path = root / relative
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                with self.assertRaises(BoundaryError):
                    immutable_write(path, b"overwritten")
            with self.assertRaises(BoundaryError):
                export_run(root, {**snapshot, "status": "draft"})


if __name__ == "__main__":
    unittest.main()
