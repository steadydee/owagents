import datetime as dt
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import handoffs
import recovery
import review_backlog
import server
import test_recovery
from test_recovery import message


class HandoffTests(unittest.TestCase):
    setUp = test_recovery.RecoveryTests.setUp
    def test_model_cannot_assign_owner_postpone_due_or_forge_reconciliation(self):
        recovery.atomic_json(recovery.root(server)/"review-policy.json", {"defaultOwner": "Configured reviewer", "normalSlaHours": 48})
        now = server.now_utc()
        saved = server.tool_email_upsert_task({"task": {"taskId": "one", "status": "needs_human", "owner": "Someone else", "nextActionAt": "2099-01-01T00:00:00Z", "reconciliationState": "sent_reply_verified", "resolutionMessageId": "invented"}})
        self.assertEqual(saved["owner"], "Configured reviewer")
        self.assertLess(server.parse_iso_datetime(saved["nextActionAt"]), now+dt.timedelta(hours=49))
        actual = server.read_task(server.task_path("one"))
        self.assertNotIn("resolutionMessageId", actual)
        self.assertNotIn("reconciliationState", actual)
        due = saved["nextActionAt"]
        again = server.tool_email_upsert_task({"task": {"taskId": "one", "nextActionAt": "2099-01-01T00:00:00Z"}})
        self.assertEqual(again["nextActionAt"], due)

    def test_existing_owner_and_overdue_deadline_survive_default_change(self):
        old = {"owner": "Existing reviewer", "createdAt": "2026-01-01T00:00:00Z", "nextActionAt": "2026-01-02T00:00:00Z"}
        meta = handoffs.metadata(server, old, old, {"defaultOwner": "New default", "normalSlaHours": 48})
        self.assertEqual(meta["owner"], "Existing reviewer")
        self.assertTrue(handoffs.overview(server, meta)["overdue"])

    def test_legacy_identifier_resolution_never_uses_generated_task_hash(self):
        known = {"taskId": "email-1234567890abcdef", "gmailThreadId": "thread1"}
        self.assertEqual(handoffs.candidate_thread_ids({"taskId": "two", "id": known["taskId"]}, {known["taskId"]: known}), ["thread1"])
        self.assertEqual(handoffs.candidate_thread_ids({"taskId": "other", "id": known["taskId"]}), [])
        self.assertEqual(handoffs.candidate_thread_ids({"taskId": "other", "id": "thr_1234567890abcdef"}), ["1234567890abcdef"])
        self.assertEqual(handoffs.candidate_thread_ids({"taskId": "one", "id": "one"}, {"one": {"taskId": "one", "id": "one"}}), [])

    def test_sent_message_cannot_close_source_not_present_in_thread(self):
        server.tool_email_upsert_task({"task": {"taskId": "one", "gmailThreadId": "thread1", "sourceMessageId": "missing", "status": "needs_human"}})
        self.gmail.messages.append(message("sent", "info@owlswatch.com", ["SENT"]))
        self.assertEqual(recovery.reconcile_tasks(server, "thread1", server.thread_messages({"messages": self.gmail.messages}), "info@owlswatch.com"), 0)
        task = server.read_task(server.task_path("one"))
        self.assertEqual(task["status"], "needs_human")
        self.assertEqual(task["reconciliationState"], "source_not_found")

    def test_plan_reads_but_does_not_mutate_tasks_then_apply_closes_with_proof(self):
        server.tool_email_upsert_task({"task": {"taskId": "one", "gmailThreadId": "thread1", "sourceMessageId": "message1", "status": "needs_human"}})
        self.gmail.messages.append(message("sent", "info@owlswatch.com", ["SENT"]))
        state = Path(self.tmp.name)/"audit"
        planned = review_backlog.review(state, read_gmail=True, owner="Configured reviewer")
        self.assertEqual(planned["verifiedSentClosures"], 1)
        self.assertEqual(server.read_task(server.task_path("one"))["status"], "needs_human")
        self.assertFalse((recovery.root(server)/"review-policy.json").exists())
        applied = review_backlog.review(state, apply=True, owner="Configured reviewer")
        self.assertEqual(applied["verifiedSentClosures"], 1)
        task = server.read_task(server.task_path("one"))
        self.assertEqual(task["resolutionMessageId"], "sent")
        self.assertEqual(task["owner"], "Configured reviewer")
        self.assertFalse(self.gmail.created)

    def test_missing_identity_never_closed_based_on_note(self):
        server.tool_email_upsert_task({"task": {"taskId": "one", "status": "needs_human", "note": "Someone says they already replied."}})
        review_backlog.review(Path(self.tmp.name)/"audit")
        result = review_backlog.review(Path(self.tmp.name)/"audit", apply=True)
        self.assertEqual(result["verifiedSentClosures"], 0)
        self.assertEqual(result["reconciliationStates"], {"source_identity_missing": 1})

    def test_stale_evidence_cannot_close_a_task(self):
        server.tool_email_upsert_task({"task": {"taskId": "one", "gmailThreadId": "thread1", "status": "needs_human"}})
        state = Path(self.tmp.name)/"audit"
        recovery.atomic_json(state/"gmail-evidence.json", {"thread1": {"threadId": "thread1", "checkedAt": "2020-01-01T00:00:00Z", "state": "sent_reply_verified", "messageIds": ["guest", "sent"], "latestExternalMessageId": "guest", "sentReplyMessageId": "sent"}})
        review_backlog.review(state)
        result = review_backlog.review(state, apply=True)
        self.assertEqual(result["verifiedSentClosures"], 0)
        self.assertEqual(result["gmailReadsThisRun"], 1)

    def test_backlog_apply_skips_tasks_changed_after_plan(self):
        server.tool_email_upsert_task({"task": {"taskId": "one", "status": "needs_human"}})
        state = Path(self.tmp.name)/"audit"
        review_backlog.review(state, owner="Configured reviewer")
        server.tool_email_upsert_task({"task": {"taskId": "one", "status": "waiting_for_quote"}})
        result = review_backlog.review(state, owner="Configured reviewer", apply=True)
        self.assertEqual(result["skippedChangedTasks"], 1)
        self.assertEqual(server.read_task(server.task_path("one"))["owner"], "unassigned")
        self.assertEqual(server.read_task(server.task_path("one"))["status"], "waiting_for_quote")

    def test_new_inbound_after_plan_blocks_cached_sent_closure(self):
        server.tool_email_upsert_task({"task": {"taskId": "one", "gmailThreadId": "thread1", "sourceMessageId": "message1", "status": "needs_human"}})
        self.gmail.messages.append(message("sent", "info@owlswatch.com", ["SENT"]))
        state = Path(self.tmp.name)/"audit"
        self.assertEqual(review_backlog.review(state, read_gmail=True)["verifiedSentClosures"], 1)
        self.gmail.messages.append(message("new-external"))
        result = review_backlog.review(state, apply=True)
        self.assertEqual(result["verifiedSentClosures"], 0)
        self.assertEqual(server.read_task(server.task_path("one"))["status"], "needs_human")

    def test_backup_failure_prevents_local_mutations(self):
        server.tool_email_upsert_task({"task": {"taskId": "one", "status": "needs_human"}})
        state = Path(self.tmp.name)/"audit"
        review_backlog.review(state, owner="Configured reviewer")
        original = recovery.atomic_json
        def write(path, value):
            if path.name.startswith("backup-"):
                raise OSError("Synthetic disk failure")
            return original(path, value)
        with patch.object(recovery, "atomic_json", side_effect=write), self.assertRaises(OSError):
            review_backlog.review(state, owner="Configured reviewer", apply=True)
        self.assertEqual(server.read_task(server.task_path("one"))["owner"], "unassigned")
        self.assertFalse((recovery.root(server)/"review-policy.json").exists())


if __name__ == "__main__":
    unittest.main()
