"""Offline regression coverage for OpenClaw receipt staging boundaries."""

import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


PACKAGE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("intake_media_test", PACKAGE / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class OpenClawMediaPathsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.workspace = self.base / "workspace-cuenta"
        self.state_inbound = self.base / "state" / "media" / "inbound"
        self.workspace_inbound = self.workspace / "media" / "inbound"
        self.spool = self.workspace / "spool" / "intake"
        self.state_inbound.mkdir(parents=True)
        self.workspace_inbound.mkdir(parents=True)
        patches = patch.multiple(
            server,
            WORKSPACE=self.workspace,
            MEDIA_INBOUND=self.state_inbound,
            SPOOL_INTAKE=self.spool,
        )
        patches.start()
        self.addCleanup(patches.stop)
        config = patch.object(server, "load_config", return_value={})
        config.start()
        self.addCleanup(config.stop)
        network = patch.object(server.urllib.request, "urlopen", side_effect=AssertionError("Unexpected network call"))
        network.start()
        self.addCleanup(network.stop)

    def make_file(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic receipt fixture")
        return path

    def assert_rejected(self, path, code="invalid_input"):
        with self.assertRaises(server.ToolError) as caught:
            server.tool_telegram_download_file({
                "source_message_id": "telegram-test-123",
                "openclaw_media_path": str(path),
            })
        self.assertEqual(caught.exception.code, code)
        self.assertFalse(self.spool.exists(), "A rejected source must not create spool files")

    def assert_copied(self, path):
        result = server.tool_telegram_download_file({
            "source_message_id": "telegram-test-123",
            "openclaw_media_path": str(path),
        })
        destination = self.spool / "telegram-test-123" / "original-1.jpg"
        self.assertEqual(Path(result["local_path"]), destination)
        self.assertEqual(destination.read_bytes(), path.read_bytes())
        self.assertEqual(result["bytes_written"], len(path.read_bytes()))
        self.assertEqual(result["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(result["content_type"], "image/jpeg")
        self.assertFalse(destination.with_suffix(".jpg.part").exists())

    def test_current_workspace_staging_path_copies_receipt_to_spool(self):
        self.assert_copied(self.make_file(
            self.workspace_inbound / "openclaw-staged-00000000-0000-4000-8000-000000000000" / "receipt.jpg"
        ))

    def test_legacy_state_inbound_path_still_copies_receipt_to_spool(self):
        self.assert_copied(self.make_file(self.state_inbound / "receipt.jpg"))

    def test_staged_symlink_to_legacy_inbound_file_is_allowed(self):
        source = self.make_file(self.state_inbound / "receipt.jpg")
        staged = self.workspace_inbound / "openclaw-staged-test" / "receipt.jpg"
        staged.parent.mkdir()
        staged.symlink_to(source)
        self.assert_copied(staged)

    def test_file_symlink_escaping_workspace_inbound_is_rejected(self):
        outside = self.make_file(self.workspace / "private" / "receipt.jpg")
        link = self.workspace_inbound / "receipt.jpg"
        link.symlink_to(outside)
        self.assert_rejected(link)

    def test_directory_symlink_escaping_workspace_inbound_is_rejected(self):
        outside = self.make_file(self.base / "other" / "receipt.jpg")
        link = self.workspace_inbound / "openclaw-staged-test"
        link.symlink_to(outside.parent, target_is_directory=True)
        self.assert_rejected(link / "receipt.jpg")

    def test_symlink_escaping_legacy_state_inbound_is_rejected(self):
        outside = self.make_file(self.base / "other" / "receipt.jpg")
        link = self.state_inbound / "receipt.jpg"
        link.symlink_to(outside)
        self.assert_rejected(link)

    def test_path_traversal_out_of_workspace_inbound_is_rejected(self):
        self.make_file(self.workspace / "media" / "receipt.jpg")
        self.assert_rejected(self.workspace_inbound / ".." / "receipt.jpg")

    def test_other_workspace_inbound_is_rejected(self):
        self.assert_rejected(self.make_file(
            self.base / "workspace-other" / "media" / "inbound" / "openclaw-staged-test" / "receipt.jpg"
        ))

    def test_similarly_named_inbound_directory_is_rejected(self):
        self.assert_rejected(self.make_file(self.workspace / "media" / "inbound-other" / "receipt.jpg"))

    def test_missing_staged_file_reports_not_found(self):
        self.assert_rejected(self.workspace_inbound / "openclaw-staged-test" / "missing.jpg", "not_found")

    def test_missing_legacy_file_reports_not_found(self):
        self.assert_rejected(self.state_inbound / "missing.jpg", "not_found")

    def test_inbound_directory_cannot_be_used_as_a_receipt(self):
        directory = self.workspace_inbound / "openclaw-staged-test"
        directory.mkdir()
        self.assert_rejected(directory, "not_found")


if __name__ == "__main__":
    unittest.main()
