import datetime as dt
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

runner = module("finca_schedule", ROOT / "scripts/run-finca-tool-job.py")
server = module("finca_schedule_transport", ROOT / "tools/finca_tasks/server.py")
NOW = dt.datetime(2026, 9, 26, 17, tzinfo=runner.BOGOTA)


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.profile = Path(self.temp.name)
        self.messages = ["private report one", "private report two"]
        self.calls = []
        self.report_calls = []
        self.outcomes = []
        self.destination = "configured-staff"
        def report(*args):
            self.report_calls.append(args)
            return {"messages": self.messages}
        def send(config, destination, text):
            self.calls.append(text)
            outcome = self.outcomes.pop(0) if self.outcomes else {"ok": True, "messageId": len(self.calls)}
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        self.fake = SimpleNamespace(load_config=lambda: {}, notify_chat_id=lambda _: self.destination,
                                    telegram_token=lambda _: "test", operations_tool=report,
                                    worker_safe_report_message=lambda text: text, telegram_send=send)

    def run_job(self, job="report", **kwargs):
        return runner.run_job(job, profile=self.profile, server_path=Path("unused"), server=self.fake,
                              force=True, now=NOW, **kwargs)

    def journal(self, job="report"):
        return self.profile / "schedule-state" / ("finca-daily-" + job) / "2026-09-26.json"

    def stamps(self):
        return list((self.profile / "schedule-stamps").glob("*.stamp"))

    def rejected(self, retryable=True):
        exc = RuntimeError("private provider detail")
        exc.delivery_status = "not_sent"
        exc.retryable = retryable
        return exc

    def test_success_receipts_stamp_and_force_cannot_duplicate(self):
        result = self.run_job()
        self.assertTrue(result["success"])
        self.assertEqual(len(self.stamps()), 1)
        state = json.loads(self.journal().read_text())
        self.assertEqual([row["status"] for row in state["messages"]], ["sent", "sent"])
        self.assertEqual(self.journal().stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.run_job()["code"], "already_completed")
        self.assertEqual(self.calls, self.messages)
        self.assertNotIn("private", json.dumps(result))

    def test_partial_confirmed_rejection_retries_only_unsent_frozen_message(self):
        self.outcomes = [{"ok": True, "messageId": 91}, self.rejected()]
        self.assertTrue(self.run_job()["retryable"])
        self.assertEqual(self.stamps(), [])
        self.messages = ["changed report"]
        self.assertTrue(self.run_job()["success"])
        self.assertEqual(self.calls, ["private report one", "private report two", "private report two"])
        self.assertEqual(len(self.report_calls), 1)

    def test_timeout_and_invalid_receipt_block_retries(self):
        for outcome in [TimeoutError("private timeout"), {"ok": True}, {"ok": False}, {"ok": True, "messageId": True}]:
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                self.profile = Path(tmp)
                self.calls = []
                self.outcomes = [outcome]
                self.assertEqual(self.run_job()["code"], "delivery_outcome_unknown")
                self.assertEqual(self.run_job()["code"], "delivery_outcome_unknown")
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(self.stamps(), [])

    def test_crash_after_attempt_checkpoint_is_not_replayed(self):
        self.outcomes = [KeyboardInterrupt()]
        with self.assertRaises(KeyboardInterrupt):
            self.run_job()
        self.assertEqual(json.loads(self.journal().read_text())["messages"][0]["status"], "attempting")
        self.assertEqual(self.run_job()["code"], "delivery_outcome_unknown")
        self.assertEqual(len(self.calls), 1)

    def test_receipt_persistence_failure_is_unknown_on_restart(self):
        original = runner.atomic_write
        def fail_receipt(path, text):
            if path.suffix == ".json" and any(row["status"] == "sent" for row in json.loads(text)["messages"]):
                raise OSError("disk unavailable")
            return original(path, text)
        with mock.patch.object(runner, "atomic_write", side_effect=fail_receipt), self.assertRaises(OSError):
            self.run_job()
        self.assertEqual(self.run_job()["code"], "delivery_outcome_unknown")
        self.assertEqual(len(self.calls), 1)

    def test_stamp_failure_recovers_without_resending_receipted_messages(self):
        original = runner.atomic_write
        def fail_stamp(path, text):
            if path.suffix == ".stamp":
                raise OSError("stamp unavailable")
            return original(path, text)
        with mock.patch.object(runner, "atomic_write", side_effect=fail_stamp), self.assertRaises(OSError):
            self.run_job()
        self.assertTrue(self.run_job()["success"])
        self.assertEqual(self.calls, self.messages)

    def test_definitive_rejects_are_bounded_and_permanent_rejects_not_retried(self):
        self.outcomes = [self.rejected()] * 3
        for _ in range(3):
            self.assertFalse(self.run_job()["success"])
        self.assertFalse(self.run_job()["retryable"])
        self.assertEqual(len(self.calls), 3)
        self.profile = self.profile / "permanent"
        self.outcomes = [self.rejected(False)]
        self.run_job()
        self.run_job()
        self.assertEqual(len(self.calls), 4)

    def test_changed_destination_and_invalid_journal_fail_closed(self):
        self.outcomes = [self.rejected()]
        self.run_job()
        self.destination = "changed-staff"
        self.assertEqual(self.run_job()["code"], "destination_changed")
        self.journal().write_text('{"broken":true}')
        self.assertEqual(self.run_job()["code"], "journal_invalid")
        self.assertEqual(len(self.calls), 1)

    def test_enable_and_time_gates_do_not_load_server(self):
        self.assertEqual(runner.run_job("report", profile=self.profile, server_path=Path("missing"), now=NOW)["code"], "not_due")
        (self.profile / "daily-report.enabled").touch()
        self.assertEqual(runner.run_job("report", profile=self.profile, server_path=Path("missing"), now=NOW.replace(hour=6))["code"], "not_due")

    def test_checkin_has_one_fixed_message_and_honors_legacy_stamp(self):
        self.assertTrue(self.run_job("checkin")["success"])
        self.assertEqual(self.calls, [runner.CHECKIN_TEXT])
        self.assertEqual(self.report_calls, [])
        self.fake.load_config = lambda: self.fail("Stamped job must not load config")
        self.assertEqual(self.run_job("checkin")["code"], "already_completed")

    def test_real_concurrent_processes_claim_job_once_without_business_io(self):
        fake_path = self.profile / "fake_server.py"
        fake_path.write_text('''import os, time
from pathlib import Path
def load_config(): return {}
def notify_chat_id(config): return "test-staff"
def telegram_token(config): return "test"
def telegram_send(config, destination, text):
    path = Path(os.environ["FINCA_PROFILE_DIR"]) / "calls"
    with path.open("a") as stream: stream.write("sent\\n")
    time.sleep(0.7)
    return {"ok": True, "messageId": 11}
''')
        env = {"PATH": os.environ["PATH"], "HOME": str(self.profile), "FINCA_PROFILE_DIR": str(self.profile),
               "FINCA_TOOL_SERVER": str(fake_path), "PYTHONPYCACHEPREFIX": str(self.profile / "pycache")}
        cmd = [sys.executable, str(ROOT / "scripts/run-finca-tool-job.py"), "checkin", "--force"]
        first = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not (self.profile / "calls").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue((self.profile / "calls").exists())
            second = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=5)
            self.assertEqual(second.returncode, 1)
            self.assertEqual(json.loads(second.stdout)["code"], "operation_in_progress")
            stdout, stderr = first.communicate(timeout=5)
            self.assertEqual(first.returncode, 0, stderr)
            self.assertTrue(json.loads(stdout)["success"])
            self.assertEqual((self.profile / "calls").read_text().splitlines(), ["sent"])
        finally:
            if first.poll() is None:
                first.kill()
                first.wait()


class TelegramOutcomeTests(unittest.TestCase):
    def test_only_explicit_definitive_http_rejects_are_safe_not_sent(self):
        for status in [400, 401, 403, 404, 429, 408, 500, 502, 503]:
            body = io.BytesIO(json.dumps({"ok": False, "error_code": status}).encode())
            error = server.parse_http_error(urllib.error.HTTPError("https://example.invalid", status, "error", {}, body), "http_error")
            self.assertEqual(getattr(error, "delivery_status", None) == "not_sent", status in [400, 401, 403, 404, 429])
        malformed = urllib.error.HTTPError("https://example.invalid", 429, "error", {}, io.BytesIO(b"proxy response"))
        self.assertIsNone(getattr(server.parse_http_error(malformed, "http_error"), "delivery_status", None))

    def test_http_200_errors_and_missing_receipts_remain_conservative(self):
        with mock.patch.object(server, "notify_chat_id", return_value="test-staff"), mock.patch.object(server, "telegram_token", return_value="test"):
            for code in [400, 401, 403, 404, 429, 408, 500, None]:
                with self.subTest(code=code), mock.patch.object(server, "http_json", return_value={"ok": False, "error_code": code}):
                    with self.assertRaises(server.ToolError) as caught:
                        server.telegram_send({}, "test-staff", "test")
                    self.assertEqual(getattr(caught.exception, "delivery_status", None) == "not_sent", code in [400, 401, 403, 404, 429])
            with mock.patch.object(server, "http_json", return_value={"ok": True, "result": {}}), self.assertRaises(server.ToolError):
                server.telegram_send({}, "test-staff", "test")


if __name__ == "__main__":
    unittest.main()
