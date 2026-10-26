import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("operations_observability", ROOT / "scripts/operations-observability.py")
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class ObservabilityTests(unittest.TestCase):
    def test_revisions_and_repeated_business_receipts_count_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "health").mkdir()
            base = {"at": "2026-10-17T10:00:00Z", "agent": "cuenta", "runKey": "a" * 24}
            rows = [{**base, "kind": "run_end", "success": True, "revision": 1, "durationMs": 2000},
                    {**base, "kind": "run_end", "success": False, "code": "transport", "revision": 2},
                    {**base, "kind": "usage", "usageKey": "b" * 24, "tokens": 1000, "input": 1000, "provider": "fixture", "model": "fixture"},
                    {**base, "kind": "business_outcome", "taskKey": "c" * 24, "outcome": "expense_draft_created"}]
            (root / "health/runs.jsonl").write_text("\n".join(json.dumps(row) for row in rows + [rows[-1], rows[-2]]))
            rates = {"fixture/fixture": {"low": {"input": 1}, "high": {"input": 2}}}
            result = monitor.report_profile(root, monitor.health.timestamp(base["at"]) + 60, 1, rates)["agents"]["cuenta"]
            self.assertEqual(result["completedResponses"], 0)
            self.assertEqual(result["failedRuns"], 1)
            self.assertEqual(result["verifiedBusinessOutcomes"], 1)
            self.assertEqual(result["usageAttempts"], 1)
            self.assertEqual(result["estimatedCostPerVerifiedOutcomeUsdRange"], [.001, .002])

    def test_response_latency_excludes_unobserved_and_failed_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "health").mkdir()
            base = {"at": "2026-10-17T10:00:00Z", "agent": "cuenta", "kind": "run_end", "success": True}
            rows = [
                {**base, "runKey": "a" * 24, "responseCompleted": True, "durationMs": 2000},
                {**base, "runKey": "b" * 24, "responseCompleted": True, "durationMs": 4000},
                {**base, "runKey": "c" * 24, "durationMs": 600000},
                {**base, "runKey": "d" * 24, "responseCompleted": False, "durationMs": 900000},
                {**base, "runKey": "e" * 24, "success": False, "code": "transport", "responseCompleted": True, "durationMs": 1000000},
            ]
            (root / "health/runs.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
            result = monitor.report_profile(root, monitor.health.timestamp(base["at"]) + 60, 1, {})["agents"]["cuenta"]
            self.assertEqual(result["completedRuns"], 4)
            self.assertEqual(result["completedResponses"], 2)
            self.assertEqual(result["failedRuns"], 1)
            self.assertEqual(result["responseP50Seconds"], 4)
            self.assertEqual(result["responseP95Seconds"], 4)

    def test_response_latency_is_unknown_without_observed_responses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "health").mkdir()
            base = {"at": "2026-10-17T10:00:00Z", "agent": "cuenta", "kind": "run_end", "success": True, "durationMs": 2000}
            rows = [{**base, "runKey": "a" * 24},
                    {**base, "runKey": "b" * 24, "responseCompleted": False}]
            (root / "health/runs.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
            result = monitor.report_profile(root, monitor.health.timestamp(base["at"]) + 60, 1, {})["agents"]["cuenta"]
            self.assertEqual(result["completedRuns"], 2)
            self.assertEqual(result["completedResponses"], 0)
            self.assertIsNone(result["responseP50Seconds"])
            self.assertIsNone(result["responseP95Seconds"])

    def test_unknown_prices_are_not_free(self):
        self.assertIsNone(monitor.estimate({"provider": "legacy", "model": "unknown", "tokens": 9999}, {}))

    def test_balance_errors_are_rate_limited_and_never_resolve_as_zero(self):
        def failed(*args, **kwargs):
            raise OSError("synthetic")
        with patch.object(monitor, "balance_runtime", return_value={"nodePath": "/fixture/node", "openclawPackage": "/fixture/sdk"}):
            balance = monitor.check_balance("owlswatch", {}, 100, failed)
        self.assertEqual(balance["status"], "check_failed")
        self.assertNotIn("balances", balance)
        def must_not_call(*args, **kwargs):
            raise AssertionError("cached check unexpectedly repeated")
        self.assertEqual(monitor.check_balance("owlswatch", {"balances": {"owlswatch": balance}}, 101, must_not_call), balance)

    def runtime_fixture(self, home, profile="hotel"):
        runtime = home / (".openclaw-" + profile) / "runtime"
        release = runtime / "releases/generation-one"
        node = release / "node/bin/node"
        sdk = release / "node_modules/openclaw"
        node.parent.mkdir(parents=True)
        node.write_text("fixture, never executed")
        node.chmod(0o700)
        (sdk / "dist/plugin-sdk").mkdir(parents=True)
        (sdk / "dist/plugin-sdk/provider-auth-runtime.js").touch()
        (sdk / "package.json").write_text(json.dumps({"name": "openclaw"}))
        record = {"schemaVersion": 1, "profile": profile, "nodePath": str(node), "openclawPackage": str(sdk)}
        path = runtime / "active.json"
        path.write_text(json.dumps(record))
        path.chmod(0o600)
        return path, record

    def test_balance_uses_exact_selected_node_and_sdk_without_global_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            _, record = self.runtime_fixture(home)
            def run(command, **options):
                self.assertEqual(command[0], record["nodePath"])
                self.assertEqual(command[2:], ["hotel", "--sdk-root", record["openclawPackage"]])
                self.assertEqual(options["env"]["HOME"], str(home))
                self.assertNotIn("NODE_OPTIONS", options["env"])
                self.assertNotIn("NODE_PATH", options["env"])
                self.assertEqual(options["timeout"], 15)
                return subprocess.CompletedProcess(command, 0, '{"status":"credential_unavailable"}')
            with patch.dict(os.environ, {"NODE_OPTIONS": "untrusted hook", "NODE_PATH": "other SDK"}):
                result = monitor.check_balance("hotel", {}, 100, run, home)
            self.assertEqual(result["status"], "credential_unavailable")

    def test_staged_only_runtime_never_selects_itself_or_global_default(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            path, _ = self.runtime_fixture(home)
            path.rename(path.with_name("activation-plan.json"))
            def forbidden(*args, **kwargs):
                self.fail("No process may run without explicit activation metadata")
            self.assertEqual(monitor.check_balance("hotel", {}, 100, forbidden, home)["status"], "runtime_unconfigured")

    def test_bad_or_cross_profile_runtime_metadata_fails_before_process_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            path, record = self.runtime_fixture(home)
            other_path, other = self.runtime_fixture(home, "nomina")
            cases = [{**record, "profile": "nomina"}, {**record, "nodePath": other["nodePath"]},
                     {**record, "openclawPackage": other["openclawPackage"]},
                     {**record, "nodePath": "node"}, {**record, "schemaVersion": 2}]
            for candidate in cases:
                path.write_text(json.dumps(candidate))
                with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                    monitor.balance_runtime("hotel", home)
            path.write_text(json.dumps(record))
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                monitor.balance_runtime("hotel", home)
            path.unlink()
            path.symlink_to(other_path)
            with self.assertRaises(ValueError):
                monitor.balance_runtime("hotel", home)

    def test_legacy_runtime_requires_explicit_selection_and_legacy_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            path, record = self.runtime_fixture(home)
            sdk = home / ".npm-global/lib/node_modules/openclaw"
            sdk.parent.mkdir(parents=True)
            Path(record["openclawPackage"]).rename(sdk)
            node = home / "legacy-node"
            Path(record["nodePath"]).rename(node)
            record.update(nodePath=str(node), openclawPackage=str(sdk))
            path.write_text(json.dumps(record))
            with self.assertRaises(ValueError):
                monitor.balance_runtime("hotel", home)
            record["legacy"] = True
            path.write_text(json.dumps(record))
            self.assertEqual(monitor.balance_runtime("hotel", home), {"nodePath": str(node), "openclawPackage": str(sdk)})

    def test_balance_helper_contract_with_fake_auth_and_http(self):
        result = subprocess.run(["node", "--test", str(ROOT / "tests/test_balance_helper.test.mjs")],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_alerts_only_emit_on_transition(self):
        self.assertEqual(monitor.transition_alerts(["x"], ["x"], 123), [])
        self.assertEqual(monitor.transition_alerts(["x"], ["y"], 123), [
            {"at": 123, "state": "opened", "code": "y"}, {"at": 123, "state": "resolved", "code": "x"}])


if __name__ == "__main__":
    unittest.main()
