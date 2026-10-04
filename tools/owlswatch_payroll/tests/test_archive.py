import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from archive import Archive, restore_backup
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
        self.permission_entries = [{"type": "user"}]
        self.permission_pages = None

    def files(self):
        return self

    def permissions(self):
        return self

    def list(self, pageToken=None, **kwargs):
        def execute():
            if self.permission_pages is None:
                return {"permissions": self.permission_entries}
            index = int(pageToken or 0)
            return {"permissions": self.permission_pages[index],
                    **({"nextPageToken": str(index + 1)} if index + 1 < len(self.permission_pages) else {})}
        return Request(execute)

    def get(self, fileId, **kwargs):
        def execute():
            if fileId == "private-folder":
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
        self.config = {"archive": {"enabled": True, "backup_key_file": "backup.key", "drive_folder_id": "private-folder"}}
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
        with sqlite3.connect(destination / "payroll.sqlite3") as restored:
            self.assertEqual(restored.execute("SELECT amount FROM entries").fetchall(), [(125,)])
        self.assertEqual(destination.stat().st_mode & 0o777, 0o700)
        self.assertEqual((destination / "payroll.sqlite3").stat().st_mode & 0o777, 0o600)
        self.assertFalse((destination / "backup.key").exists())

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

    def test_uncertain_create_retries_same_reserved_drive_id(self):
        self.archive.enqueue(self.path, 7)
        drive = FakeDrive()
        drive.uncertain_once = True
        with self.assertRaises(BoundaryError):
            self.archive.retry(service=drive)
        self.assertEqual(self.archive.status(7)["counts"]["failed"], 1)
        result = self.archive.retry(service=drive)
        self.assertEqual(result["status"], "uploaded")
        self.assertEqual(drive.generated, 1)
        self.assertEqual(drive.creates, ["remote-1", "remote-1"])
        self.assertTrue(self.archive.status(7)["off_machine_current"])
        self.assertEqual(self.archive.retry(service=drive)["status"], "idle")

    def test_drive_conflict_checks_bytes_not_mutable_custom_hash_metadata(self):
        self.archive.enqueue(self.path, 7)
        drive = FakeDrive()
        drive.uncertain_once = True
        with self.assertRaises(BoundaryError):
            self.archive.retry(service=drive)
        drive.objects["remote-1"]["bytes"] = b"remote content changed while metadata stayed unchanged"
        with self.assertRaises(BoundaryError):
            self.archive.retry(service=drive)
        self.assertFalse(self.archive.status(7)["off_machine_current"])

    def test_public_drive_folder_is_rejected_before_upload(self):
        self.archive.enqueue(self.path, 7)
        drive = FakeDrive()
        drive.permission_entries = [{"type": "anyone"}]
        with self.assertRaises(BoundaryError):
            self.archive.retry(service=drive)
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
