"""Synthetic scheduler integration; all provider/Telegram transports are blocked."""
import contextlib
import datetime as dt
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import recovery
import run
import server
from test_recovery import Gmail


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.now = dt.datetime(2026, 10, 27, 13, tzinfo=dt.timezone.utc)  # 08:00 Bogota
        self.sent = []
        config = {"env": {"vars": {"OWLSWATCH_EMAIL_NOTIFY_CHAT_ID": "fixture-chat",
                                      "OWLSWATCH_EMAIL_NOTIFY_THREAD_ID": "17"}}}
        patches = [patch.object(server, "WORKSPACE", self.folder),
                   patch.object(server, "TASK_DIR", self.folder / "tasks/email"),
                   patch.object(server, "NOTIFICATION_DIR", self.folder / "tasks/email_notifications"),
                   patch.object(server, "now_utc", side_effect=lambda: self.now),
                   patch.object(server, "load_config", return_value=config),
                   patch.object(server, "google_build_service", side_effect=AssertionError("unexpected Google call")),
                   patch.object(server, "http_json", side_effect=self.transport),
                   patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "fixture-no-credential",
                       "OWLSWATCH_EMAIL_NOTIFY_CHAT_ID": "fixture-chat", "OWLSWATCH_EMAIL_NOTIFY_THREAD_ID": "17",
                       "OPENCLAW_BIN": "fixture-openclaw", "OWLSWATCH_LEGACY_STAMP_DIR": str(self.folder / "legacy"),
                       "ENABLED_FILE": str(self.folder / "enabled"), "OWLSWATCH_EMAIL_AGENT_ENABLED": "0"})]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.state = recovery.root(server)

    def transport(self, url, payload, headers, **kwargs):
        self.sent.append(payload)
        return {"ok": True, "result": {"message_id": 1000 + len(self.sent), "message_thread_id": 17}}

    def call(self, mode, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return run.run(mode, **kwargs)

    def latest(self, mode):
        return recovery.read_json(self.state / f"schedule-status-{mode}.json")

    def stamp(self, mode="daily_summary"):
        return self.state / f"{mode}-{run.schedule_day(self.now)}.json"

    def digest_attempt(self, mode="daily_summary"):
        return recovery.read_json(self.state / "digest-attempts" / f"{mode}-{run.schedule_day(self.now)}.json")

    def unchanged_poll(self):
        with patch.object(recovery, "preflight", return_value={"scanId": "fixture", "candidates": []}), \
             patch.object(recovery, "finalize", return_value={"ok": True}), \
             patch.object(run.subprocess, "run", side_effect=AssertionError("unexpected model call")):
            return self.call("polling_30m", invoke=Mock(side_effect=AssertionError("unexpected model call")))

    def test_due_digest_is_local_only_and_preserves_incomplete_batch(self):
        recovery.atomic_json(self.state / "checkpoint.json", {"lastAcknowledgedAt": "2026-10-17T13:00:00Z"})
        recovery.atomic_json(self.state / "active.json", {"status": "incomplete", "candidates": [{"threadId": "fixture"}], "receipts": {}})
        before = (self.state / "active.json").read_bytes()
        self.assertEqual(self.call("daily_summary"), 0)
        self.assertEqual(len(self.sent), 1)
        text = self.sent[0]["text"]
        self.assertIn("Intake is stale", text)
        self.assertIn("Intake incomplete: 1", text)
        self.assertIn("not a verified count of currently unanswered emails", text)
        self.assertEqual((self.state / "active.json").read_bytes(), before)
        self.assertEqual(self.latest("daily_summary")["intakeStatus"], "not_requested")
        self.assertEqual(recovery.read_json(self.stamp())["notification"]["message_id"], 1001)

    def test_digest_does_not_acquire_intake_lock(self):
        original = recovery.lock
        @contextlib.contextmanager
        def lock(module, name="state"):
            self.assertNotEqual(name, "scheduled-run")
            with original(module, name):
                yield
        with patch.object(recovery, "lock", side_effect=lock):
            self.assertEqual(self.call("daily_summary"), 0)

    def test_failed_gmail_preflight_cannot_suppress_due_poll_digest(self):
        def fail(_):
            self.assertEqual(len(self.sent), 1, "digest must precede Gmail")
            raise RuntimeError("private upstream detail")
        with patch.object(recovery, "preflight", side_effect=fail):
            self.assertEqual(self.call("polling_30m"), 1)
        self.assertTrue(self.stamp().exists())
        status = self.latest("polling_30m")
        self.assertEqual(status["intakeStatus"], "failed")
        self.assertNotIn("private", json.dumps(status))

    def test_incomplete_real_intake_does_not_suppress_digest_or_clear_pending(self):
        gmail = Gmail()
        with patch.object(server, "google_build_service", return_value=gmail):
            active = recovery.preflight(server)
            self.assertEqual(self.call("polling_30m", invoke=Mock(return_value=Mock(returncode=0))), 1)
        self.assertEqual(len(self.sent), 1)
        retained = recovery.read_json(self.state / "active.json")
        self.assertEqual(retained["scanId"], active["scanId"])
        self.assertEqual(retained["status"], "incomplete")
        self.assertEqual(retained["receipts"], {})

    def test_unchanged_poll_starts_no_model_and_only_retries_due_digest(self):
        self.assertEqual(self.unchanged_poll(), 0)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.unchanged_poll(), 0)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.latest("polling_30m")["intakeStatus"], "unchanged")

    def test_explicit_rejection_retries_on_poll_after_thirty_minutes(self):
        cause = urllib.error.HTTPError("https://example.test", 429, "fixture", {}, None)
        error = server.ToolError("http_error", "fixture", retryable=False)
        error.__cause__ = cause
        with patch.object(server, "http_json", side_effect=error) as send:
            self.assertEqual(self.call("daily_summary"), 1)
            self.now += dt.timedelta(minutes=10)
            self.assertEqual(self.unchanged_poll(), 1)
            self.assertEqual(send.call_count, 1)
        self.now += dt.timedelta(minutes=20)
        self.assertEqual(self.unchanged_poll(), 0)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.digest_attempt()["attempts"], 2)
        self.assertTrue(self.stamp().exists())

    def test_known_failed_attempts_are_bounded_per_day(self):
        with patch.object(server, "telegram_token", side_effect=server.ToolError("config_missing", "fixture")):
            for _ in range(run.DIGEST_MAX_ATTEMPTS + 2):
                self.call("daily_summary")
                self.now += dt.timedelta(minutes=30)
        self.assertEqual(self.digest_attempt()["attempts"], run.DIGEST_MAX_ATTEMPTS)
        self.assertFalse(self.sent)
        self.assertFalse(self.stamp().exists())
        self.assertEqual(self.latest("daily_summary")["digests"]["daily_summary"]["status"], "attempt_limit")

    def test_lost_response_is_not_retried_and_does_not_starve_other_digest(self):
        def lost(*args, **kwargs):
            self.transport(*args, **kwargs)
            raise TimeoutError("response lost after delivery")
        with patch.object(server, "http_json", side_effect=lost):
            self.assertEqual(self.call("daily_summary"), 1)
        self.assertEqual(self.digest_attempt()["status"], "outcome_unknown")
        self.now += dt.timedelta(minutes=30)
        self.assertEqual(self.unchanged_poll(), 1)  # unresolved daily evidence remains visible
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(self.sent[1]["text"].startswith("Email review follow-up"))
        self.assertFalse(self.stamp().exists())
        self.assertTrue(self.stamp("unanswered_7d").exists())
        self.call("daily_summary", force=True)
        self.assertEqual(len(self.sent), 2)

    def test_lost_scheduler_stamp_reconciles_tool_receipt_without_second_send(self):
        original = recovery.atomic_json
        def fail_stamp(path, value):
            if path == self.stamp():
                raise OSError("fixture failure after acknowledged Telegram send")
            return original(path, value)
        with patch.object(recovery, "atomic_json", side_effect=fail_stamp):
            self.assertEqual(self.call("daily_summary"), 1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.digest_attempt()["status"], "outcome_unknown")
        self.assertEqual(self.call("daily_summary"), 0)
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(recovery.read_json(self.stamp())["notification"]["suppressed"])

    def test_suppression_without_matching_positive_receipt_is_not_delivery(self):
        with patch.object(server, "tool_email_send_telegram_message", return_value={"ok": True, "suppressed": True}):
            self.assertEqual(self.call("daily_summary"), 1)
        self.assertFalse(self.stamp().exists())
        self.assertEqual(self.digest_attempt()["status"], "outcome_unknown")

    def assert_topic_mismatch_stays_unknown(self, returned_topic):
        def transport(*args, **kwargs):
            result = self.transport(*args, **kwargs)
            if returned_topic is None:
                result["result"].pop("message_thread_id")
            else:
                result["result"]["message_thread_id"] = returned_topic
            return result
        with patch.object(server, "http_json", side_effect=transport):
            self.assertEqual(self.call("daily_summary"), 1)
            self.assertEqual(self.call("daily_summary", force=True), 1)
        self.assertFalse(self.stamp().exists())
        self.assertEqual(self.digest_attempt()["status"], "outcome_unknown")
        self.assertEqual(len(self.sent), 1, "an uncertain topic receipt must never trigger a second send")

    def test_missing_topic_receipt_is_unknown_and_not_replayed(self):
        self.assert_topic_mismatch_stays_unknown(None)

    def test_wrong_topic_receipt_is_unknown_and_not_replayed(self):
        self.assert_topic_mismatch_stays_unknown(18)

    def test_direct_send_id_must_match_its_durable_receipt(self):
        original_send = server.tool_email_send_telegram_message
        def wrong_result(*args, **kwargs):
            result = original_send(*args, **kwargs)
            result["message_id"] += 1
            return result
        with patch.object(server, "tool_email_send_telegram_message", side_effect=wrong_result):
            self.assertEqual(self.call("daily_summary"), 1)
        self.assertFalse(self.stamp().exists())
        self.assertEqual(self.digest_attempt()["status"], "outcome_unknown")
        self.assertEqual(len(self.sent), 1)
        # The next run may reconcile the actual durable receipt, but cannot send again.
        self.assertEqual(self.call("daily_summary"), 0)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(recovery.read_json(self.stamp())["notification"]["message_id"], 1001)

    def test_startup_sends_one_due_mode_per_poll_and_never_replays_old_days(self):
        self.now += dt.timedelta(hours=2)
        self.assertEqual(self.unchanged_poll(), 0)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.unchanged_poll(), 0)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.unchanged_poll(), 0)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual({path.name for path in self.state.glob('*-2026-*.json')},
                         {"daily_summary-2026-10-27.json", "unanswered_7d-2026-10-27.json"})
        self.call("daily_summary", force=True)
        self.assertEqual(len(self.sent), 2)

    def test_parallel_digest_jobs_have_one_transport_delivery(self):
        def slow(*args, **kwargs):
            time.sleep(0.03)
            return self.transport(*args, **kwargs)
        with patch.object(server, "http_json", side_effect=slow), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: run.due_digests("daily_summary", self.state, self.now), range(2)))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual({item["daily_summary"]["status"] for item in results}, {"delivered", "already_recorded"})

    def test_disabled_persists_truthful_status_without_touching_delivery_state(self):
        attempt = self.state / "digest-attempts/daily_summary-2026-10-27.json"
        recovery.atomic_json(attempt, {"status": "outcome_unknown"})
        before = attempt.read_bytes()
        original_lock = recovery.lock
        @contextlib.contextmanager
        def status_lock(module, name="state"):
            self.assertNotEqual(name, "scheduled-run", "a pause must not wait behind intake")
            with original_lock(module, name):
                yield
        with patch.object(recovery, "lock", side_effect=status_lock), \
             patch.object(sys, "argv", ["run.py", "daily_summary"]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run.main(), 0)
        status = self.latest("daily_summary")
        self.assertEqual(status["status"], "disabled")
        self.assertFalse(status["enabled"])
        self.assertEqual(status["enabledSource"], "none")
        self.assertIn("observedAt", status)
        self.assertEqual(attempt.read_bytes(), before)
        self.assertFalse(self.sent)

    def test_before_schedule_persists_local_status_without_sending(self):
        self.now -= dt.timedelta(minutes=1)
        (self.folder / "enabled").touch()
        with patch.object(sys, "argv", ["run.py", "daily_summary"]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run.main(), 0)
        status = self.latest("daily_summary")
        self.assertEqual(status["status"], "before_schedule")
        self.assertTrue(status["enabled"])
        self.assertFalse(self.sent)

    def test_same_day_legacy_stamp_is_not_replayed_even_with_force(self):
        legacy = self.folder / "legacy"
        legacy.mkdir()
        (legacy / "correo-daily-summary-2026-10-27.stamp").touch()
        self.assertEqual(self.call("daily_summary", force=True), 0)
        self.assertFalse(self.stamp().exists(), "legacy stamp is not verified delivery")
        self.assertFalse(self.sent)


if __name__ == "__main__":
    unittest.main()
