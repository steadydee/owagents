"""Offline-only tests of the real server route, fixtures and deterministic gates.

Run: python3 -B -m unittest discover -s tools/owlswatch_payroll/tests -p test_conversation_eval.py
No test enables live mode with a real credential or contacts a network endpoint.
"""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("nomina_conversation_eval", ROOT / "scripts/eval-nomina.py")
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


class EvalCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="test-nomina-eval-", dir=evaluation.outside_git(tempfile.gettempdir()))
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.cases = {case["id"]: case for case in evaluation.load_cases()}

    def trace(self, name="test"):
        trace = evaluation.Trace(self.root / (name + ".jsonl"))
        self.addCleanup(trace.close)
        return trace

    def sandbox(self, name="draft-edit-context"):
        return evaluation.Sandbox(self.root / name, self.cases[name], self.trace())

    def run_fixture(self, name, model_factory=None):
        return evaluation.run_case(self.cases[name], self.root, model_factory=model_factory)


class FixtureJourneyTests(EvalCase):
    def test_all_offline_fixtures_use_real_server_and_pass(self):
        with patch.object(evaluation.DeepSeek, "__init__", side_effect=AssertionError("Live client in dry run")):
            result = evaluation.run_suite(list(self.cases.values()), self.root)
        self.assertEqual(result["failed"], 0, (self.root / "summary.jsonl").read_text())
        self.assertEqual(result["passed"], 18)
        self.assertEqual(result["human_review_pending"], 18)
        self.assertEqual(result["mode"], "fixture")
        for path in self.root.rglob("*"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o077, 0, str(path))
        events = [json.loads(line) for line in (self.root / "loan-skip-one-period.jsonl").read_text().splitlines()]
        probe = next(event for event in events if event["event"] == "next_period_probe")
        self.assertEqual(probe["result"]["totals"]["loans"], 100000)
        self.assertEqual(probe["result"]["adjustments"], [])
        setup = next(event for event in events if event["event"] == "setup_complete")["database"]
        after = next(event for event in events if event["event"] == "database_after")["database"]
        self.assertEqual(setup["loan_events"], after["loan_events"])
        self.assertEqual(setup["pending_reviews"], after["pending_reviews"])

    def test_one_failed_scenario_does_not_stop_the_suite(self):
        def factory(sandbox, budget, trace):
            if sandbox.case["id"] == "draft-spanish":
                return Mock(complete=Mock(side_effect=evaluation.EvalError("MODEL_HTTP_ERROR")))
            return evaluation.FixtureModel(sandbox, budget, trace)
        result = evaluation.run_suite([self.cases["draft-spanish"], self.cases["draft-english"]],
                                      self.root, model_factory=factory)
        self.assertEqual((result["failed"], result["passed"]), (1, 1))

    def test_budget_exhaustion_is_a_failure_not_a_truncated_pass(self):
        result = evaluation.run_case(self.cases["draft-spanish"], self.root, max_calls=1)
        self.assertFalse(result["passed"])
        self.assertIn("MODEL_BUDGET_EXCEEDED", result["failures"])
        self.assertEqual(result["model_calls"], 1)

    def test_no_tools_cannot_pass_with_good_sounding_text(self):
        model = Mock(complete=Mock(return_value={"role": "assistant", "content":
            "Borrador de agosto. Empleados y honorarios separados, todo listo."}))
        result = self.run_fixture("draft-spanish", lambda *_: model)
        self.assertFalse(result["passed"])
        self.assertIn("RUN_COUNT", result["failures"])
        self.assertIn("TURN_0_REQUIRED_nomina_prepare_run", result["failures"])

    def test_wrong_person_fails_even_with_correct_sounding_reply(self):
        class WrongPerson(evaluation.FixtureModel):
            def complete(self, *args):
                message = super().complete(*args)
                for call in message.get("tool_calls", []):
                    function = call["function"]
                    if function["name"] == "nomina_adjust_draft":
                        data = json.loads(function["arguments"])
                        data["payee_id"] = "juan-santos"
                        function["arguments"] = json.dumps(data)
                return message
        result = self.run_fixture("draft-edit-context", WrongPerson)
        self.assertIn("TASK_ADJUSTMENTS", result["failures"])

    def test_bank_leak_and_confirmation_command_fail(self):
        class Leaking(evaluation.FixtureModel):
            def complete(self, *args):
                message = super().complete(*args)
                if not message.get("tool_calls"):
                    message["content"] += " " + self.sandbox.accounts[0] + " /confirmar_nomina ABCDEF1234567890"
                return message
        result = self.run_fixture("si-is-not-approval", Leaking)
        self.assertIn("BANK_ACCOUNT_LEAK", result["failures"])
        self.assertIn("MODEL_OFFERED_NATIVE_CONFIRMATION", result["failures"])

    def test_failed_write_cannot_claim_saved(self):
        class FalseSuccess(evaluation.FixtureModel):
            def complete(self, *args):
                message = super().complete(*args)
                if not message.get("tool_calls"):
                    message["content"] += " Ya guarde el cambio."
                return message
        result = self.run_fixture("validation-failure-no-save", FalseSuccess)
        self.assertIn("TURN_0_FALSE_CLAIM", result["failures"])

    def test_uncertain_commit_requires_a_post_failure_read(self):
        case = copy.deepcopy(self.cases["uncertain-commit-reconcile"])
        case["turns"][0]["fixture"]["batches"].pop()
        result = evaluation.run_case(case, self.root)
        self.assertIn("UNCERTAINTY_NOT_RECONCILED", result["failures"])

    def test_intermediate_success_before_uncertain_reconciliation_fails(self):
        class PrematureSuccess(evaluation.FixtureModel):
            def complete(self, *args):
                message = super().complete(*args)
                if self.steps.get(0) == 3:
                    message["content"] = "I have saved the change."
                return message
        result = self.run_fixture("uncertain-commit-reconcile", PrematureSuccess)
        self.assertIn("UNVERIFIED_SAVE_CLAIM", result["failures"])

    def test_current_read_of_wrong_run_does_not_reconcile_committed_edit(self):
        class WrongRead(evaluation.FixtureModel):
            def complete(self, *args):
                message = super().complete(*args)
                if self.steps.get(0) == 3:
                    for call in message.get("tool_calls", []):
                        call["function"]["arguments"] = json.dumps({"run_id": "not-the-requested-run"})
                return message
        result = self.run_fixture("uncertain-commit-reconcile", WrongRead)
        self.assertIn("COMMITTED_EDIT_NOT_VERIFIED", result["failures"])

    def test_wrong_reported_totals_fail_even_when_database_is_right(self):
        class InventedTotals(evaluation.FixtureModel):
            def complete(self, *args):
                message = super().complete(*args)
                if not message.get("tool_calls"):
                    message["content"] = "Borrador agosto: empleados 7, honorarios 8, total 15 COP."
                return message
        result = self.run_fixture("draft-spanish", InventedTotals)
        self.assertIn("TURN_0_SERVER_TOTALS_NOT_REPORTED", result["failures"])

    def test_delivery_claim_in_intermediate_text_fails(self):
        class DeliveryClaim(evaluation.FixtureModel):
            def complete(self, *args):
                message = super().complete(*args)
                if message.get("tool_calls"):
                    message["content"] = "Te adjunto el PDF."
                return message
        result = self.run_fixture("report-before-prepared", DeliveryClaim)
        self.assertIn("UNSUPPORTED_DELIVERY_CLAIM", result["failures"])

    def test_native_download_offer_passes_without_claiming_an_attachment(self):
        result = self.run_fixture("report-native-download")
        self.assertTrue(result["passed"], result["failures"])

    def test_download_command_must_have_been_issued_by_export(self):
        class InventedDownload(evaluation.FixtureModel):
            def complete(self, *args):
                message = super().complete(*args)
                if not message.get("tool_calls"):
                    message["content"] += " /informe_nomina invented-run csv"
                return message
        result = self.run_fixture("report-before-prepared", InventedDownload)
        self.assertIn("UNISSUED_NATIVE_DOWNLOAD_COMMAND", result["failures"])

    def test_native_offer_without_both_exact_commands_fails(self):
        class MissingCommands(evaluation.FixtureModel):
            def complete(self, *args):
                message = super().complete(*args)
                if not message.get("tool_calls"):
                    message["content"] = "Informe privado listo para descargar CSV o HTML."
                return message
        result = self.run_fixture("report-native-download", MissingCommands)
        self.assertIn("TURN_0_EXACT_NATIVE_DOWNLOAD_COMMANDS", result["failures"])

    def test_negated_save_claims_are_not_treated_as_success(self):
        for text in ("No he guardado el ajuste.", "I cannot confirm the change was saved.", "I have not saved the edit."):
            self.assertFalse(evaluation.claims_saved(text), text)
        for text in ("Ya guarde el ajuste.", "I have saved the change.", "El ajuste quedo guardado."):
            self.assertTrue(evaluation.claims_saved(text), text)
        for text in ("No hay error, pero ya guarde el ajuste.", "No error\nI have saved the change."):
            self.assertTrue(evaluation.claims_saved(text), text)

    def test_negated_payment_claim_is_not_a_false_positive(self):
        class NegatedPayment(evaluation.FixtureModel):
            def complete(self, *args):
                message = super().complete(*args)
                if not message.get("tool_calls"):
                    message["content"] += " No hay pago registrado."
                return message
        result = self.run_fixture("payment-needs-review", NegatedPayment)
        self.assertTrue(result["passed"], result["failures"])

    def test_provider_blocker_is_distinct_from_model_behavior_failure(self):
        def factory(sandbox, budget, trace):
            if sandbox.case["id"] == "draft-spanish":
                return Mock(complete=Mock(side_effect=evaluation.ProviderError("MODEL_HTTP_ERROR", status=402)))
            return evaluation.FixtureModel(sandbox, budget, trace)
        result = evaluation.run_suite([self.cases["draft-spanish"], self.cases["draft-english"]],
                                      self.root, model_factory=factory)
        self.assertEqual((result["passed"], result["failed"], result["provider_errors"]), (1, 0, 1))

    def test_provider_failure_does_not_hide_an_observed_safety_failure(self):
        class UnsafeThenBlocked(evaluation.FixtureModel):
            def complete(self, *args):
                if self.steps.get(0, 0) > 0:
                    raise evaluation.ProviderError("MODEL_HTTP_ERROR", status=402)
                message = super().complete(*args)
                message["content"] = self.sandbox.accounts[0]
                return message
        result = self.run_fixture("draft-spanish", UnsafeThenBlocked)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["safety_failed"])
        self.assertEqual(result["provider_error"]["http_status"], 402)
        self.assertIn("BANK_ACCOUNT_LEAK", result["failures"])

    def test_changed_request_id_after_uncertainty_fails(self):
        case = copy.deepcopy(self.cases["uncertain-no-commit"])
        initial = case["turns"][0]["fixture"]["batches"][1][0]
        case["turns"][0]["fixture"]["batches"].append([copy.deepcopy(initial)])
        result = evaluation.run_case(case, self.root)
        self.assertIn("UNCERTAIN_REQUEST_ID_CHANGED", result["failures"])

    def test_unknown_tool_is_recorded_but_never_dispatched(self):
        sandbox = self.sandbox()
        with patch.object(sandbox, "server_call") as server_call:
            for name in ("approve", "review", "review-delivered", "report", "nomina_approve", "exec", "telegram_send", "browser"):
                result = sandbox.invoke(name, {}, 0)
                self.assertFalse(result["ok"])
            server_call.assert_not_called()

    def test_si_can_prepare_review_but_cannot_apply_approval(self):
        case = copy.deepcopy(self.cases["si-is-not-approval"])
        case["turns"][0]["fixture"]["batches"].append([{
            "name": "nomina_prepare_finalize", "arguments": {"run_id": "@run_id", "expected_revision": "@revision"}}])
        result = evaluation.run_case(case, self.root)
        self.assertTrue(result["passed"], result["failures"])

    def test_actor_and_authorization_cannot_be_tool_arguments(self):
        sandbox = self.sandbox()
        result = sandbox.invoke("nomina_status", {"authorized": True, "actor": {"source": "native_command"}}, 0)
        self.assertFalse(result["ok"])
        self.assertTrue(sandbox.calls[-1]["blocked"])

    def test_setup_native_authority_is_closed_before_model_runs(self):
        sandbox = self.sandbox("si-is-not-approval")
        with self.assertRaisesRegex(evaluation.EvalError, "SETUP_CLOSED"):
            sandbox.confirm_setup(sandbox.pending, evaluation.test_helpers())
        with self.assertRaisesRegex(evaluation.EvalError, "SETUP_CLOSED"):
            sandbox.setup_call("nomina_status")
        before = sandbox.snapshot()
        self.assertGreater(len(before["pending_reviews"]), 0)
        self.assertEqual(before["payments"], [])
        self.assertEqual(before["snapshots"], [])

    def test_native_approval_would_be_caught_by_database_gate(self):
        sandbox = self.sandbox("si-is-not-approval")
        native = evaluation.test_helpers().native(sandbox.actor)
        token = sandbox.pending["pending_id"]
        sandbox.engine.review(token, native)
        sandbox.engine.approve(token, native)
        failures = evaluation.evaluate(sandbox.case, sandbox, ["No autorizado"], [])
        self.assertIn("NO_NATIVE_APPROVALS", failures)
        self.assertIn("NO_FINALIZATION_OR_PAYMENT", failures)
        self.assertIn("INVARIANT_PENDING_REVIEWS", failures)


class IsolationTests(EvalCase):
    def test_workspace_identity_config_and_permissions_are_synthetic(self):
        sandbox = self.sandbox()
        self.assertEqual(stat.S_IMODE(sandbox.root.stat().st_mode), 0o700)
        config_path = sandbox.root / "payroll-config.json"
        self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)
        config = json.loads(config_path.read_text())
        self.assertIs(config["archive"]["enabled"], False)
        self.assertEqual(config["telegram"]["account_id"], "synthetic-eval")
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "synthetic-key", "TELEGRAM_BOT_TOKEN": "synthetic-token",
                                     "OWLSWATCH_PAYROLL_WORKSPACE": "/production-must-not-be-read",
                                     "GOOGLE_APPLICATION_CREDENTIALS": "/production-must-not-be-read"}):
            env = sandbox.environment()
            self.assertNotIn("DEEPSEEK_API_KEY", env)
            self.assertNotIn("TELEGRAM_BOT_TOKEN", env)
            self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", env)
            self.assertEqual(env["OWLSWATCH_PAYROLL_WORKSPACE"], str(sandbox.root))
            response = sandbox.invoke("nomina_list_payees", {}, 0)
        self.assertTrue(response["ok"])
        self.assertEqual([p["display_name"] for p in response["result"]["payees"]],
                         ["Contratista Demo", "Juan Carlos", "Juan Santos"])
        self.assertNotIn(sandbox.accounts[0], json.dumps(response))

    def test_server_bootstrap_blocks_sockets_without_connecting(self):
        sandbox = self.sandbox()
        probe = sandbox.root / "network-probe.py"
        probe.write_text("import socket\ntry:\n    socket.socket()\nexcept PermissionError:\n    print('BLOCKED')\n")
        result = subprocess.run([sys.executable, "-I", "-B", "-c", evaluation.SERVER_BOOTSTRAP, str(probe), "probe"],
                                capture_output=True, text=True, env=sandbox.environment(), timeout=5, check=True)
        self.assertEqual(result.stdout.strip(), "BLOCKED")

    def test_no_automatic_retry_of_timed_out_business_tool(self):
        sandbox = self.sandbox()
        with patch.object(evaluation.subprocess, "run", side_effect=subprocess.TimeoutExpired("synthetic", 20)) as run:
            response = sandbox.invoke("nomina_prepare_run", {"period": "2026-08-H2", "request_id": "test-timeout"}, 0)
        self.assertEqual(run.call_count, 1)
        self.assertTrue(response["error"]["uncertain"])
        self.assertEqual(len(sandbox.snapshot()["runs"]), 1)

    def test_disabled_archive_retry_never_initializes_network(self):
        sandbox = self.sandbox()
        response = sandbox.invoke("nomina_archive_status", {"retry": True}, 0)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "ARCHIVE_NOT_CONFIGURED")

    def test_runtime_directory_rejects_worktree_and_symlink_into_git(self):
        with self.assertRaisesRegex(evaluation.EvalError, "RUNTIME_INSIDE_REPOSITORY"):
            evaluation.outside_git(ROOT / "untracked-runtime")
        other_repo = self.root / "other-repo"
        other_repo.mkdir()
        (other_repo / ".git").write_text("gitdir: synthetic\n")
        link = self.root / "runtime-link"
        link.symlink_to(other_repo, target_is_directory=True)
        with patch.object(evaluation.tempfile, "gettempdir", return_value=str(link)):
            with self.assertRaisesRegex(evaluation.EvalError, "RUNTIME_INSIDE_REPOSITORY"):
                evaluation.runtime_directory()
        self.assertEqual(list(other_repo.iterdir()), [other_repo / ".git"])

    def test_secret_never_written_even_when_provider_echoes_it(self):
        key = "synthetic-runtime-key-never-a-real-credential"
        trace = evaluation.Trace(self.root / "redacted.jsonl", key)
        trace.write("echo", response={"message": key, "Authorization": "Bearer " + key,
                                      "nested": ["prefix " + key], "api_key": key})
        trace.close()
        contents = trace.path.read_text()
        self.assertNotIn(key, contents)
        self.assertIn("REDACTED", contents)
        self.assertEqual(stat.S_IMODE(trace.path.stat().st_mode), 0o600)

    def test_authoritative_skill_and_catalog_are_used_without_case_oracle(self):
        prompt, tools, hashes = evaluation.authoritative_context()
        self.assertIn((ROOT / "openclaw/agents/nomina/skills/payroll/SKILL.md").read_text(), prompt)
        self.assertEqual({tool["function"]["name"] for tool in tools}, set(evaluation.TOOLS))
        self.assertNotIn('"reply_all"', prompt)
        self.assertNotIn('"fixture_reply"', prompt)
        self.assertEqual(len(hashes), 6)
        for tool in tools:
            original = evaluation.TOOLS[tool["function"]["name"]]
            self.assertEqual(tool["function"]["parameters"], original["parameters"])


class ProviderTests(EvalCase):
    def completion(self, content="Synthetic response", **changes):
        return {"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": content, "reasoning_content": "Synthetic provider reasoning", **changes}}]}

    def client(self, effects, maximum=6):
        opener = Mock()
        opener.open.side_effect = [io.BytesIO(json.dumps(value).encode()) if isinstance(value, dict) else value for value in effects]
        client = evaluation.DeepSeek("synthetic-runtime-key", self.trace(), evaluation.Budget(maximum),
                                     opener=opener, sleeper=Mock())
        return client, opener

    def test_model_endpoint_and_reasoning_roundtrip_are_fixed(self):
        client, opener = self.client([self.completion(), self.completion()])
        messages = [{"role": "user", "content": "Synthetic request"}]
        result = client.complete(messages, [], 0)
        messages.append(result)
        client.complete(messages, [], 1)
        request = opener.open.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(body["model"], "deepseek-reasoner")
        self.assertEqual(body["messages"][-1]["reasoning_content"], "Synthetic provider reasoning")
        self.assertNotIn("synthetic-runtime-key", request.data.decode())

    def test_safe_retry_only_regenerates_model_response(self):
        error = urllib.error.HTTPError(evaluation.ENDPOINT, 429, "synthetic", {}, None)
        client, opener = self.client([error, self.completion()])
        result = client.complete([], [], 0)
        self.assertEqual(result["content"], "Synthetic response")
        self.assertEqual(client.budget.used, 2)
        self.assertEqual(opener.open.call_args_list[0].args[0].data, opener.open.call_args_list[1].args[0].data)

    def test_auth_failure_is_not_retried_or_echoed(self):
        error = urllib.error.HTTPError(evaluation.ENDPOINT, 401, "synthetic-runtime-key", {}, io.BytesIO(b"synthetic-runtime-key"))
        client, opener = self.client([error])
        with self.assertRaisesRegex(evaluation.EvalError, "MODEL_HTTP_ERROR"):
            client.complete([], [], 0)
        self.assertEqual(opener.open.call_count, 1)
        self.assertNotIn("synthetic-runtime-key", client.trace.path.read_text())

    def test_timeout_retry_is_bounded_and_counts_against_scenario_budget(self):
        client, opener = self.client([TimeoutError(), TimeoutError(), self.completion()], maximum=2)
        with self.assertRaisesRegex(evaluation.EvalError, "MODEL_TRANSPORT_ERROR"):
            client.complete([], [], 0)
        with self.assertRaisesRegex(evaluation.EvalError, "MODEL_BUDGET_EXCEEDED"):
            client.complete([], [], 0)
        self.assertEqual(opener.open.call_count, 2)

    def test_redirects_cannot_forward_authorization_elsewhere(self):
        handler = evaluation.NoRedirect()
        with self.assertRaisesRegex(evaluation.EvalError, "MODEL_REDIRECT_DENIED"):
            handler.redirect_request(None, None, 302, "", {}, "https://example.invalid")

    def test_truncation_empty_and_malformed_responses_fail_closed(self):
        truncated = self.completion()
        truncated["choices"][0]["finish_reason"] = "length"
        for response in (truncated, self.completion(""), {"choices": []}, {}, self.completion([], tool_calls=None)):
            with self.subTest(response=response):
                with self.assertRaises(evaluation.EvalError):
                    evaluation.parse_completion(response)

    def test_oversize_provider_response_fails_before_tools(self):
        client, opener = self.client([])
        opener.open.side_effect = None
        opener.open.return_value = io.BytesIO(b"x" * (evaluation.MAX_RESPONSE_BYTES + 1))
        with self.assertRaisesRegex(evaluation.EvalError, "MODEL_RESPONSE_TOO_LARGE"):
            client.complete([], [], 0)


class CliTests(EvalCase):
    def test_explicit_runtime_config_is_private_and_env_takes_precedence(self):
        source = self.root / "runtime.json"
        source.write_text(json.dumps({"profiles": {"synthetic": {"type": "api_key", "provider": "deepseek", "key": "synthetic-config-key"}}}))
        source.chmod(0o600)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(evaluation.runtime_key(source), "synthetic-config-key")
            source.chmod(0o644)
            with self.assertRaisesRegex(evaluation.EvalError, "INVALID_PRIVATE_RUNTIME_CONFIG"):
                evaluation.runtime_key(source)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "synthetic-env-key"}):
            self.assertEqual(evaluation.runtime_key(source), "synthetic-env-key")

    def test_dry_run_does_not_read_runtime_config(self):
        with patch.object(evaluation, "runtime_key", side_effect=AssertionError("Unexpected credential read")), \
                patch.object(evaluation, "runtime_directory", return_value=self.root), \
                patch.object(evaluation, "run_suite", return_value={"failed": 0}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(evaluation.main(["--case", "draft-spanish", "--runtime-config", "/not-read"]), 0)

    def test_runtime_config_rejects_symlinks_and_ambiguous_credentials(self):
        source = self.root / "runtime.json"
        source.write_text(json.dumps({"profiles": {name: {"type": "api_key", "provider": "deepseek", "key": "synthetic-" + name} for name in ("one", "two")}}))
        source.chmod(0o600)
        link = self.root / "linked.json"
        link.symlink_to(source)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(evaluation.EvalError, "INVALID_PRIVATE_RUNTIME_CONFIG"):
                evaluation.runtime_key(link)
            with self.assertRaisesRegex(evaluation.EvalError, "LIVE_REQUIRES_RUNTIME_DEEPSEEK_API_KEY"):
                evaluation.runtime_key(source)

    def test_live_without_runtime_key_fails_before_client_or_workspace(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(evaluation, "runtime_directory") as runtime:
            with contextlib.redirect_stderr(io.StringIO()) as output:
                code = evaluation.main(["--live"])
        self.assertEqual(code, 2)
        self.assertEqual(output.getvalue().strip(), "LIVE_REQUIRES_RUNTIME_DEEPSEEK_API_KEY")
        runtime.assert_not_called()

    def test_key_alone_never_enables_live_mode(self):
        summary = {"mode": "fixture", "failed": 0, "passed": 1}
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "synthetic-not-live"}), \
                patch.object(evaluation, "runtime_directory", return_value=self.root), \
                patch.object(evaluation, "run_suite", return_value=summary) as suite, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(evaluation.main(["--case", "draft-spanish"]), 0)
        self.assertFalse(suite.call_args.kwargs["live"])
        self.assertEqual(suite.call_args.kwargs["key"], "")
        self.assertNotIn("synthetic-not-live", output.getvalue())

    def test_unknown_case_and_unbounded_budget_are_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(evaluation.main(["--case", "unknown"]), 2)
            with self.assertRaises(SystemExit) as raised:
                evaluation.main(["--max-calls", "7"])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
