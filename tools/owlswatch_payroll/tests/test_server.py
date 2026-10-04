"""Service-boundary journeys with synthetic people and no external services."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from archive import Archive, restore_backup
from engine import PayrollEngine, PayrollError
import server
from security import BoundaryError


class ServiceJourneyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.config = {"schema_version": 1, "enabled": True,
                       "telegram": {"account_id": "default", "allowed_sender_ids": ["101"],
                                    "allowed_routes": [{"chat_id": "101", "thread_id": ""}]},
                       "archive": {"enabled": False}}
        self.write_config()
        self.actor = {"channel": "telegram", "agentId": "nomina", "senderId": "101", "chatId": "101",
                      "threadId": "", "accountId": "default", "sessionKey": "agent:nomina:telegram:direct:101",
                      "source": "tool_context"}
        self.environment = patch.dict(os.environ, {"OWLSWATCH_PAYROLL_WORKSPACE": str(self.root),
                                     "OWLSWATCH_PAYROLL_CONFIG": "payroll-config.json"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def write_config(self):
        path = self.root / "payroll-config.json"
        path.write_text(json.dumps(self.config))
        path.chmod(0o600)

    def invoke(self, name, args=None, command="call", actor=None):
        context = dict(actor or self.actor)
        if command != "call":
            context.update(source="native_command", authorized=True, approvedAt=time.time(), approvalEventId=str(uuid.uuid4()))
        with patch.dict(os.environ, {"OWLSWATCH_PAYROLL_TRUSTED_CONTEXT": json.dumps(context)}):
            return server.execute(command, name, args or {})

    def confirmed(self, prepared):
        token = prepared["result"]["pending_id"]
        review = self.invoke(token, command="review")
        self.assertTrue(review["ok"])
        self.assertIn("/confirmar_nomina " + token, review["summary"])
        return self.invoke(token, command="approve")

    def payee(self, payee_id="sample-a", salary=1750905, allowance=249095, kind="employee", health=35018):
        return self.confirmed(self.invoke("nomina_prepare_change", {
            "kind": "upsert_payee", "request_id": "baseline-" + payee_id,
            "payload": {"payee_id": payee_id, "display_name": "Synthetic " + payee_id,
                        "kind": kind, "monthly_salary": salary, "monthly_allowance": allowance,
                        "health_per_half": health, "pension_per_half": health,
                        "other_deduction_per_half": 0, "active": True, "effective_from": "2026-08-H1",
                        "payment_destination": {"institution": "Test bank", "account": "SYNTHETIC-ACCOUNT-1234"},
                        "baseline_note": "Synthetic approved recurring amounts; no statutory assumption."}}))

    def test_native_review_is_required_and_full_account_never_reaches_model(self):
        arguments = {"kind": "upsert_payee", "request_id": "initial-payee", "payload": {
            "payee_id": "sample", "display_name": "Synthetic", "kind": "employee", "monthly_salary": 2000000,
            "monthly_allowance": 0, "health_per_half": 0, "pension_per_half": 0,
            "other_deduction_per_half": 0, "active": True, "effective_from": "2026-08-H1",
            "payment_destination": {"institution": "Test", "account": "SYNTHETIC-ACCOUNT-1234"},
            "baseline_note": "Explicit synthetic deduction baseline."}}
        prepared = self.invoke("nomina_prepare_change", arguments)
        token = prepared["result"]["pending_id"]
        self.assertNotIn("SYNTHETIC-ACCOUNT-1234", json.dumps(prepared))
        with self.assertRaises(PayrollError) as caught:
            self.invoke(token, command="approve")
        self.assertEqual(caught.exception.code, "REVIEW_REQUIRED")
        with patch.dict(os.environ, {"OWLSWATCH_PAYROLL_TRUSTED_CONTEXT": json.dumps(self.actor)}):
            with self.assertRaises(BoundaryError):
                server.execute("review", token)
        reviewed = self.invoke(token, command="review")
        self.assertIn("SYNTHETIC-ACCOUNT-1234", reviewed["summary"])
        self.assertNotIn("preview", reviewed["result"])
        approved = self.invoke(token, command="approve")
        self.assertTrue(approved["ok"])
        self.assertNotIn("SYNTHETIC-ACCOUNT-1234", json.dumps(approved))
        replay_review = self.invoke(token, command="review")
        self.assertIn("ya fue confirmada", replay_review["summary"])
        self.assertNotIn("/confirmar_nomina", replay_review["summary"])

    def test_native_review_displays_markup_as_literal_data(self):
        from native_review import render_native
        account = "[masked](https://example.invalid)```_*"
        summary = render_native({"action": "upsert_payee", "expires_at": time.time() + 900,
            "confirmation_command": "/confirmar_nomina AABBCCDDEEFF0011", "preview": {
                "display_name": "Synthetic **Name**", "payee_id": "sample", "kind": "employee",
                "effective_from": "2026-08-H1", "active": True, "monthly_salary": 2000000,
                "monthly_allowance": 0, "health_per_half": 0, "pension_per_half": 0,
                "other_deduction_per_half": 0, "payment_destination": {"institution": "Test", "account": account},
                "baseline_note": "Approved synthetic baseline"}})
        self.assertTrue(summary.startswith("````\n"))
        self.assertIn(account, summary)
        self.assertTrue(summary.endswith("`/confirmar_nomina AABBCCDDEEFF0011`"))

    def test_finalization_exports_partial_payment_summary_and_replay_are_consistent(self):
        self.payee("sample-a")
        self.payee("sample-b", 500000, 0, "contractor", 0)
        run = self.invoke("nomina_prepare_run", {"period": "2026-08-H1", "request_id": "draft"})["result"]
        final_prepared = self.invoke("nomina_prepare_finalize", {
            "run_id": run["run_id"], "expected_revision": run["revision"], "request_id": "finalize"})
        final = self.confirmed(final_prepared)
        self.assertIn("1,179,964", final["summary"])
        self.assertEqual(final["archive"]["status"], "not_configured")
        exported = list((self.root / "exports").glob("*/payroll.json"))
        self.assertEqual(len(exported), 1)
        self.assertIn("SYNTHETIC-ACCOUNT-1234", exported[0].read_text())
        self.assertEqual(exported[0].stat().st_mode & 0o777, 0o600)
        payment = self.invoke("nomina_prepare_paid", {"run_id": run["run_id"], "expected_revision": run["revision"],
                             "payee_ids": ["sample-b"], "request_id": "pay-contractor"})
        paid = self.confirmed(payment)
        self.assertIn("250,000", paid["summary"])
        self.assertNotIn("1,179,964", paid["summary"])
        self.assertEqual(paid["result"]["run"]["status"], "partially_paid")
        replay = self.invoke(payment["result"]["pending_id"], command="approve")
        self.assertEqual(replay["result"], paid["result"])
        self.assertEqual(len(replay["result"]["run"]["payments"]), 1)

    def test_native_commits_enqueue_restorable_encrypted_ledger_and_reservations(self):
        from cryptography.fernet import Fernet
        secrets = self.root / "secrets"
        secrets.mkdir(mode=0o700)
        key = secrets / "backup.key"
        key.write_bytes(Fernet.generate_key())
        key.chmod(0o600)
        self.config["archive"] = {"enabled": True, "backup_key_file": "secrets/backup.key",
                                  "google_credentials_file": "secrets/not-configured.json", "drive_folder_id": "synthetic"}
        self.write_config()
        self.assertEqual(self.payee()["archive"]["status"], "queued")
        self.confirmed(self.invoke("nomina_prepare_change", {"kind": "open_loan", "request_id": "open-loan", "payload": {
            "loan_id": "sample-loan", "payee_id": "sample-a", "original_principal": 2100000,
            "opening_balance": 2100000, "as_of_date": "2026-08-01", "installment": 400000,
            "start_period": "2026-08-H1", "agreement_reference": "Synthetic signed agreement"}}))
        run = self.invoke("nomina_prepare_run", {"period": "2026-08-H1", "request_id": "draft"})["result"]
        result = self.confirmed(self.invoke("nomina_prepare_finalize", {
            "run_id": run["run_id"], "expected_revision": run["revision"], "request_id": "finalize"}))
        self.assertEqual(result["archive"]["status"], "queued")
        backup = self.root / "backups" / (result["archive"]["job_id"] + ".fernet")
        restored = self.root / "restored"
        restore_backup(backup, key, restored)
        recovered = PayrollEngine(restored / "payroll.sqlite3")
        loan = recovered.call("nomina_get_loans", {}, self.actor)["loans"][0]
        self.assertEqual((loan["outstanding"], loan["reserved"]), (2100000, 400000))
        self.assertEqual(recovered.snapshot(run["run_id"])["status"], "finalized")
        self.assertEqual(len(recovered.call("nomina_history", {}, self.actor)["runs"]), 1)

    def test_archive_failure_does_not_change_committed_confirmation(self):
        self.config["archive"] = {"enabled": True, "backup_key_file": "secrets/missing.key"}
        self.write_config()
        result = self.payee()
        self.assertTrue(result["ok"])
        self.assertEqual(result["archive"]["status"], "needs_retry")
        self.assertEqual(self.invoke("nomina_status")["result"]["counts"]["payees"], 1)

    def test_unauthorized_invocation_cannot_create_payroll_database(self):
        with self.assertRaises(BoundaryError):
            self.invoke("nomina_status", actor={**self.actor, "senderId": "202"})
        self.assertFalse((self.root / "state" / "payroll.sqlite3").exists())

    def test_cli_catalog_works_without_setup_and_error_never_discloses_input(self):
        program = Path(server.__file__)
        result = subprocess.run([sys.executable, str(program), "catalog"], capture_output=True, text=True, check=True)
        self.assertIn("nomina_prepare_run", json.loads(result.stdout))
        env = {**os.environ, "OWLSWATCH_PAYROLL_TRUSTED_CONTEXT": json.dumps(self.actor)}
        result = subprocess.run([sys.executable, str(program), "call", "nomina_prepare_change"],
                                input='{"secret":"DO-NOT-DISCLOSE"}', capture_output=True, text=True, env=env, check=True)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertNotIn("DO-NOT-DISCLOSE", result.stdout)


if __name__ == "__main__":
    unittest.main()
