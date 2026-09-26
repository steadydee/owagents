from __future__ import annotations
import base64
import datetime as dt
import email
import json
import os
import plistlib
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import recovery
import run


class Request:
    def __init__(self, value): self.value = value
    def execute(self):
        if isinstance(self.value, BaseException): raise self.value
        return self.value() if callable(self.value) else self.value


def message(identifier, sender="guest@example.test", labels=None):
    return {"id": identifier, "threadId": "thread1", "internalDate": "1780000000000", "labelIds": labels or ["INBOX"], "payload": {"headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": "Trip inquiry"}, {"name": "Message-ID", "value": f"<{identifier}@example.test>"}], "mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(b"Hello, please help with a trip.").decode()}}}


class Gmail:
    def __init__(self):
        self.messages = [message("message1")]
        self.created = []
        self.fail_after_create = False
        self.fail_get = False
        self.history_page = {"historyId": "101", "history": []}
        self.thread_page = {"threads": [{"id": "thread1"}]}
        self.kind = None
    def users(self): return self
    def threads(self): self.kind = "threads"; return self
    def history(self): self.kind = "history"; return self
    def drafts(self): self.kind = "drafts"; return self
    def getProfile(self, **kw): return Request({"historyId": "100"})
    def list(self, **kw):
        if self.kind == "threads": return Request(self.thread_page)
        if self.kind == "history": return Request(self.history_page)
        return Request({"drafts": [{"id": item["id"]} for item in self.created]})
    def get(self, **kw):
        if self.kind == "threads":
            return Request(RuntimeError("provider down") if self.fail_get else {"id": kw["id"], "messages": self.messages})
        return Request(next(item for item in self.created if item["id"] == kw["id"]))
    def create(self, **kw):
        def apply():
            mime = email.message_from_bytes(base64.urlsafe_b64decode(kw["body"]["message"]["raw"] + "===") )
            item = {"id": f"draft{len(self.created)+1}", "message": {"threadId": "thread1", "payload": {"headers": [{"name": "Message-ID", "value": mime["Message-ID"]}]}}}
            self.created.append(item)
            if self.fail_after_create: raise RuntimeError("response lost")
            return item
        return Request(apply)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        folder = Path(self.tmp.name)
        self.gmail = Gmail()
        for name, value in {"WORKSPACE": folder, "TASK_DIR": folder/"tasks/email", "NOTIFICATION_DIR": folder/"tasks/email_notifications", "MEMORY_DIR": folder/"memory"}.items():
            p = patch.object(server, name, value); p.start(); self.addCleanup(p.stop)
        for name, value in {"load_config": lambda: {"env": {"vars": {"OWLSWATCH_EMAIL_NOTIFY_CHAT_ID": "configured-chat"}}}, "google_build_service": lambda *_: self.gmail, "gmail_drafts_enabled": lambda _: True}.items():
            p = patch.object(server, name, value); p.start(); self.addCleanup(p.stop)
    def ack_ignored(self, scan):
        for item in scan["candidates"]:
            recovery.acknowledge(server, {**item, "scanId": scan["scanId"], "outcome": "ignored"})
    def test_textual_success_does_not_advance_cursor(self):
        scan = recovery.preflight(server)
        self.assertEqual(len(scan["candidates"]), 1)
        self.assertFalse(recovery.finalize(server, scan["scanId"], 0)["ok"])
        checkpoint = recovery.read_json(recovery.root(server)/"checkpoint.json")
        self.assertNotIn("historyId", checkpoint)
        resumed = recovery.preflight(server)
        self.assertEqual(resumed["scanId"], scan["scanId"])
        self.ack_ignored(scan)
        self.assertTrue(recovery.finalize(server, scan["scanId"])["ok"])
        self.assertEqual(recovery.read_json(recovery.root(server)/"checkpoint.json")["historyId"], "100")
    def test_unchanged_poll_skips_model(self):
        scan = recovery.preflight(server); self.ack_ignored(scan); recovery.finalize(server, scan["scanId"])
        def must_not_invoke(*a, **kw): self.fail("model invoked for no new email")
        self.assertEqual(run.run("polling_30m", invoke=must_not_invoke), 0)
    def test_provider_failure_retains_discovery(self):
        self.gmail.fail_get = True
        with self.assertRaises(RuntimeError): recovery.preflight(server)
        checkpoint = recovery.read_json(recovery.root(server)/"checkpoint.json")
        self.assertEqual(checkpoint["discovery"]["ids"], ["thread1"])
        self.gmail.fail_get = False
        self.assertEqual(len(recovery.preflight(server)["candidates"]), 1)
    def test_crash_after_checkpoint_commit_is_replayable(self):
        scan = recovery.preflight(server); self.ack_ignored(scan)
        original = recovery.atomic_json
        def crash(path, value):
            if path.name == "active.json" and value.get("status") == "completed": raise RuntimeError("crash")
            return original(path, value)
        with patch.object(recovery, "atomic_json", side_effect=crash):
            with self.assertRaises(RuntimeError): recovery.finalize(server, scan["scanId"])
        self.assertTrue(recovery.finalize(server, scan["scanId"])["ok"])
    def test_waiting_task_visible_and_owner_explicit(self):
        saved = server.tool_email_upsert_task({"task": {"taskId": "one", "gmailThreadId": "thread1", "sourceMessageId": "message1", "status": "waiting_for_quote"}})
        self.assertEqual(saved["owner"], "unassigned")
        self.assertIsNotNone(saved["nextActionAt"])
        self.assertEqual(server.tool_email_list_open_tasks({})["tasks"][0]["status"], "waiting_for_quote")
    def test_only_confirmed_sent_reply_closes_task(self):
        server.tool_email_upsert_task({"task": {"taskId": "one", "gmailThreadId": "thread1", "status": "needs_human"}})
        self.gmail.messages.append(message("draft", "info@owlswatch.com", ["DRAFT"]))
        scan = recovery.preflight(server)
        self.assertEqual(len(scan["candidates"]), 1)
        self.assertEqual(server.read_task(server.task_path("one"))["status"], "needs_human")
        self.ack_ignored(scan); recovery.finalize(server, scan["scanId"])
        self.gmail.messages.append(message("sent", "info@owlswatch.com", ["SENT"]))
        self.gmail.history_page = {"historyId": "102", "history": [{"messagesAdded": [{"message": {"threadId": "thread1"}}]}]}
        self.assertEqual(recovery.preflight(server)["candidates"], [])
        self.assertEqual(server.read_task(server.task_path("one"))["status"], "resolved")
    def test_ack_requires_saved_exact_source_and_handoff(self):
        scan = recovery.preflight(server); args={**scan["candidates"][0], "scanId": scan["scanId"], "outcome": "task_saved", "taskId": "one"}
        with self.assertRaises(server.ToolError): recovery.acknowledge(server,args)
        server.tool_email_upsert_task({"task": {"taskId": "one", "gmailThreadId": "thread1", "sourceMessageId": "message1", "status": "needs_human"}})
        with self.assertRaises(server.ToolError): recovery.acknowledge(server,args)
        server.write_notification("gmail-thread-thread1", {"messageId": 123, "lastNotifiedAt": server.now_iso(), "chatId": "configured-chat"})
        self.assertTrue(recovery.acknowledge(server,args)["ok"])
    def draft_args(self): return {"threadId": "thread1", "to": "guest@example.test", "subject": "Re: Trip", "body": "Thank you."}
    def test_draft_repeated_create_reuses_confirmed_operation(self):
        first = server.tool_gmail_create_draft(self.draft_args())
        again = server.tool_gmail_create_draft(self.draft_args())
        self.assertEqual(first["gmailDraftId"], again["gmailDraftId"])
        self.assertEqual(len(self.gmail.created),1)
    def test_existing_human_or_legacy_draft_is_preserved(self):
        self.gmail.messages.append(message("legacy-draft", "info@owlswatch.com", ["DRAFT"]))
        with self.assertRaises(server.ToolError) as error:
            server.tool_gmail_create_draft(self.draft_args())
        self.assertEqual(error.exception.code,"existing_gmail_draft")
        self.assertFalse(self.gmail.created)
    def test_lost_draft_response_reconciles_without_second_create(self):
        self.gmail.fail_after_create=True
        with self.assertRaises(server.ToolError): server.tool_gmail_create_draft(self.draft_args())
        self.gmail.fail_after_create=False
        result=server.tool_gmail_create_draft(self.draft_args())
        self.assertTrue(result["reused"])
        self.assertEqual(len(self.gmail.created),1)
    def test_unknown_draft_does_not_blindly_retry(self):
        self.gmail.fail_after_create=True
        with self.assertRaises(server.ToolError): server.tool_gmail_create_draft(self.draft_args())
        self.gmail.created=[]
        with self.assertRaises(server.ToolError) as err: server.tool_gmail_create_draft(self.draft_args())
        self.assertEqual(err.exception.code,"draft_outcome_unknown")
        self.assertFalse(self.gmail.created)
    def test_destination_override_rejected(self):
        with self.assertRaises(server.ToolError): server.tool_email_send_telegram_message({"text":"test","chat_id":"elsewhere"})
        args=self.draft_args();args["to"]="elsewhere@example.test"
        with self.assertRaises(server.ToolError): server.tool_gmail_create_draft(args)
    def test_pending_source_refreshes_without_skipping_ack(self):
        scan=recovery.preflight(server)
        self.gmail.messages.append(message("message2"))
        resumed=recovery.preflight(server)
        self.assertEqual(resumed["scanId"],scan["scanId"])
        self.assertEqual(resumed["candidates"][0]["sourceMessageId"],"message2")
        with self.assertRaises(server.ToolError):
            recovery.acknowledge(server,{**scan["candidates"][0],"scanId":scan["scanId"],"outcome":"ignored"})
        self.ack_ignored(resumed)
        self.assertTrue(recovery.finalize(server,scan["scanId"])["ok"])
    def test_bounded_batch_preserves_rest_of_provider_page(self):
        self.gmail.thread_page={"threads":[{"id":f"thread{x}"} for x in range(9)]}
        seen=[]
        for expected in (4,4,1):
            scan=recovery.preflight(server)
            self.assertEqual(len(scan["candidates"]),expected)
            seen.extend(c["threadId"] for c in scan["candidates"])
            self.ack_ignored(scan);recovery.finalize(server,scan["scanId"])
        self.assertEqual(len(set(seen)),9)
        self.assertEqual(recovery.read_json(recovery.root(server)/"checkpoint.json")["historyId"],"100")
    def test_history_expiry_retains_recovery_gap(self):
        class Expired(Exception):
            resp=type("Response",(),{"status":404})()
        recovery.atomic_json(recovery.root(server)/"checkpoint.json",{"historyId":"old","lastAcknowledgedAt":"2026-01-01T00:00:00Z"})
        original=self.gmail.list
        def listing(**kw):
            return Request(Expired()) if self.gmail.kind=="history" else original(**kw)
        self.gmail.list=listing
        scan=recovery.preflight(server)
        checkpoint=recovery.read_json(recovery.root(server)/"checkpoint.json")
        self.assertEqual(checkpoint["expiredHistoryId"],"old")
        self.assertEqual(checkpoint["recoveryStartAt"],"2025-12-31T00:00:00+00:00")
        self.assertTrue(scan["candidates"])
    def test_old_reconciliation_error_does_not_block_new_intake(self):
        server.tool_email_upsert_task({"task":{"taskId":"old","gmailThreadId":"deleted","status":"needs_human"}})
        original=self.gmail.get
        def get(**kw):
            return Request(RuntimeError("gone")) if kw.get("id")=="deleted" else original(**kw)
        self.gmail.get=get
        self.assertTrue(recovery.preflight(server)["candidates"])
        self.assertEqual(len(recovery.read_json(recovery.root(server)/"checkpoint.json")["reconciliationErrors"]),1)
    def test_thread_response_is_bounded(self):
        self.gmail.messages=[message(str(i)) for i in range(30)]
        result=server.tool_gmail_read_thread({"threadId":"thread1"})["thread"]
        self.assertEqual(len(result["messages"]),6)
        self.assertEqual(result["messageCount"],30)
        self.assertTrue(result["truncated"])

    def test_install_uses_fixed_bin_not_worktree(self):
        base=Path(self.tmp.name); fake=base/"fake-bin";fake.mkdir()
        launch=fake/"launchctl";launch.write_text("#!/bin/sh\nexit 0\n");launch.chmod(0o755)
        repository=Path(__file__).resolve().parents[3]
        env={**os.environ,"PATH":str(fake)+":"+os.environ.get("PATH",""),"OWLSWATCH_RUNTIME_BIN":str(base/"runtime-bin"),"OWLSWATCH_SCHEDULE_LOG_DIR":str(base/"logs"),"OWLSWATCH_LAUNCHAGENT_DIR":str(base/"plists"),"ENABLED_FILE":str(base/"enabled")}
        subprocess.run(["bash",str(repository/"scripts/install-email-schedules.sh")],env=env,check=True,capture_output=True)
        plists=list((base/"plists").glob("*.plist"));self.assertEqual(len(plists),3)
        for file in plists:
            value=plistlib.loads(file.read_bytes())
            script=Path(value["ProgramArguments"][0])
            self.assertEqual(script.parent,base/"runtime-bin")
            self.assertTrue(script.exists())
            self.assertNotIn(str(repository),file.read_text())
    def test_catalog_matches_manifest_and_destinations_are_not_arguments(self):
        manifest=json.loads((Path(__file__).resolve().parents[1]/"openclaw.plugin.json").read_text())
        self.assertEqual(set(manifest["contracts"]["tools"]),set(server.TOOLS))
        schema=server.TOOLS["owlswatch_email_send_telegram_message"][1]
        self.assertNotIn("chat_id",schema["properties"])
        self.assertNotIn("message_thread_id",schema["properties"])

    def test_atomic_failure_preserves_previous_record(self):
        path=Path(self.tmp.name)/"value.json"; recovery.atomic_json(path,{"n":1})
        with patch.object(recovery.os,"replace",side_effect=OSError("crash")):
            with self.assertRaises(OSError): recovery.atomic_json(path,{"n":2})
        self.assertEqual(recovery.read_json(path),{"n":1})


if __name__ == "__main__": unittest.main()
