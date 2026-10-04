import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from catalog import validate_call
from security import BoundaryError, private_directory, private_file, runtime_config, trusted_actor


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.config = {"schema_version": 1, "enabled": True, "telegram": {
            "account_id": "default", "allowed_sender_ids": ["101"],
            "allowed_routes": [{"chat_id": "-900", "thread_id": "8"}]}}
        self.actor = {"agentId": "nomina", "channel": "telegram", "accountId": "default", "senderId": "101",
                      "chatId": "-900", "threadId": "8", "sessionKey": "agent:nomina:telegram:group:-900:topic:8",
                      "source": "tool_context"}
        self.env = patch.dict(os.environ, {"OWLSWATCH_PAYROLL_WORKSPACE": str(self.root),
            "OWLSWATCH_PAYROLL_CONFIG": "payroll-config.json", "OWLSWATCH_PAYROLL_TRUSTED_CONTEXT": json.dumps(self.actor)})
        self.env.start()
        self.write_config(self.config)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def write_config(self, value):
        path = self.root / "payroll-config.json"
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def test_valid_private_setup_and_host_context(self):
        root, config = runtime_config()
        self.assertEqual(root, self.root.resolve())
        self.assertEqual(trusted_actor(config), self.actor)

    def test_workspace_config_permissions_symlinks_and_outside_paths_fail(self):
        config_file = self.root / "payroll-config.json"
        config_file.chmod(0o644)
        with self.assertRaises(BoundaryError):
            runtime_config()
        config_file.chmod(0o600)
        self.root.chmod(0o755)
        with self.assertRaises(BoundaryError):
            runtime_config()
        self.root.chmod(0o700)
        (self.root / "alias.json").symlink_to(config_file)
        for candidate in [self.root / "alias.json", self.root / "../outside.json"]:
            with self.subTest(candidate=candidate), self.assertRaises(BoundaryError):
                private_file(candidate, self.root)
        (self.root / "state").symlink_to(self.root)
        with self.assertRaises(BoundaryError):
            private_directory(self.root / "state", self.root)

    def test_malformed_configuration_rejected_with_safe_boundary_error(self):
        cases = [[], None, "not a config", {**self.config, "schema_version": True}]
        for senders in ["101", {"101": True}, [], [101], ["*"]]:
            value = copy.deepcopy(self.config)
            value["telegram"]["allowed_sender_ids"] = senders
            cases.append(value)
        for value in cases:
            with self.subTest(value=value):
                self.write_config(value)
                with self.assertRaises(BoundaryError):
                    runtime_config()

    def test_every_host_binding_dimension_is_authorized(self):
        for field, value in {"agentId": "main", "channel": "webchat", "accountId": "another",
                             "senderId": "202", "chatId": "-901", "threadId": "9", "sessionKey": "",
                             "source": "native_command"}.items():
            with self.subTest(field=field), patch.dict(os.environ, {
                "OWLSWATCH_PAYROLL_TRUSTED_CONTEXT": json.dumps({**self.actor, field: value})
            }), self.assertRaises(BoundaryError):
                trusted_actor(self.config)

    def test_malformed_host_context_is_safe_rejection(self):
        for value in [[], None, 42, "text"]:
            with self.subTest(value=value), patch.dict(os.environ, {
                "OWLSWATCH_PAYROLL_TRUSTED_CONTEXT": json.dumps(value)
            }), self.assertRaises(BoundaryError):
                trusted_actor(self.config)

    def test_native_approval_requires_explicit_host_authorization_and_freshness(self):
        native = {**self.actor, "source": "native_command", "authorized": True,
                  "approvedAt": time.time(), "approvalEventId": "00000000-0000-4000-8000-000000000001"}
        with patch.dict(os.environ, {"OWLSWATCH_PAYROLL_TRUSTED_CONTEXT": json.dumps(native)}):
            self.assertEqual(trusted_actor(self.config, native=True)["source"], "native_command")
        for changes in [{"authorized": "true"}, {"authorized": False}, {"approvedAt": time.time() - 301},
                        {"approvedAt": time.time() + 60}, {"approvedAt": True}, {"approvalEventId": "invalid"},
                        {"source": "tool_context"}]:
            with self.subTest(changes=changes), patch.dict(os.environ, {
                "OWLSWATCH_PAYROLL_TRUSTED_CONTEXT": json.dumps({**native, **changes})
            }), self.assertRaises(BoundaryError):
                trusted_actor(self.config, native=True)


class SchemaTests(unittest.TestCase):
    def test_no_model_confirmation_or_context_properties(self):
        for name, payload in [("nomina_status", {"confirmed": True}),
                              ("nomina_prepare_finalize", {"run_id": "run-1", "expected_revision": 1,
                               "request_id": "req-1", "actor": {"authorized": True}})]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_call(name, payload)

    def test_money_and_revision_never_coerce_float_or_bool(self):
        for value in [True, 1.0, "1", -1]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_call("nomina_adjust_draft", {"run_id": "run-1", "expected_revision": 1,
                    "payee_id": "payee-1", "kind": "earning", "amount": value,
                    "reason": "Reviewed adjustment", "request_id": "req-1"})

    def test_kind_payload_mismatch_and_unknown_fields_rejected(self):
        outside = {"loan_id": "loan-1", "amount": 10, "paid_on": "2026-10-04", "reference": "receipt-1", "reason": "Paid outside payroll"}
        with self.assertRaises(ValueError):
            validate_call("nomina_prepare_change", {"kind": "open_loan", "payload": outside, "request_id": "req-1"})
        validate_call("nomina_prepare_change", {"kind": "outside_repayment", "payload": outside, "request_id": "req-1"})

    def test_loan_override_is_explicit_and_can_be_zero(self):
        args = {"run_id": "run-1", "expected_revision": 1, "payee_id": "payee-1", "kind": "loan_override",
                "loan_id": "loan-1", "amount": 0, "reason": "Skip this quincena", "request_id": "req-1"}
        validate_call("nomina_adjust_draft", args)
        with self.assertRaises(ValueError):
            validate_call("nomina_adjust_draft", {**args, "kind": "earning"})
        del args["loan_id"]
        with self.assertRaises(ValueError):
            validate_call("nomina_adjust_draft", args)

    def test_invalid_array_members_and_duplicates_produce_validation_errors(self):
        for values in [["payee-1", "payee-1"], [{}], [None], []]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_call("nomina_prepare_paid", {"run_id": "run-1", "expected_revision": 1,
                    "payee_ids": values, "request_id": "req-1"})


if __name__ == "__main__":
    unittest.main()
