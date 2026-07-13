from __future__ import annotations

import importlib.util
import base64
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
SPEC = importlib.util.spec_from_file_location("finca_tasks_server", SERVER_PATH)
server = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(server)


class FincaTaskToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        server.WORKSPACE = root / "workspace"
        server.MOCK_FILE = root / "mock" / "tasks.json"
        server.SPOOL_ROOT = root / "workspace" / "spool" / "finca"
        server.ALBUM_ROOT = root / "workspace" / "spool" / "media-groups"
        server.INBOUND_MEDIA_ROOT = root / "state" / "media" / "inbound"
        server.INBOUND_MEDIA_ROOT.mkdir(parents=True)
        os.environ["FINCA_TASKS_MOCKS"] = "1"

    def tearDown(self):
        os.environ.pop("FINCA_TASKS_MOCKS", None)
        self.temp.cleanup()

    @staticmethod
    def actor(user_id="101", name="Dennis", message_id="1"):
        return {
            "telegramChatId": "-1001",
            "telegramUserId": user_id,
            "telegramMessageId": message_id,
            "telegramDisplayName": name,
        }

    def create(self, title="Reparar la puerta", key="telegram--1001-1", actor=None, **extra):
        default_message_id = key.rsplit("-", 1)[-1]
        return server.tool_create({
            "title": title,
            "idempotencyKey": key,
            "actor": actor or self.actor(message_id=default_message_id),
            **extra,
        })

    def update(self, code, action, key, **extra):
        return server.tool_update({
            "taskCode": code,
            "action": action,
            "idempotencyKey": key,
            "actor": self.actor(message_id=key.rsplit("-", 1)[-1]),
            **extra,
        })

    def test_create_is_idempotent_and_defaults_are_safe(self):
        first = self.create()
        second = self.create()
        self.assertTrue(first["ok"])
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["task"]["code"], second["task"]["code"])
        self.assertEqual(first["task"]["status"], "open")
        self.assertEqual(first["task"]["progressPercent"], 0)
        self.assertFalse(first["task"]["priority"])

    def test_telegram_metadata_overrides_conflicting_model_idempotency(self):
        actor = self.actor(message_id="77")
        first = self.create("Reparar cerca", "invented-key-a", actor=actor)
        second = self.create("Texto distinto", "invented-key-b", actor=actor)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["task"]["code"], second["task"]["code"])
        self.assertEqual(first["task"]["idempotencyKey"], "telegram--1001-77")

    def test_status_progress_completion_and_reopen(self):
        task = self.create()["task"]
        started = self.update(task["code"], "start", "telegram--1001-2")["task"]
        self.assertEqual(started["status"], "in_progress")
        self.assertEqual(started["progressPercent"], 0)
        halfway = self.update(task["code"], "progress", "telegram--1001-3", progressPercent=50)["task"]
        self.assertEqual(halfway["progressPercent"], 50)
        completed = self.update(task["code"], "complete", "telegram--1001-4")["task"]
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["progressPercent"], 100)
        with self.assertRaises(server.ToolError) as raised:
            self.update(task["code"], "progress", "telegram--1001-5", progressPercent=80)
        self.assertEqual(raised.exception.code, "reopen_required")
        reopened = self.update(task["code"], "reopen", "telegram--1001-6")["task"]
        self.assertEqual(reopened["status"], "open")
        self.assertEqual(reopened["progressPercent"], 0)

    def test_block_requires_reason_and_preserves_progress(self):
        code = self.create()["task"]["code"]
        self.update(code, "progress", "telegram--1001-2", progressPercent=35)
        blocked = self.update(code, "block", "telegram--1001-3", blockedReason="Falta cemento")["task"]
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["progressPercent"], 35)
        self.assertEqual(blocked["blockedReason"], "Falta cemento")

    def test_worker_registration_assignment_and_my_tasks(self):
        self.create("Presentarse", "telegram--1001-10", actor=self.actor("202", "Juan", "10"))
        assigned = self.create(
            "Limpiar sendero",
            "telegram--1001-11",
            assigneeName="Juan",
            actor=self.actor("101", "Dennis", "11"),
        )["task"]
        mine = server.tool_list({"telegramUserId": "202"})["tasks"]
        self.assertEqual([item["code"] for item in mine], [assigned["code"]])

    def test_daily_report_excludes_completed_and_groups_priority(self):
        priority = self.create("Arreglar cerca", "telegram--1001-20", priority=True)["task"]
        done = self.create("Guardar herramientas", "telegram--1001-21")["task"]
        self.update(done["code"], "complete", "telegram--1001-22")
        report = server.tool_daily_report({"dryRun": True})
        text = "\n".join(report["messages"])
        self.assertIn(priority["title"], text)
        self.assertNotIn(priority["code"], text)
        self.assertIn("Prioridad", text)
        self.assertNotIn(done["code"], text)

    def test_worker_report_hides_codes_from_operations_messages(self):
        message = (
            "Tareas de la finca\n\nPrioridad\n"
            "F-0042 · Reparar la puerta\nJuan · En progreso\n"
            "• F-0043 - Limpiar los vidrios · sin asignar · 0%"
        )
        cleaned = server.worker_safe_report_message(message)
        self.assertNotIn("F-0042", cleaned)
        self.assertNotIn("F-0043", cleaned)
        self.assertIn("Reparar la puerta", cleaned)
        self.assertIn("• Limpiar los vidrios", cleaned)

    def test_photo_is_spooled_before_mock_attachment(self):
        code = self.create()["task"]["code"]
        inbound = server.INBOUND_MEDIA_ROOT / "photo.jpg"
        inbound.write_bytes(b"test-photo")
        result = server.tool_attach_photos({
            "taskCode": code,
            "sourceMessageId": "30",
            "openclawMediaPaths": [str(inbound)],
            "attachmentType": "completion_photo",
            "idempotencyKeyPrefix": "telegram--1001-30-photo",
            "actor": self.actor(message_id="30"),
        })
        self.assertEqual(result["status"], "attached")
        spooled = Path(result["attachments"][0]["localPath"])
        self.assertTrue(spooled.exists())
        self.assertTrue(str(spooled).startswith(str(server.SPOOL_ROOT)))

    def test_failed_upload_keeps_pending_spool_marker(self):
        os.environ["FINCA_TASKS_MOCKS"] = "0"
        code = "F-0042"
        inbound = server.INBOUND_MEDIA_ROOT / "photo.jpg"
        inbound.write_bytes(b"test-photo")
        with mock.patch.object(server, "upload_photos", side_effect=server.ToolError("attachment_upload_failed", "offline", True)):
            with self.assertRaises(server.ToolError):
                server.tool_attach_photos({
                    "taskCode": code,
                    "sourceMessageId": "31",
                    "openclawMediaPaths": [str(inbound)],
                    "attachmentType": "progress_photo",
                    "idempotencyKeyPrefix": "telegram--1001-31-photo",
                    "actor": self.actor(message_id="31"),
                })
        marker = server.SPOOL_ROOT / code / "31" / "pending-upload.json"
        self.assertTrue(marker.exists())
        data = json.loads(marker.read_text())
        self.assertEqual(data["taskCode"], code)

    def test_album_waits_for_quiet_period_and_only_one_run_claims(self):
        code = self.create()["task"]["code"]
        first = server.INBOUND_MEDIA_ROOT / "one.jpg"
        second = server.INBOUND_MEDIA_ROOT / "two.jpg"
        first.write_bytes(b"one")
        second.write_bytes(b"two")
        results = []

        def run(path, message_id, owner):
            results.append(server.album_spool({}, {
                "taskCode": code,
                "sourceMessageId": message_id,
                "openclawMediaPaths": [str(path)],
                "mediaGroupId": "album-1",
                "claimOwner": owner,
                "quietSeconds": 5,
                "actor": self.actor(message_id=message_id),
            }, code))

        first_thread = threading.Thread(target=run, args=(first, "40", "run-a"))
        second_thread = threading.Thread(target=run, args=(second, "41", "run-b"))
        first_thread.start()
        time.sleep(0.1)
        second_thread.start()
        first_thread.join()
        second_thread.join()

        claimed = [item for item in results if item[0]]
        not_claimed = [item for item in results if not item[0]]
        self.assertEqual(len(claimed), 1)
        self.assertEqual(len(not_claimed), 1)
        self.assertEqual(len(claimed[0][1]), 2)

    def test_failed_album_upload_releases_claim_for_retry(self):
        os.environ["FINCA_TASKS_MOCKS"] = "0"
        inbound = server.INBOUND_MEDIA_ROOT / "album.jpg"
        inbound.write_bytes(b"album")
        args = {
            "taskCode": "F-0042",
            "sourceMessageId": "50",
            "openclawMediaPaths": [str(inbound)],
            "attachmentType": "progress_photo",
            "idempotencyKeyPrefix": "telegram--1001-50-photo",
            "mediaGroupId": "album-retry",
            "claimOwner": "run-retry",
            "quietSeconds": 5,
            "actor": self.actor(message_id="50"),
        }
        with mock.patch.object(server, "upload_photos", side_effect=server.ToolError("attachment_upload_failed", "offline", True)):
            with self.assertRaises(server.ToolError):
                server.tool_attach_photos(args)
        claim = server.ALBUM_ROOT / "-1001" / "album-retry" / "claimed.json"
        self.assertFalse(claim.exists())

    def test_media_path_outside_allowed_roots_is_rejected(self):
        outside = Path(self.temp.name) / "outside.jpg"
        outside.write_bytes(b"x")
        with self.assertRaises(server.ToolError) as raised:
            server.contained_media_path(str(outside))
        self.assertEqual(raised.exception.code, "invalid_input")

    def test_operations_token_is_short_lived_and_exact_tool_scoped(self):
        os.environ["OW_AGENT_TOKEN_SECRET"] = "unit-test-secret-that-is-never-committed"
        os.environ["OPERATIONS_PROPERTY_ID"] = "owlswatch-test"
        try:
            token = server.mint_operations_token({})
            encoded = token.split(".", 1)[0]
            encoded += "=" * (-len(encoded) % 4)
            payload = json.loads(base64.urlsafe_b64decode(encoded).decode())
            self.assertEqual(payload["agentId"], "finca")
            self.assertEqual(payload["activePropertyId"], "owlswatch-test")
            self.assertEqual(payload["allowedTools"], server.OPERATIONS_TOOLS)
            self.assertLessEqual(payload["exp"] - payload["iat"], 300)
            self.assertNotIn("unit-test-secret", json.dumps(payload))
        finally:
            os.environ.pop("OW_AGENT_TOKEN_SECRET", None)
            os.environ.pop("OPERATIONS_PROPERTY_ID", None)

    def test_unknown_operations_tool_is_denied_before_network(self):
        os.environ["FINCA_TASKS_MOCKS"] = "0"
        with self.assertRaises(server.ToolError) as raised:
            server.operations_tool({}, "operations.payroll.get_current_run", {})
        self.assertEqual(raised.exception.code, "tool_not_allowed")

    def test_catalog_manifest_and_profile_allowlist_match(self):
        root = SERVER_PATH.parents[2]
        catalog_names = set(server.TOOLS)
        manifest = json.loads((root / "tools" / "finca_tasks" / "openclaw.plugin.json").read_text())
        profile = json.loads((root / "openclaw" / "profiles" / "finca" / "openclaw.example.json").read_text())
        manifest_names = set(manifest["contracts"]["tools"])
        allowed = set(profile["agents"]["list"][0]["tools"]["alsoAllow"]) - {"session_status"}
        self.assertEqual(catalog_names, manifest_names)
        self.assertEqual(catalog_names, allowed)


if __name__ == "__main__":
    unittest.main()
