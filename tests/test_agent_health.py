import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime

spec = importlib.util.spec_from_file_location("health", Path(__file__).resolve().parents[1] / "scripts/agent-health.py")
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)


class HealthTests(unittest.TestCase):
    NOW = datetime.fromisoformat("2026-09-26T14:00:00+00:00").timestamp()

    def inspect(self, records, *, previous=(), circuit=None, retained=None, dispositions=None):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "health").mkdir()
            for filename, items in [("runs.jsonl", records), ("runs.jsonl.previous", previous)]:
                (root / "health" / filename).write_text("".join(json.dumps(item) + "\n" for item in items))
            if circuit is not None:
                (root / "health/provider-circuit.json").write_text(json.dumps(circuit))
            if dispositions is not None:
                path = root / "health/run-dispositions.json"
                path.write_text(json.dumps(dispositions))
                path.chmod(0o600)
            return health.inspect_runtime(root, self.NOW, previous=retained)

    def event(self, kind, **fields):
        return {"at": "2026-09-26T12:00:00Z", "kind": kind, "agent": "cobros", "runKey": "a" * 24, **fields}

    def test_reports_blocked_agent_even_with_live_gateway_and_omits_content(self):
        records = [self.event("run_end", success=False, code="context_limit", prompt="private")]
        result = self.inspect(records)
        self.assertIn("agent_blocked:cobros:context_limit", result["reasons"])
        self.assertNotIn("private", json.dumps(result))
        records.append(self.event("run_end", at="2026-09-26T13:00:00Z", runKey="b" * 24, success=True))
        self.assertEqual(self.inspect(records)["reasons"], [])

    def test_late_usage_revises_one_final_run_and_counts_actual_model_requests(self):
        records = [self.event("run_start")]
        records.extend(self.event("model_call", callKey=str(n) * 24, success=n != 2) for n in range(3))
        records += [self.event("run_end", revision=1, success=True, tokens=0),
                    self.event("usage", usageKey="f" * 24, usageScope="attempt", tokens=900),
                    self.event("run_end", revision=2, success=False, code="provider_credit", tokens=900, modelRequests=3, modelErrors=1)]
        result = self.inspect(records)
        self.assertEqual(result["last24Hours"], {"modelRequests": 3, "modelErrors": 1, "attemptsWithUsage": 1,
                                               "tokens": 900, "failedRuns": 1, "completedRuns": 0})
        self.assertFalse(result["agents"]["cobros"]["success"])
        self.assertEqual(result["agents"]["cobros"]["tokens"], 900)

    def test_rotation_duplicates_and_out_of_order_revisions_do_not_inflate_totals(self):
        earlier = self.event("run_end", success=False, code="transport", revision=1)
        later = self.event("run_end", success=True, revision=3)
        call = self.event("model_call", callKey="c" * 24, success=True)
        usage = self.event("usage", usageKey="e" * 24, tokens=88)
        result = self.inspect([later, call, usage, earlier], previous=[earlier, call, usage])
        self.assertEqual(result["last24Hours"]["modelRequests"], 1)
        self.assertEqual(result["last24Hours"]["tokens"], 88)
        self.assertEqual(result["last24Hours"]["completedRuns"], 1)
        self.assertEqual(result["last24Hours"]["failedRuns"], 0)
        self.assertEqual(result["reasons"], [])

    def test_recovered_retry_is_one_run_but_keeps_all_attempt_usage(self):
        records = [self.event("run_end", revision=1, success=False, code="transport"),
                   self.event("usage", usageKey="d" * 24, tokens=10),
                   self.event("usage", usageKey="e" * 24, tokens=20),
                   self.event("run_end", revision=3, success=True, tokens=30)]
        result = self.inspect(records)
        self.assertEqual(result["last24Hours"]["completedRuns"], 1)
        self.assertEqual(result["last24Hours"]["failedRuns"], 0)
        self.assertEqual(result["last24Hours"]["attemptsWithUsage"], 2)
        self.assertEqual(result["last24Hours"]["tokens"], 30)
        self.assertEqual(result["last24Hours"]["modelRequests"], 0)

    def test_old_failure_stays_unresolved_outside_usage_window(self):
        old = self.event("run_end", at="2026-09-24T12:00:00Z", success=False, code="transport")
        result = self.inspect([old])
        self.assertFalse(result["agents"]["cobros"]["success"])
        self.assertEqual(result["last24Hours"]["failedRuns"], 0)
        self.assertEqual(result["reasons"], ["agent_blocked:cobros:transport"])
        self.assertTrue(result["agents"]["cobros"]["staleEvidence"])
        self.assertEqual(self.inspect([old], circuit={"until": (self.NOW + 600) * 1000})["reasons"], ["provider_credit", "agent_blocked:cobros:transport"])

    def test_invalid_telemetry_is_safe_and_never_echoes_content(self):
        records = [None, [], "private", {}, self.event("usage", tokens="private"), self.event("usage", tokens=-4),
                   self.event("run_end", agent="private@example.com", success=False, code="private error", calls="NaN", tokens={"private": 1}),
                   self.event("run_end", at="2100-01-01T00:00:00Z", success=True),
                   self.event("run_end", at="2026-09-26T12:00:00", success=True)]
        result = self.inspect(records, circuit=[])
        self.assertNotIn("private", json.dumps(result))
        self.assertEqual(result["agents"]["unknown"]["code"], "run_failed")
        self.assertEqual(result["last24Hours"]["tokens"], 0)
        self.assertEqual(result["last24Hours"]["failedRuns"], 1)

    def test_end_time_not_late_snapshot_time_defines_24h_window(self):
        record = self.event("run_end", endedAt="2026-09-24T12:00:00Z", success=False, code="context_limit")
        result = self.inspect([record])
        self.assertEqual(result["last24Hours"]["failedRuns"], 0)
        self.assertEqual(result["reasons"], ["agent_blocked:cobros:context_limit"])

    def test_legacy_usage_does_not_masquerade_as_model_request_count(self):
        usage = self.event("usage", tokens=77)
        usage.pop("runKey")
        result = self.inspect([usage])
        self.assertEqual(result["last24Hours"]["modelRequests"], 0)
        self.assertEqual(result["last24Hours"]["tokens"], 77)
        self.assertEqual(result["last24Hours"]["attemptsWithUsage"], 1)

    def test_unfinished_run_reports_age_and_only_warns_after_fifteen_minutes(self):
        recent = self.event("run_start", at="2026-09-26T13:55:00Z")
        result = self.inspect([recent])
        self.assertTrue(result["instrumented"])
        self.assertEqual(result["unfinishedRuns"]["cobros"]["currentRunAgeSeconds"], 300)
        self.assertFalse(result["unfinishedRuns"]["cobros"]["staleRunning"])
        self.assertEqual(result["reasons"], [])
        stale = self.event("run_start", at="2026-09-26T13:00:00Z")
        result = self.inspect([stale])
        self.assertTrue(result["unfinishedRuns"]["cobros"]["staleRunning"])
        self.assertIn("agent_unfinished:cobros", result["reasons"])

    def test_only_completed_run_supersedes_abandoned_start_and_old_starts_persist(self):
        start = self.event("run_start")
        end = self.event("run_end", at="2026-09-26T12:10:00Z", success=True)
        self.assertEqual(self.inspect([start, end])["unfinishedRuns"], {})
        end["runKey"] = "b" * 24
        self.assertEqual(self.inspect([start, end])["unfinishedRuns"], {})
        new = self.event("run_start", runKey="c" * 24, at="2026-09-26T13:55:00Z")
        result = self.inspect([start, new])
        self.assertEqual(result["unfinishedRuns"]["cobros"]["currentRunAgeSeconds"], 7200)
        self.assertEqual(result["reasons"], ["agent_unfinished:cobros"])
        old = self.event("run_start", at="2026-09-24T12:00:00Z")
        self.assertTrue(self.inspect([old])["unfinishedRuns"]["cobros"]["staleEvidence"])

    def test_failure_survives_rotation_and_only_newer_success_resolves_it(self):
        retained = self.inspect([self.event("run_end", success=False, code="provider_timeout")])
        result = self.inspect([], retained=retained)
        self.assertEqual(result["reasons"], ["agent_blocked:cobros:provider_timeout"])
        self.assertEqual(result["last24Hours"]["failedRuns"], 0)  # not counted again from retained evidence
        older = self.event("run_end", at="2026-09-25T12:00:00Z", success=True, runKey="b" * 24)
        self.assertEqual(self.inspect([older], retained=retained)["reasons"], result["reasons"])
        newer = self.event("run_end", at="2026-09-26T13:00:00Z", success=True, runKey="c" * 24)
        self.assertEqual(self.inspect([newer], retained=retained)["reasons"], [])

    def test_operator_disposition_is_exact_incident_bound_and_not_a_success(self):
        failed = self.event("run_end", success=False, code="provider_timeout")
        incident = self.inspect([failed])["agents"]["cobros"]["evidenceId"]
        entry = {"agent": "cobros", "evidenceId": incident, "resolution": "closed_without_retry", "recordedAt": "2026-09-26T13:00:00Z"}
        def check(value):
            return self.inspect([failed], dispositions={"schemaVersion": 1, "dispositions": [value]})
        result = check(entry)
        self.assertEqual(result["reasons"], [])
        self.assertFalse(result["agents"]["cobros"]["success"])
        self.assertEqual(result["agents"]["cobros"]["evidenceState"], "operator_disposition")
        for changes in ({"agent": "cotiza"}, {"evidenceId": "b" * 24}, {"resolution": []},
                        {"recordedAt": "2026-09-26T11:00:00Z"}, {"recordedAt": "2026-09-27T11:00:00Z"}):
            self.assertTrue(check({**entry, **changes})["reasons"])
        new_failure = {**failed, "at": "2026-09-26T13:30:00Z", "runKey": "b" * 24}
        self.assertTrue(self.inspect([new_failure], dispositions={"schemaVersion": 1, "dispositions": [entry]})["reasons"])

    def test_unfinished_evidence_survives_rotation(self):
        retained = self.inspect([self.event("run_start")])
        result = self.inspect([], retained=retained)
        self.assertEqual(result["reasons"], ["agent_unfinished:cobros"])

    def test_new_start_with_same_key_is_not_recovery_without_an_end(self):
        retained = self.inspect([self.event("run_start")])
        new = self.event("run_start", at="2026-09-26T13:55:00Z")
        result = self.inspect([new], retained=retained)
        self.assertEqual(result["reasons"], ["agent_unfinished:cobros"])
        self.assertEqual(result["unfinishedRuns"]["cobros"]["currentRunAgeSeconds"], 7200)

    def test_operator_closing_old_start_does_not_hide_new_unfinished_attempt(self):
        old = self.event("run_start", at="2026-09-25T12:00:00Z")
        incident = self.inspect([old])["unfinishedRuns"]["cobros"]["evidenceId"]
        entries = {"schemaVersion": 1, "dispositions": [{"agent": "cobros", "evidenceId": incident,
                   "resolution": "resolved_externally", "recordedAt": "2026-09-26T11:00:00Z"}]}
        newer = self.event("run_start", at="2026-09-26T13:00:00Z", runKey="b" * 24)
        result = self.inspect([old, newer], dispositions=entries)
        self.assertEqual(result["reasons"], ["agent_unfinished:cobros"])
        self.assertEqual(result["unfinishedRuns"]["cobros"]["runKey"], "b" * 24)

    def test_retry_start_after_previous_end_is_still_unfinished(self):
        result = self.inspect([self.event("run_end", success=False, code="transport"),
                               self.event("run_start", at="2026-09-26T13:50:00Z")])
        self.assertEqual(result["unfinishedRuns"]["cobros"]["currentRunAgeSeconds"], 600)


if __name__ == "__main__":
    unittest.main()
