import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

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
        balance = monitor.check_balance("owlswatch", {}, 100, failed)
        self.assertEqual(balance["status"], "check_failed")
        self.assertNotIn("balances", balance)
        def must_not_call(*args, **kwargs):
            raise AssertionError("cached check unexpectedly repeated")
        self.assertEqual(monitor.check_balance("owlswatch", {"balances": {"owlswatch": balance}}, 101, must_not_call), balance)

    def test_alerts_only_emit_on_transition(self):
        self.assertEqual(monitor.transition_alerts(["x"], ["x"], 123), [])
        self.assertEqual(monitor.transition_alerts(["x"], ["y"], 123), [
            {"at": 123, "state": "opened", "code": "y"}, {"at": 123, "state": "resolved", "code": "x"}])


if __name__ == "__main__":
    unittest.main()
