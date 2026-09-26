"""Scheduled Hotel jobs: synthetic workspace only, no provider or bot calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run-hotel-tool-job.py"
SCHEDULE = ROOT / "scripts/run-hotel-registro-pickup.sh"
PRIVATE = "SYNTHETIC_GUEST_DOCUMENT_MUST_NOT_BE_LOGGED"


def pickup_result(**changes):
    # Actual daily_pickup places blocked government outcomes in needsReview,
    # rather than the errors array or top-level ok=False.
    return {
        "ok": True, "mode": "submit_government", "pendingCount": 0,
        "eligibleCount": 0, "processed": [], "needsReview": [],
        "skipped": [], "errors": [], "notificationNeeded": False,
        "telegram": {"ok": True, "sent": False, "reason": "no_action_required"},
        "message": PRIVATE, "notificationMessage": PRIVATE, **changes,
    }


class HotelScheduleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.server_path = self.workspace / "tools/hotel_pms/server.py"
        self.server_path.parent.mkdir(parents=True)
        self.stamps = self.root / "stamps"
        self.log = self.root / "logs/pickup.log"
        self.bash_env = self.root / "clock.sh"
        self.bash_env.write_text('''date() {
  case "$1" in
    +%H) printf '18\\n' ;;
    +%M) printf '00\\n' ;;
    +%Y-%m-%d) printf '2026-09-26\\n' ;;
    *) printf '2026-09-26 18:00:00\\n' ;;
  esac
}
''')
        self.env = {
            **os.environ, "HOTEL_WORKSPACE": str(self.workspace),
            "HOTEL_PMS_WORKSPACE": str(self.root / "must-not-use"),
            "OPENCLAW_CONFIG_PATH": str(self.root / "config.json"),
            "OPENCLAW_STATE_DIR": str(self.root / "state"),
            "HOTEL_REGISTRO_PICKUP_ENABLED": "1",
            "LOG_DIR": str(self.log.parent), "LOG_FILE": str(self.log),
            "STAMP_DIR": str(self.stamps), "ENABLED_FILE": str(self.root / "enabled"),
            "BASH_ENV": str(self.bash_env), "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPYCACHEPREFIX": str(self.root / "pycache"),
        }

    def fake_server(self, result=None, failure=None):
        content = f'''import json, os, sys
from pathlib import Path
print({PRIVATE!r})
print({PRIVATE!r}, file=sys.stderr)
'''
        if failure == "import":
            content += f"raise RuntimeError({PRIVATE!r})\n"
        else:
            content += '''def tool_hotel_registro_daily_pickup(args):
    workspace = Path(os.environ["HOTEL_PMS_WORKSPACE"])
    with (workspace / "calls.jsonl").open("a") as handle:
        handle.write(json.dumps(args) + "\\n")
'''
            content += f"    print({PRIVATE!r})\n    print({PRIVATE!r}, file=sys.stderr)\n"
            if failure == "call":
                content += f"    raise RuntimeError({PRIVATE!r})\n"
            else:
                content += f"    return {result!r}\n"
        self.server_path.write_text(content)

    def run_helper(self):
        result = subprocess.run([sys.executable, str(RUNNER)], env=self.env, capture_output=True, text=True, timeout=10)
        self.assertNotIn(PRIVATE, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(result.stdout.strip().splitlines()), 1)
        return result, json.loads(result.stdout)

    def run_schedule(self):
        result = subprocess.run(["/bin/bash", str(SCHEDULE)], env=self.env, capture_output=True, text=True, timeout=10)
        log = self.log.read_text() if self.log.exists() else ""
        self.assertNotIn(PRIVATE, result.stdout + result.stderr + log)
        return result, log

    def assert_unstamped(self):
        self.assertEqual(list(self.stamps.glob("*.stamp")), [])

    def test_success_is_counts_only_and_invokes_exact_bounded_operation(self):
        self.fake_server(pickup_result(processed=[{"status": "submitted", "receiptReference": PRIVATE}]))
        proc, summary = self.run_helper()
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(summary["success"])
        self.assertEqual(summary["processed"], 1)
        self.assertEqual(summary["code"], "completed")
        calls = [json.loads(line) for line in (self.workspace / "calls.jsonl").read_text().splitlines()]
        self.assertEqual(calls, [{"submitTra": True, "notify": True, "maxRecords": 25, "daysBack": 7, "daysAhead": 2}])
        self.assertFalse((self.root / "must-not-use").exists())

    def test_ordinary_document_review_is_a_completed_sweep(self):
        for reason in ["sin documentos", "sin registro", "sin reserva vinculada", "needs_info", "falta documento/registro de 1 huesped; 1 huesped necesita revision de extraccion", "faltan documentos/registros de 2 huespedes; 2 huespedes necesitan revision de extraccion"]:
            with self.subTest(reason=reason):
                self.fake_server(pickup_result(needsReview=[{"reason": reason, "label": PRIVATE}]))
                proc, summary = self.run_helper()
                self.assertEqual(proc.returncode, 0)
                self.assertEqual(summary["needsReview"], 1)
                self.assertFalse(summary["retryable"])

    def test_generic_mixed_portal_block_and_unknown_review_reasons_fail_closed(self):
        # Actual pickup uses the first result's reason: successful TRA followed
        # by failed SIRE yields "blocked", hiding the later portal's reason.
        for reason in ["blocked", "government_submitter_disabled", "prepared_submission_not_ready", "tra_credentials_missing", PRIVATE, None]:
            with self.subTest(reason=reason):
                self.fake_server(pickup_result(needsReview=[{"reason": reason}]))
                proc, summary = self.run_helper()
                self.assertEqual(proc.returncode, 1)
                self.assertEqual(summary["unresolved"], 1)
                self.assertFalse(summary["retryable"])

    def test_errors_and_truncation_are_not_success(self):
        for changes in [{"errors": [{"reason": PRIVATE}]}, {"truncated": True, "remainingCount": 3}, {"remainingCount": 3}, {"ok": False}]:
            with self.subTest(changes=changes):
                self.fake_server(pickup_result(**changes))
                proc, summary = self.run_helper()
                self.assertEqual(proc.returncode, 1)
                self.assertFalse(summary["success"])

    def test_unknown_outcome_is_actionable_nonretryable_even_in_needs_review(self):
        for reason in ["government_outcome_unknown", "government_payload_changed", "journal_invalid"]:
            with self.subTest(reason=reason):
                self.fake_server(pickup_result(needsReview=[{"reason": reason, "label": PRIVATE}]))
                proc, summary = self.run_helper()
                self.assertEqual(proc.returncode, 1)
                self.assertEqual(summary["code"], "reconciliation_required")
                self.assertEqual(summary["reconciliationRequired"], 1)
                self.assertFalse(summary["retryable"])

    def test_receipt_pending_signals_safe_later_retry_without_retrying_inside_job(self):
        self.fake_server(pickup_result(needsReview=[{"reason": "government_receipt_pending"}]))
        proc, summary = self.run_helper()
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(summary["code"], "receipt_recording_pending")
        self.assertEqual(summary["receiptPending"], 1)
        self.assertTrue(summary["retryable"])
        self.assertEqual(len((self.workspace / "calls.jsonl").read_text().splitlines()), 1)

    def test_concurrent_claim_waits_for_later_schedule(self):
        self.fake_server(pickup_result(needsReview=[{"reason": "operation_in_progress"}]))
        proc, summary = self.run_helper()
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(summary["code"], "operation_in_progress")
        self.assertTrue(summary["retryable"])

    def test_unknown_outcome_takes_precedence_over_receipt_retry(self):
        self.fake_server(pickup_result(needsReview=[{"reason": "government_outcome_unknown"}, {"reason": "government_receipt_pending"}]))
        proc, summary = self.run_helper()
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(summary["code"], "reconciliation_required")
        self.assertFalse(summary["retryable"])

    def test_notification_and_alert_state_failure_are_not_completion(self):
        for changes in [{"telegram": {"ok": False}}, {"alertStateWarning": "state_unavailable"}]:
            with self.subTest(changes=changes):
                self.fake_server(pickup_result(**changes))
                proc, summary = self.run_helper()
                self.assertEqual(proc.returncode, 1)
                self.assertFalse(summary["success"])

    def test_import_tool_and_missing_file_failures_do_not_leak_error_details(self):
        for failure in ["import", "call", "missing"]:
            with self.subTest(failure=failure):
                self.fake_server(failure=failure)
                if failure == "missing":
                    self.server_path.unlink()
                proc, summary = self.run_helper()
                self.assertEqual(proc.returncode, 1)
                self.assertEqual(summary["code"], "tool_job_failed")

    def test_invalid_result_shapes_cannot_leak_through_count_fields(self):
        for result in [None, [], {"ok": True}, pickup_result(remainingCount=PRIVATE), pickup_result(remainingCount=True), pickup_result(remainingCount=-1), pickup_result(errors=PRIVATE), pickup_result(processed=[PRIVATE]), pickup_result(truncated="false")]:
            with self.subTest(result=result):
                self.fake_server(result)
                proc, summary = self.run_helper()
                self.assertEqual(proc.returncode, 1)
                self.assertEqual(summary["code"], "tool_job_failed")

    def test_shell_stamps_only_success_and_does_not_repeat_completed_day(self):
        self.fake_server(pickup_result())
        proc, log = self.run_schedule()
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(len(list(self.stamps.glob("*.stamp"))), 1)
        self.assertIn('"success": true', log)
        second, second_log = self.run_schedule()
        self.assertEqual(second.returncode, 0)
        self.assertIn("already ran today", second_log)
        self.assertEqual(len((self.workspace / "calls.jsonl").read_text().splitlines()), 1)

    def test_shell_never_stamps_failed_or_incomplete_jobs(self):
        for changes in [{"errors": [{"reason": "synthetic_failure"}]}, {"truncated": True, "remainingCount": 1}, {"needsReview": [{"reason": "government_outcome_unknown"}]}, {"needsReview": [{"reason": "government_receipt_pending"}]}, {"needsReview": [{"reason": "blocked"}]}]:
            with self.subTest(changes=changes):
                self.fake_server(pickup_result(**changes))
                proc, log = self.run_schedule()
                self.assertEqual(proc.returncode, 1)
                self.assert_unstamped()
                self.assertNotIn("hotel registro pickup end", log)

    def test_shell_import_failure_is_unstamped(self):
        self.fake_server(failure="import")
        proc, log = self.run_schedule()
        self.assertEqual(proc.returncode, 1)
        self.assert_unstamped()
        self.assertIn('"success": false', log)


if __name__ == "__main__":
    unittest.main()
