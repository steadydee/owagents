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
        self.save_item()

    def save_item(self, identifier="digest-fixture", **overrides):
        task = {"taskId": identifier, "gmailThreadId": identifier, "sourceMessageId": "source-" + identifier,
                "status": "needs_human", "summary": "The guest needs an arrival time confirmed before booking transport.",
                "nextAction": "Confirm the arrival time and reply to the guest in Gmail.",
                "lastVerifiedSourceSubject": "Arrival time confirmation", "lastReconciledAt": self.now.isoformat(),
                "lastVerifiedExternalMessageAt": (self.now - dt.timedelta(hours=1)).isoformat(),
                "lastVerifiedMessageId": "source-" + identifier, "reconciliationState": "external_reply_pending",
                "updatedAt": (self.now - dt.timedelta(minutes=30)).isoformat()}
        task.update(overrides)
        recovery.atomic_json(server.TASK_DIR / f"{identifier}.json", task)
        return task

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
        self.assertIn("Arrival time confirmation", text)
        self.assertIn("Next: Confirm the arrival time", text)
        self.assertNotIn("Intake", text)
        self.assertNotIn("recovery", text)
        self.assertNotIn("2026-", text)
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
            self.save_item()  # independent recent, exact-source handoff evidence
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
        self.save_item("other-conversation")
        self.assertEqual(self.unchanged_poll(), 1)  # unresolved daily evidence remains visible
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(self.sent[1]["text"].startswith("Email needing attention"))
        self.assertNotIn("/digest-fixture", self.sent[1]["text"])
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
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(recovery.read_json(self.stamp("unanswered_7d"))["status"], "quiet")
        self.save_item("new-conversation")
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

    def choose(self):
        return run.digest.choose(server, self.state, server.load_config(), self.now, run.digest.read_journal(self.state))

    def test_no_qualified_items_is_quiet_without_delivery_or_model(self):
        self.save_item(sourceMessageId=None)
        self.assertEqual(self.call("daily_summary"), 0)
        stamp = recovery.read_json(self.stamp())
        self.assertEqual(stamp, {"mode": "daily_summary", "day": "2026-10-27", "status": "quiet",
            "reason": "no_new_actionable_items", "completedAt": server.now_iso(), "itemCount": 0})
        self.assertEqual(self.latest("daily_summary")["status"], "quiet")
        self.assertEqual(self.digest_attempt(), {})
        self.assertFalse(self.sent)
        self.save_item()
        self.assertEqual(self.call("daily_summary"), 0)
        self.assertEqual(len(self.sent), 1, "a quiet observation cannot hide later new work")

    def test_selection_requires_recent_protected_exact_source_evidence(self):
        for changes in ({"sourceMessageId": None}, {"lastVerifiedMessageId": "different"},
                        {"reconciliationState": "source_message_unverified"},
                        {"lastReconciledAt": (self.now-dt.timedelta(hours=24, seconds=1)).isoformat()},
                        {"lastReconciledAt": (self.now+dt.timedelta(seconds=1)).isoformat()},
                        {"lastVerifiedExternalMessageAt": (self.now-dt.timedelta(days=7, seconds=1)).isoformat()},
                        {"lastVerifiedExternalMessageAt": None, "lastExternalMessageAt": self.now.isoformat()},
                        {"lastVerifiedSourceSubject": None, "subject": "Unverified model subject"}):
            with self.subTest(changes=changes):
                self.save_item(**changes)
                self.assertEqual(self.choose(), [])

    def test_missing_useful_summary_or_action_does_not_get_boilerplate(self):
        for changes in ({"summary": None}, {"summary": "Arrival time confirmation"},
                        {"nextAction": None}, {"nextAction": "review in gmail"}):
            with self.subTest(changes=changes):
                self.save_item(**changes)
                self.assertEqual(self.choose(), [])

    def test_three_unique_conversations_prioritized_with_context(self):
        self.save_item("duplicate", gmailThreadId="digest-fixture", sourceMessageId="source-digest-fixture",
                       lastVerifiedMessageId="source-digest-fixture")
        self.save_item("urgent", priority="urgent", lastVerifiedSourceSubject="Airport pickup decision")
        self.save_item("high", priority="high", lastVerifiedSourceSubject="Menu choice needed")
        self.save_item("low", priority="low")
        chosen = self.choose()
        self.assertEqual([row["threadId"] for row in chosen], ["urgent", "high", "digest-fixture"])
        self.assertEqual(self.call("daily_summary"), 0)
        text = self.sent[0]["text"]
        self.assertEqual(text.count("Open in Gmail:"), 3)
        self.assertEqual(text.count("Next:"), 3)
        for unwanted in ("Owner", "records", "Overdue", "2026-", "verified", "saved Gmail"):
            self.assertNotIn(unwanted, text)
        stamp = recovery.read_json(self.stamp())
        self.assertEqual(len(stamp["selection"]), 3)
        self.assertNotIn("summary", stamp["selection"][0])

    def test_newer_rejected_duplicate_does_not_mask_valid_same_source(self):
        self.save_item("duplicate", gmailThreadId="digest-fixture", sourceMessageId="source-digest-fixture",
                       lastVerifiedMessageId="source-digest-fixture", nextAction=None,
                       updatedAt=self.now.isoformat())
        self.assertEqual(len(self.choose()), 1)
        self.save_item("duplicate", gmailThreadId="digest-fixture", sourceMessageId="source-digest-fixture",
                       lastVerifiedMessageId="staff-reply", reconciliationState="sent_reply_verified")
        self.assertEqual(self.choose(), [], "a newer conflicting Gmail read must veto old actionability")

    def test_same_business_item_stays_quiet_across_modes_days_and_presentation_edits(self):
        self.assertEqual(self.call("daily_summary"), 0)
        self.now += dt.timedelta(minutes=30)
        self.assertEqual(self.call("unanswered_7d"), 0)
        self.assertEqual(recovery.read_json(self.stamp("unanswered_7d"))["status"], "quiet")
        self.now += dt.timedelta(days=1)
        self.save_item(summary="The guest still needs the arrival time so they can arrange transport.",
                       lastVerifiedSourceSubject="Re: Arrival time confirmation",
                       nextAction="CONFIRM the arrival time and reply to the guest in Gmail!")
        self.assertEqual(self.call("daily_summary"), 0)
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn("notification", recovery.read_json(self.stamp()))

    def test_changed_source_status_and_action_remain_eligible(self):
        first = self.choose()[0]["fingerprint"]
        for changes in ({"sourceMessageId": "new-source", "lastVerifiedMessageId": "new-source"},
                        {"status": "needs_info"}, {"nextAction": "Confirm the pickup location and reply in Gmail."}):
            with self.subTest(changes=changes):
                self.save_item(**changes)
                self.assertNotEqual(self.choose()[0]["fingerprint"], first)
        self.save_item()
        self.assertEqual(self.call("daily_summary"), 0)
        self.now += dt.timedelta(minutes=30)
        self.save_item(sourceMessageId="new-source", lastVerifiedMessageId="new-source", updatedAt=self.now.isoformat(),
                       lastVerifiedExternalMessageAt=self.now.isoformat())
        self.assertEqual(self.call("unanswered_7d"), 0)
        self.assertEqual(len(self.sent), 2)

    def test_unknown_send_blocks_reworded_repeat_across_days_but_not_new_source(self):
        with patch.object(server, "http_json", side_effect=TimeoutError("unknown after send")):
            self.assertEqual(self.call("daily_summary"), 1)
        before = self.digest_attempt()
        self.now += dt.timedelta(minutes=30)
        self.assertEqual(self.call("unanswered_7d"), 0)
        self.assertEqual(self.digest_attempt(), before)
        self.now += dt.timedelta(days=1)
        self.save_item(summary="The guest needs the arrival time in order to arrange airport transport.",
                       nextAction="Confirm the revised pickup location and reply in Gmail.")
        self.assertEqual(self.call("daily_summary"), 0)
        self.assertFalse(self.sent)
        self.save_item(sourceMessageId="new-source", lastVerifiedMessageId="new-source")
        self.assertEqual(self.call("daily_summary"), 0)
        self.assertEqual(len(self.sent), 1)

    def test_recent_immediate_handoff_receipt_prevents_digest_repeat(self):
        server.write_notification("gmail-thread-digest-fixture", {"lastNotifiedAt": server.now_iso(),
            "messageId": 50, "chatId": "fixture-chat", "messageThreadId": 17})
        self.assertEqual(self.call("daily_summary"), 0)
        self.assertFalse(self.sent)
        self.now += dt.timedelta(minutes=1)
        self.save_item(nextAction="Confirm the revised pickup location and reply in Gmail.", updatedAt=self.now.isoformat())
        self.assertEqual(self.call("daily_summary"), 0)
        self.assertFalse(self.sent, "same-source edits conservatively respect the immediate-notice cooldown")
        self.save_item(sourceMessageId="new-source", lastVerifiedMessageId="new-source",
                       lastVerifiedExternalMessageAt=self.now.isoformat())
        self.assertEqual(self.call("daily_summary"), 0)
        self.assertEqual(len(self.sent), 1)

    def test_identical_tool_upsert_does_not_bypass_immediate_notice_cooldown(self):
        task = self.save_item()
        server.write_notification("gmail-thread-digest-fixture", {"lastNotifiedAt": server.now_iso(),
            "messageId": 50, "chatId": "fixture-chat", "messageThreadId": 17})
        self.assertEqual(self.choose(), [])
        self.now += dt.timedelta(minutes=1)
        payload = {key: task[key] for key in ("taskId", "gmailThreadId", "sourceMessageId", "status", "summary", "nextAction")}
        with patch.object(server, "gmail_account", return_value="fixture@example.test"):
            server.tool_email_upsert_task({"task": payload})
        saved = server.read_task(server.TASK_DIR / "digest-fixture.json")
        self.assertEqual(saved["updatedAt"], server.now_iso())
        self.assertEqual(self.choose(), [])
        self.assertEqual(self.call("daily_summary"), 0)
        self.assertFalse(self.sent)

    def test_same_source_changed_action_can_notify_after_immediate_cooldown(self):
        server.write_notification("gmail-thread-digest-fixture", {"lastNotifiedAt": server.now_iso(),
            "messageId": 50, "chatId": "fixture-chat", "messageThreadId": 17})
        self.now += dt.timedelta(hours=24, seconds=1)
        self.save_item(nextAction="Confirm the revised pickup location and reply in Gmail.",
                       lastVerifiedExternalMessageAt=(self.now-dt.timedelta(days=2)).isoformat())
        self.assertEqual(self.call("daily_summary"), 0)
        self.assertEqual(len(self.sent), 1)

    def test_old_or_wrong_destination_immediate_receipt_does_not_hide_item(self):
        for receipt in ({"lastNotifiedAt": (self.now-dt.timedelta(hours=25)).isoformat(), "messageId": 50,
                         "chatId": "fixture-chat", "messageThreadId": 17},
                        {"lastNotifiedAt": server.now_iso(), "messageId": 50, "chatId": "other", "messageThreadId": 17}):
            server.write_notification("gmail-thread-digest-fixture", receipt)
            self.assertEqual(len(self.choose()), 1)

    def test_draft_ready_requires_current_exact_draft_proof(self):
        self.save_item(status="draft_ready", gmailDraftId="draft-one", gmailHasDraft=True,
                       nextAction="Review the draft in Gmail and send after checking the arrival time.")
        self.assertEqual(self.choose(), [])
        self.save_item(status="draft_ready", gmailDraftId="draft-one", lastVerifiedDraftId="other",
                       lastVerifiedDraftSourceMessageId="source-digest-fixture")
        self.assertEqual(self.choose(), [])
        self.save_item(status="draft_ready", gmailDraftId="draft-one", lastVerifiedDraftId="draft-one",
                       lastVerifiedDraftSourceMessageId="source-digest-fixture", nextAction=None)
        self.assertTrue(self.choose()[0]["draftConfirmed"])
        self.assertIn("confirmed draft", self.choose()[0]["nextAction"])

    def test_journal_corruption_fails_closed_without_overwriting(self):
        path = self.state / "digest-items.json"
        for value in ("broken-json", '{"version":1,"items":{"bad":null}}'):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value)
            self.assertEqual(self.call("daily_summary"), 1)
            self.assertEqual(path.read_text(), value)
            self.assertFalse(self.sent)
            self.assertFalse(self.stamp().exists())

    def test_malformed_quiet_stamp_cannot_reopen_delivery(self):
        base = {"mode": "daily_summary", "day": "2026-10-27", "status": "quiet",
                "reason": "no_new_actionable_items", "completedAt": server.now_iso(), "itemCount": 0}
        for changes in ({"notification": {"ok": True, "message_id": 90}}, {"itemCount": False},
                        {"mode": "unanswered_7d"}, {"completedAt": "invalid"}, {"day": "2026-10-26"}):
            stamp = {**base, **changes}
            recovery.atomic_json(self.stamp(), stamp)
            self.assertEqual(self.call("daily_summary"), 1)
            self.assertEqual(recovery.read_json(self.stamp()), stamp)
            self.assertFalse(self.sent)

    def test_parallel_different_modes_cannot_repeat_same_item(self):
        self.now += dt.timedelta(minutes=30)
        def slow(*args, **kwargs):
            time.sleep(0.03)
            return self.transport(*args, **kwargs)
        with patch.object(server, "http_json", side_effect=slow), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda mode: run.due_digests(mode, self.state, self.now), ["daily_summary", "unanswered_7d"]))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual({next(iter(result.values()))["status"] for result in results}, {"delivered", "quiet"})

    def test_crash_between_shared_reservation_and_attempt_write_stays_unresolved(self):
        class Crash(BaseException):
            pass
        original = recovery.atomic_json
        def crash(path, value):
            if path.parent.name == "digest-attempts" and value.get("status") == "sending":
                raise Crash()
            return original(path, value)
        with patch.object(recovery, "atomic_json", side_effect=crash), self.assertRaises(Crash):
            self.call("daily_summary")
        self.assertEqual(self.digest_attempt()["status"], "preparing")
        self.assertEqual(self.call("daily_summary"), 1)
        self.assertEqual(self.digest_attempt()["status"], "outcome_unknown")
        self.assertFalse(self.stamp().exists())
        self.assertFalse(self.sent)


if __name__ == "__main__":
    unittest.main()
