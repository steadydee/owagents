import hashlib
import importlib.util
import json
from pathlib import Path
import secrets
import sqlite3
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("agent_recovery", ROOT / "scripts/agent-recovery.py")
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class RecoveryTests(unittest.TestCase):
    def test_encrypted_restore_verifies_live_sqlite_snapshot_and_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "fixture"
            state.mkdir()
            (state / "credential.json").write_text('{"token":"SYNTHETIC-SECRET"}')
            database = sqlite3.connect(state / "state.sqlite")
            database.execute("PRAGMA journal_mode=WAL")
            database.execute("CREATE TABLE receipts(id TEXT)")
            database.execute("INSERT INTO receipts VALUES('fixture')")
            database.commit()
            (state / "escape").symlink_to(root / "outside")
            (root / "outside").write_text("must not be included")
            clear, encrypted = root / "clear.zip", root / "snapshot.owlrec"
            manifest = recovery.archive_snapshot({"profiles/owlswatch": state}, clear, root)
            database.close()
            key = secrets.token_bytes(32)
            recovery.encrypt(clear, encrypted, key)
            self.assertNotIn(b"SYNTHETIC-SECRET", encrypted.read_bytes())
            self.assertEqual(manifest["skippedSymlinks"], 1)
            self.assertEqual(manifest["sqliteSnapshots"], 1)
            result = recovery.restore_drill(encrypted, key, root)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["verifiedFiles"], 2)
            self.assertFalse(result["runtimeStarted"])
            self.assertFalse(list(root.glob("restore-drill-*")))

    def test_wrong_key_and_tampered_archive_fail_authentication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clear, encrypted = root / "clear", root / "encrypted"
            clear.write_bytes(b"fixture")
            key = secrets.token_bytes(32)
            recovery.encrypt(clear, encrypted, key)
            with self.assertRaisesRegex(recovery.RecoveryError, "AUTHENTICATION"):
                recovery.restore_drill(encrypted, secrets.token_bytes(32), root)
            data = bytearray(encrypted.read_bytes())
            data[-17] ^= 1
            encrypted.write_bytes(data)
            with self.assertRaisesRegex(recovery.RecoveryError, "AUTHENTICATION"):
                recovery.restore_drill(encrypted, key, root)

    def test_traversal_archive_never_extracts_outside_drill(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clear, encrypted = root / "clear.zip", root / "encrypted"
            payload = b"fixture"
            with zipfile.ZipFile(clear, "w") as bundle:
                bundle.writestr("../escape", payload)
                bundle.writestr("manifest.json", json.dumps({"version": 1, "sqliteSnapshots": 0, "files": [{"path": "../escape", "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}]}))
            key = secrets.token_bytes(32)
            recovery.encrypt(clear, encrypted, key)
            with self.assertRaisesRegex(recovery.RecoveryError, "UNSAFE_ARCHIVE_PATH"):
                recovery.restore_drill(encrypted, key, root)
            self.assertFalse((root / "escape").exists())

    def test_excluded_profiles_are_never_opened(self):
        with self.assertRaisesRegex(recovery.RecoveryError, "EXCLUDED_PROFILE"):
            recovery.configured_roots(Path("/nonexistent"), ["luna"])
        with self.assertRaisesRegex(recovery.RecoveryError, "EXCLUDED_PROFILE"):
            recovery.configured_roots(Path("/nonexistent"), ["finca"])


if __name__ == "__main__":
    unittest.main()
