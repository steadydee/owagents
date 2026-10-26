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
    def test_keyed_and_legacy_configs_back_up_each_explicit_or_inherited_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            state = home / ".openclaw-owlswatch"
            state.mkdir()
            primary = home / ".openclaw/workspace-owlswatch-main"
            specialist = home / ".openclaw/workspace-owlswatch-cotiza"
            primary.mkdir(parents=True)
            specialist.mkdir()
            (primary / "checkpoint").write_text("synthetic main state")
            (specialist / "receipt").write_text("synthetic specialist state")
            for keyed in (False, True):
                agents = {"defaults": {"workspace": "~/.openclaw/workspace-owlswatch-main"}}
                if keyed:
                    agents["entries"] = {"main": {}, "cotiza": {"workspace": str(specialist)}}
                else:
                    agents["list"] = [{"id": "main"}, {"id": "cotiza", "workspace": str(specialist)}]
                (state / "openclaw.json").write_text(json.dumps({"agents": agents}))
                roots = recovery.configured_roots(home, ["owlswatch"])
                self.assertEqual(set(roots), {"profiles/owlswatch", "workspaces/workspace-owlswatch-main", "workspaces/workspace-owlswatch-cotiza"})
                manifest = recovery.archive_snapshot(roots, home / ("keyed.zip" if keyed else "legacy.zip"), home)
                paths = {item["path"] for item in manifest["files"]}
                self.assertIn("workspaces/workspace-owlswatch-cotiza/receipt", paths)
                self.assertIn("workspaces/workspace-owlswatch-main/checkpoint", paths)

    def test_ambiguous_empty_or_spoofed_inventory_cannot_claim_backup_coverage(self):
        for section in ({"entries": {}}, {"list": []}, {"entries": {"hotel": {}}, "list": []},
                        {"entries": {"hotel": {"id": "nomina"}}},
                        {"list": [{"id": "hotel"}, {"id": "hotel"}]},
                        {"entries": {"../other": {}}}, {"entries": {"hotel": {"workspace": None}}}):
            with self.subTest(section=section), self.assertRaises(recovery.RecoveryError):
                recovery.configured_workspaces({"agents": section})

    def test_keyed_workspace_still_cannot_escape_its_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            state = home / ".openclaw-hotel"
            state.mkdir()
            for workspace in (home / ".openclaw/workspace-nomina-nomina", home / ".openclaw/workspace-hotelother", home / "outside"):
                workspace.mkdir(parents=True)
                (state / "openclaw.json").write_text(json.dumps({"agents": {"entries": {"hotel": {"workspace": str(workspace)}}}}))
                with self.assertRaisesRegex(recovery.RecoveryError, "OUTSIDE_APPROVED_PROFILE"):
                    recovery.configured_roots(home, ["hotel"])
            link = home / ".openclaw/workspace-hotel-ops"
            link.symlink_to(home / "outside", target_is_directory=True)
            (state / "openclaw.json").write_text(json.dumps({"agents": {"entries": {"hotel": {"workspace": str(link)}}}}))
            with self.assertRaisesRegex(recovery.RecoveryError, "WORKSPACE_UNAVAILABLE"):
                recovery.configured_roots(home, ["hotel"])

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
