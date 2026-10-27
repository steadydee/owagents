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

    def test_legacy_missing_source_and_business_waits_do_not_close_from_sent_mail(self):
        for status in ["draft_ready", "waiting_for_quote", "waiting_for_payment", "waiting_for_availability", "needs_info"]:
            task = {"taskId": status, "gmailThreadId": "thread1", "status": status}
            if status != "draft_ready":
                task["sourceMessageId"] = "message1"
            server.write_task(task)
        self.gmail.messages.append(message("sent", "info@owlswatch.com", ["SENT"]))
        self.assertEqual(recovery.reconcile_tasks(server, "thread1", server.thread_messages({"messages": self.gmail.messages}), "info@owlswatch.com"), 0)
        self.assertEqual(server.read_task(server.task_path("draft_ready"))["reconciliationState"], "source_message_unverified")
        self.assertEqual(server.read_task(server.task_path("waiting_for_quote"))["reconciliationState"], "business_outcome_unverified")
        for status in ["draft_ready", "waiting_for_quote", "waiting_for_payment", "waiting_for_availability", "needs_info"]:
            self.assertEqual(server.read_task(server.task_path(status))["status"], status)

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


    def save_source(self, **extra):
        task = {"taskId": "one", "gmailThreadId": "thread1", "sourceMessageId": "message1",
                "status": "needs_human", "summary": "Guest requests a trip estimate.", "nextAction": "Review trip dates."}
        task.update(extra)
        server.tool_email_upsert_task({"task": task})
        return server.read_task(server.task_path("one"))

    def test_existing_read_provides_tool_owned_source_evidence_without_more_http(self):
        with patch.object(self.gmail, "get", wraps=self.gmail.get) as get:
            server.tool_gmail_read_thread({"threadId": "thread1"})
            task = self.save_source(lastVerifiedExternalMessageAt="2099-01-01T00:00:00Z",
                                    lastVerifiedSourceSubject="Forged subject", lastVerifiedDraftId="invented")
        self.assertEqual(get.call_count, 1)
        self.assertEqual(task["reconciliationState"], "external_reply_pending")
        self.assertEqual(task["lastVerifiedMessageId"], "message1")
        self.assertEqual(task["lastVerifiedSourceSubject"], "Trip inquiry")
        self.assertEqual(task["lastVerifiedExternalMessageAt"], server.message_to_summary(self.gmail.messages[0])["date"])
        self.assertIsNone(task["lastVerifiedDraftId"])
        self.assertIsNotNone(task["lastReconciledAt"])
        stored = handoffs.recent_evidence(server, "thread1", server.gmail_account(server.load_config()))
        self.assertNotIn("Hello, please help", json.dumps(stored))
        self.assertNotIn("guest@example.test", json.dumps(stored))

    def test_all_new_proof_fields_reject_model_assertions_without_provider_read(self):
        task = self.save_source(**{name: "forged" for name in handoffs.EVIDENCE_FIELDS})
        self.assertTrue(handoffs.EVIDENCE_FIELDS.isdisjoint(task))

    def test_new_source_clears_old_proof_and_inherited_request_action(self):
        server.tool_gmail_read_thread({"threadId": "thread1"})
        old = self.save_source()
        self.assertEqual(old["lastVerifiedSourceSubject"], "Trip inquiry")
        server.tool_email_upsert_task({"task": {"taskId": "one", "sourceMessageId": "new-message"}})
        task = server.read_task(server.task_path("one"))
        self.assertEqual(task["reconciliationState"], "source_not_found")
        self.assertIsNone(task["lastVerifiedExternalMessageAt"])
        self.assertIsNone(task["lastVerifiedSourceSubject"])
        self.assertNotIn("summary", task)
        self.assertNotIn("nextAction", task)
        self.assertNotIn("draft", task)
        # A subsequent task for another thread cannot reuse thread1's proof.
        server.tool_email_upsert_task({"task": {"taskId": "one", "gmailThreadId": "thread2", "sourceMessageId": "message1"}})
        task = server.read_task(server.task_path("one"))
        self.assertTrue(handoffs.EVIDENCE_FIELDS.isdisjoint(task))

    def test_stale_future_naive_corrupt_and_cross_account_cache_fail_closed(self):
        account = server.gmail_account(server.load_config())
        server.tool_gmail_read_thread({"threadId": "thread1"})
        path = handoffs._evidence_path(server, "thread1", account)
        valid = recovery.read_json(path)
        invalids = [{**valid, "checkedAt": "2020-01-01T00:00:00Z"},
                    {**valid, "checkedAt": (server.now_utc()+dt.timedelta(minutes=1)).isoformat()},
                    {**valid, "checkedAt": "2026-10-27T15:00:00"}, {**valid, "state": []},
                    {**valid, "accountHash": "another account"}, {**valid, "threadId": "thread2"},
                    {**valid, "latestMessageId": "not-in-thread"},
                    {**valid, "latestExternalMessageAt": "2099-01-01T00:00:00Z"}]
        for value in invalids:
            with self.subTest(value=list(value.items())[-1:]):
                recovery.atomic_json(path, value)
                self.assertIsNone(handoffs.recent_evidence(server, "thread1", account))
        path.write_text("{broken")
        self.assertIsNone(handoffs.recent_evidence(server, "thread1", account))

    def test_preflight_existing_read_can_verify_new_task_before_rotation(self):
        scan = recovery.preflight(server)
        self.assertEqual(scan["candidates"][0]["sourceMessageId"], "message1")
        with patch.object(server, "google_build_service", side_effect=AssertionError("No extra provider read")):
            task = self.save_source()
        self.assertEqual(task["lastVerifiedMessageId"], "message1")
        self.assertEqual(task["reconciliationState"], "external_reply_pending")
        self.assertEqual(task["lastVerifiedSourceSubject"], "Trip inquiry")

    def test_raw_thread_reconciliation_preserves_actual_draft_presence(self):
        self.save_source()
        self.gmail.messages.append(message("human-draft", "info@owlswatch.com", ["DRAFT"]))
        scan = recovery.preflight(server)
        self.assertEqual(len(scan["candidates"]), 1)
        task = server.read_task(server.task_path("one"))
        self.assertIs(task["gmailHasDraft"], True)
        self.assertIsNone(task["lastVerifiedDraftId"], "Unrelated Gmail draft is not exact-source proof")
        evidence = handoffs.recent_evidence(server, "thread1", server.gmail_account(server.load_config()))
        self.assertEqual(evidence["draftMessageIds"], ["human-draft"])
        self.assertEqual(evidence["latestMessageId"], "message1")

    def create_fixture_draft(self):
        server.tool_gmail_read_thread({"threadId": "thread1"})
        return server.tool_gmail_create_draft({"threadId": "thread1", "to": "guest@example.test",
                                               "subject": "Re: Trip", "body": "Thank you."})

    def test_new_positive_create_is_immediately_bound_without_refreshing_source_read(self):
        result = self.create_fixture_draft()
        evidence = handoffs.recent_evidence(server, "thread1", server.gmail_account(server.load_config()))
        task = self.save_source(status="draft_ready", gmailDraftId=result["gmailDraftId"])
        self.assertEqual(task["lastVerifiedDraftId"], result["gmailDraftId"])
        self.assertEqual(task["lastVerifiedDraftSourceMessageId"], "message1")
        self.assertEqual(task["lastReconciledAt"], evidence["checkedAt"])
        self.assertEqual(evidence["draftEvidenceSource"], "create_receipt")
        self.assertEqual(len(self.gmail.created), 1)

    def test_delete_read_clears_exact_draft_and_reused_journal_cannot_restore_it(self):
        result = self.create_fixture_draft()
        self.save_source(status="draft_ready", gmailDraftId=result["gmailDraftId"])
        # The fake provider now returns the source but no DRAFT message.
        server.tool_gmail_read_thread({"threadId": "thread1"})
        observed = server.read_task(server.task_path("one"))
        self.assertIsNone(observed["lastVerifiedDraftId"], "Deletion must clear saved proof before any later upsert")
        self.assertIs(observed["gmailHasDraft"], False)
        again = server.tool_gmail_create_draft({"threadId": "thread1", "to": "guest@example.test", "subject": "Re: Trip", "body": "Thank you."})
        self.assertIs(again["reused"], True)
        task = self.save_source(status="draft_ready", gmailDraftId=result["gmailDraftId"])
        self.assertIsNone(task["lastVerifiedDraftId"])
        self.assertIs(task["gmailHasDraft"], False)

    def test_normal_read_of_sent_reply_immediately_closes_exact_saved_request(self):
        server.tool_gmail_read_thread({"threadId": "thread1"})
        self.save_source()
        self.gmail.messages.append(message("sent", "info@owlswatch.com", ["SENT"]))
        with patch.object(self.gmail, "get", wraps=self.gmail.get) as get:
            server.tool_gmail_read_thread({"threadId": "thread1"})
        self.assertEqual(get.call_count, 1)
        task = server.read_task(server.task_path("one"))
        self.assertEqual(task["status"], "resolved")
        self.assertEqual(task["resolutionMessageId"], "sent")
        self.assertIsNone(task["lastVerifiedExternalMessageAt"])
        self.assertIsNone(task["lastVerifiedDraftId"])

    def test_confirmed_journal_only_matches_its_provider_draft_rfc_identity(self):
        result = self.create_fixture_draft()
        operation = next((recovery.root(server)/"draft_operations").glob("*.json"))
        record = recovery.read_json(operation)
        unrelated = message("human-draft", "info@owlswatch.com", ["DRAFT"])
        self.gmail.messages.append(unrelated)
        server.tool_gmail_read_thread({"threadId": "thread1"})
        self.assertIsNone(self.save_source(status="draft_ready", gmailDraftId=result["gmailDraftId"])["lastVerifiedDraftId"])
        exact = message("provider-draft-message", "info@owlswatch.com", ["DRAFT"])
        next(header for header in exact["payload"]["headers"] if header["name"] == "Message-ID")["value"] = record["messageId"]
        self.gmail.messages.append(exact)
        server.tool_gmail_read_thread({"threadId": "thread1"})
        task = self.save_source(status="draft_ready", gmailDraftId=result["gmailDraftId"])
        self.assertEqual(task["lastVerifiedDraftId"], result["gmailDraftId"])
        self.assertNotEqual(task["lastVerifiedDraftId"], "provider-draft-message")
        record["status"] = "unknown"
        recovery.atomic_json(operation, record)
        server.tool_gmail_read_thread({"threadId": "thread1"})
        self.assertIsNone(self.save_source()["lastVerifiedDraftId"])


if __name__ == "__main__":
    unittest.main()
