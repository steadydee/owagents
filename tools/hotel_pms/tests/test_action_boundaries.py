import concurrent.futures
import datetime as dt
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location("hotel_actions", Path(__file__).resolve().parents[1] / "server.py")
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)
guard = server.reservation_guard
journal = server.submission_journal


def context(**extra):
    return {"channel": "telegram", "agentId": "hotel", "senderId": "100", "chatId": "-200", "threadId": "3", "sessionKey": "agent:hotel:test", "accountId": "default", **extra}


def approval(**extra):
    return context(source="native_command", authorized=True, approvedAt=dt.datetime.now(dt.timezone.utc).timestamp(), approvalEventId="12345678-1234-1234-1234-123456789abc", **extra)


def submitted():
    return {"status": "submitted", "receiptReference": "synthetic-receipt", "responseSummary": {}}


def die_after_provider_accepts(root, counter):
    def send():
        with open(counter, "a") as handle:
            handle.write("accepted\n")
            handle.flush()
            os.fsync(handle.fileno())
        os._exit(17)
    journal.execute(Path(root), ["test", "tra"], {"payload": 1}, lambda: journal.external_step("primary", send), lambda result: {})


class ReservationBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.draft_patch = patch.object(server, "RESERVATION_DRAFT_DIR", self.root / "drafts")
        self.draft_patch.start()
        self.addCleanup(self.draft_patch.stop)
        self.pending = server.store_reservation_draft({
            "confirmationCode": "AB12", "preparedToken": "synthetic-prepared-token",
            "expiresAt": (server.now_utc() + dt.timedelta(minutes=5)).isoformat(),
            "idempotencyKey": "source-test-message", "summary": {"guestName": "Example"},
        }, {"guestName": "Example"}, context())

    def call(self, ctx, *, approved=False, args=None):
        with patch.dict(os.environ, {"HOTEL_TRUSTED_CONTEXT": json.dumps(ctx)}), patch.object(server, "load_config", return_value={}):
            return server.create_bound_reservation(args or {"pendingId": self.pending}, approve=approved)

    def test_model_si_and_forged_approval_arguments_cannot_create(self):
        with patch.object(server, "pms_tool") as upstream:
            with self.assertRaises(guard.StateError) as caught:
                self.call(context(), args={"pendingId": self.pending, "confirmationText": "si", "authorized": True, "approval": approval()})
            self.assertEqual(caught.exception.code, "human_approval_required")
            upstream.assert_not_called()

    def test_missing_host_context_fails_closed(self):
        with self.assertRaises(guard.StateError) as caught:
            self.call({}, approved=True)
        self.assertEqual(caught.exception.code, "trusted_context_required")

    def test_command_requires_authenticated_fresh_event(self):
        for ctx in [context(), {**approval(), "authorized": False}, {**approval(), "approvedAt": 0}]:
            with self.subTest(ctx=ctx), self.assertRaises(guard.StateError):
                self.call(ctx, approved=True)

    def test_wrong_sender_chat_topic_session_or_account_rejected(self):
        for field, value in [("senderId", "101"), ("chatId", "-201"), ("threadId", "4"), ("sessionKey", "another"), ("accountId", "other")]:
            with self.subTest(field=field), self.assertRaises(guard.StateError) as caught:
                self.call(approval(**{field: value}), approved=True)
            self.assertEqual(caught.exception.code, "approval_binding_mismatch")

    def test_mutated_draft_rejected(self):
        path = server.draft_file_for_pending_id(self.pending)
        data = json.loads(path.read_text())
        data["requestPayload"]["guestName"] = "Changed"
        server.atomic_json(path, data)
        with self.assertRaises(guard.StateError) as caught:
            self.call(approval(), approved=True)
        self.assertEqual(caught.exception.code, "prepared_draft_changed")

    def test_expired_draft_rejected(self):
        with patch.object(server, "now_utc", return_value=server.now_utc() + dt.timedelta(hours=1)), self.assertRaises(server.ToolError) as caught:
            self.call(approval(), approved=True)
        self.assertEqual(caught.exception.code, "prepared_draft_expired")

    def test_single_use_returns_result_without_repeating_write(self):
        with patch.object(server, "pms_tool", return_value={"reservationId": "synthetic-reservation"}) as upstream:
            first = self.call(approval(), approved=True)
            second = self.call(approval(), approved=True)
            third = self.call(context())
        self.assertEqual(first, second)
        self.assertEqual(first, third)
        self.assertEqual(upstream.call_count, 1)
        self.assertEqual(upstream.call_args.args[2]["sourceMetadata"]["telegramUserId"], "100")

    def test_reservation_timeout_blocks_second_write(self):
        with patch.object(server, "pms_tool", side_effect=TimeoutError()) as upstream:
            with self.assertRaises(guard.StateError):
                self.call(approval(), approved=True)
            with self.assertRaises(guard.StateError) as caught:
                self.call(approval(), approved=True)
            self.assertEqual(caught.exception.code, "reservation_outcome_unknown")
            self.assertEqual(upstream.call_count, 1)

    def test_simultaneous_confirmations_issue_one_create(self):
        draft = server.load_reservation_draft(self.pending, by_pending_id=True)
        entered, release = threading.Event(), threading.Event()
        calls = []
        def create(_approval):
            calls.append(1)
            entered.set()
            self.assertTrue(release.wait(5))
            return {"ok": True, "reservation": {"reservationId": "synthetic"}}
        def execute():
            return guard.execute_approved(self.root / "approval-test", self.pending, draft, approval(), True, create)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(execute)
            self.assertTrue(entered.wait(5))
            try:
                with self.assertRaises(guard.StateError) as caught:
                    execute()
                self.assertEqual(caught.exception.code, "operation_in_progress")
            finally:
                release.set()
            first.result()
        self.assertEqual(calls, [1])

    def test_legacy_unbound_draft_cannot_be_approved(self):
        path = server.draft_file_for_pending_id(self.pending)
        data = json.loads(path.read_text())
        data.pop("approvalBinding")
        server.atomic_json(path, data)
        with self.assertRaises(guard.StateError):
            self.call(approval(), approved=True)

    def test_notification_destination_is_pinned(self):
        config = {"env": {"vars": {"HOTEL_TELEGRAM_NOTIFY_CHAT_ID": "-200", "HOTEL_TELEGRAM_NOTIFY_THREAD_ID": "3"}}}
        with patch.object(server, "load_config", return_value=config), patch.object(server, "telegram_token", return_value="synthetic"), patch.object(server, "http_json", return_value={"ok": True, "result": {}}) as send:
            for args in [{"chat_id": "-999"}, {"message_thread_id": "4"}]:
                with self.assertRaises(server.ToolError):
                    server.tool_hotel_telegram_send_message({"text": "test", **args})
            send.assert_not_called()
            server.tool_hotel_telegram_send_message({"text": "test"})
            self.assertEqual(send.call_args.args[1]["chat_id"], "-200")
            self.assertEqual(send.call_args.args[1]["message_thread_id"], "3")


class GovernmentRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = ["test", "tra"]
        self.payload = {"payload": 1}

    def execute(self, send, record, payload=None):
        return journal.execute(self.root, self.key, payload or self.payload, send, record)

    def test_pms_failure_replays_receipt_only(self):
        send = Mock(return_value=submitted())
        record = Mock(side_effect=[TimeoutError(), {"state": "submitted"}])
        with self.assertRaises(journal.StateError) as caught:
            self.execute(send, record)
        self.assertEqual(caught.exception.code, "government_receipt_pending")
        outcome, saved = self.execute(send, record)
        self.assertEqual(saved["state"], "submitted")
        self.assertEqual(send.call_count, 1)
        self.assertEqual(record.call_count, 2)
        self.execute(send, record)
        self.assertEqual(record.call_count, 2)

    def test_model_tool_recovers_pms_receipt_without_second_submission(self):
        attempts = []
        def pms(config, name, payload=None, profile="read"):
            if name == "registro_get_by_reservation":
                return {"registrationId": "synthetic-reg", "status": "validated", "dueSubmissionTypes": ["tra"]}
            if name == "registro_list_guests":
                return {"guests": [{"registrationGuestId": "synthetic-guest", "submissionStatus": "ready", "extractionStatus": "extracted", "missingFields": []}]}
            if name == "registro_prepare_government_submission":
                return {"registrationId": "synthetic-reg", "submissionType": "tra", "status": "ready", "idempotencyKey": "synthetic-key", "payload": {"test": True}}
            if name == "registro_record_submission":
                attempts.append(payload)
                if len(attempts) == 1:
                    raise TimeoutError()
                return {"state": "submitted"}
            raise AssertionError(name)
        with patch.object(server, "WORKSPACE", self.root), patch.object(server, "load_config", return_value={}), patch.object(server, "pms_tool", side_effect=pms), patch.object(server, "government_submitter_enabled", return_value=True), patch.object(server, "call_tra_submitter", return_value=submitted()) as submit:
            first = server.tool_hotel_registro_submit_government({"reservationId": "synthetic-res", "mode": "submit"})
            second = server.tool_hotel_registro_submit_government({"reservationId": "synthetic-res", "mode": "submit"})
            self.assertEqual(first["results"][0]["reason"], "government_receipt_pending")
            self.assertEqual(second["status"], "submitted")
            self.assertEqual(submit.call_count, 1)
        self.assertEqual(attempts[0]["receiptReference"], attempts[1]["receiptReference"])

    def test_changed_payload_does_not_reuse_receipt_or_resubmit(self):
        send, record = Mock(return_value=submitted()), Mock(return_value={})
        self.execute(send, record)
        with self.assertRaises(journal.StateError) as caught:
            self.execute(send, record, {"payload": 2})
        self.assertEqual(caught.exception.code, "government_payload_changed")
        self.assertEqual(send.call_count, 1)

    def test_timeout_and_unverified_response_require_reconciliation(self):
        for name, send in [("timeout", Mock(side_effect=TimeoutError())), ("missing-receipt", Mock(return_value={"status": "failed"}))]:
            with self.subTest(name=name):
                self.key = [name]
                for _ in range(2):
                    with self.assertRaises(journal.StateError) as caught:
                        self.execute(send, Mock())
                    self.assertEqual(caught.exception.code, "government_outcome_unknown")
                self.assertEqual(send.call_count, 1)

    def test_real_process_crash_after_provider_write_cannot_repeat(self):
        counter = self.root / "fake-provider.txt"
        proc = multiprocessing.get_context("fork").Process(target=die_after_provider_accepts, args=(str(self.root), str(counter)))
        proc.start()
        proc.join(timeout=10)
        self.assertFalse(proc.is_alive())
        self.assertEqual(proc.exitcode, 17)
        send = Mock(return_value=submitted())
        with self.assertRaises(journal.StateError):
            self.execute(lambda: journal.external_step("primary", send), Mock())
        send.assert_not_called()
        self.assertEqual(counter.read_text(), "accepted\n")

    def test_concurrent_invocations_only_one_provider_call(self):
        entered, release = threading.Event(), threading.Event()
        def send():
            entered.set()
            self.assertTrue(release.wait(5))
            return submitted()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.execute, send, lambda result: {})
            self.assertTrue(entered.wait(5))
            try:
                with self.assertRaises(journal.StateError) as caught:
                    self.execute(Mock(), Mock())
                self.assertEqual(caught.exception.code, "operation_in_progress")
            finally:
                release.set()
            self.assertEqual(first.result()[0]["status"], "submitted")

    def test_confirmed_primary_and_companions_saved_before_pms_write(self):
        def submit():
            return server.call_tra_api_submitter({}, {}, "synthetic")
        with patch.object(server, "build_tra_api_payloads", return_value=({"test": 1}, [{"test": 2}], {"ok": True})), patch.object(server, "http_json", side_effect=[{"code": "synthetic-primary"}, {"status": "ok"}]) as provider:
            with self.assertRaises(journal.StateError):
                self.execute(submit, Mock(side_effect=TimeoutError()))
            self.execute(submit, Mock(return_value={}))
            self.assertEqual(provider.call_count, 2)
        rows = [json.loads(path.read_text()) for path in self.root.glob("*.json")]
        self.assertEqual(rows[0]["steps"]["tra-primary"]["result"]["code"], "synthetic-primary")
        self.assertEqual(rows[0]["steps"]["tra-companion-0"]["state"], "confirmed")
        self.assertEqual(next(self.root.glob("*.json")).stat().st_mode & 0o777, 0o600)

    def test_partial_companion_failure_never_recreates_primary(self):
        with patch.object(server, "build_tra_api_payloads", return_value=({"test": 1}, [{"test": 2}], {"ok": True})), patch.object(server, "http_json", side_effect=[{"code": "synthetic-primary"}, TimeoutError()]) as provider:
            for _ in range(2):
                with self.assertRaises(journal.StateError):
                    self.execute(lambda: server.call_tra_api_submitter({}, {}, "synthetic"), Mock())
            self.assertEqual(provider.call_count, 2)
        row = json.loads(next(self.root.glob("*.json")).read_text())
        self.assertEqual(row["steps"]["tra-primary"]["state"], "confirmed")
        self.assertEqual(row["steps"]["tra-companion-0"]["state"], "unknown")

    def test_provider_error_body_is_not_marked_submitted(self):
        with patch.object(server, "build_tra_api_payloads", return_value=({}, [{}], {"ok": True})), patch.object(server, "http_json", side_effect=[{"code": "synthetic-primary"}, {"status": "error"}]):
            with self.assertRaises(journal.StateError):
                self.execute(lambda: server.call_tra_api_submitter({}, {}, "synthetic"), Mock())

    def test_pure_local_block_is_retryable_without_uncertain_claim(self):
        blocked = Mock(return_value={"status": "blocked", "reason": "credentials_missing"})
        outcome, saved = self.execute(blocked, Mock())
        self.assertEqual(outcome["status"], "blocked")
        self.assertIsNone(saved)
        self.execute(Mock(return_value=submitted()), Mock(return_value={}))


if __name__ == "__main__":
    unittest.main()
