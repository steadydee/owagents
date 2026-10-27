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
        return {"at": "2026-09-26T12:00:00Z", "kind": kind, "agent": "cobros", "runKey": "a" * 24, "revision": 1, **fields}

    def test_unrelated_success_never_clears_original_failure(self):
        records = [self.event("run_end", success=False, code="context_limit", prompt="private")]
        result = self.inspect(records)
        self.assertIn("agent_blocked:cobros:context_limit", result["reasons"])
        self.assertNotIn("private", json.dumps(result))
        records.append(self.event("run_end", at="2026-09-26T13:59:00Z", runKey="b" * 24, success=True, responseCompleted=True))
        result = self.inspect(records)
        self.assertEqual(result["reasons"], ["agent_blocked:cobros:context_limit"])
        self.assertTrue(result["agents"]["cobros"]["success"])
        self.assertEqual(result["availability"]["cobros"]["state"], "response_observed")
        self.assertEqual(result["taskHealth"]["pendingReviewCount"], 1)

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
        later = self.event("run_end", success=True, responseCompleted=True, revision=3)
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
                   self.event("run_end", revision=3, success=True, responseCompleted=True, tokens=30)]
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
        self.assertEqual(self.inspect([old], circuit={"until": (self.NOW + 600) * 1000})["reasons"], ["agent_blocked:cobros:transport", "provider_credit"])

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

    def test_only_matching_end_supersedes_abandoned_start_and_old_starts_persist(self):
        start = self.event("run_start")
        end = self.event("run_end", at="2026-09-26T12:10:00Z", success=True)
        self.assertEqual(self.inspect([start, end])["unfinishedRuns"], {})
        end["runKey"] = "b" * 24
        self.assertIn("cobros", self.inspect([start, end])["unfinishedRuns"])
        new = self.event("run_start", runKey="c" * 24, at="2026-09-26T13:55:00Z")
        result = self.inspect([start, new])
        self.assertEqual(result["unfinishedRuns"]["cobros"]["currentRunAgeSeconds"], 7200)
        self.assertEqual(result["reasons"], ["agent_unfinished:cobros"])
        old = self.event("run_start", at="2026-09-24T12:00:00Z")
        self.assertTrue(self.inspect([old])["unfinishedRuns"]["cobros"]["staleEvidence"])

    def test_failure_survives_rotation_and_unrelated_success(self):
        retained = self.inspect([self.event("run_end", success=False, code="provider_timeout")])
        result = self.inspect([], retained=retained)
        self.assertEqual(result["reasons"], ["agent_blocked:cobros:provider_timeout"])
        self.assertEqual(result["last24Hours"]["failedRuns"], 0)  # not counted again from retained evidence
        older = self.event("run_end", at="2026-09-25T12:00:00Z", success=True, runKey="b" * 24)
        self.assertEqual(self.inspect([older], retained=retained)["reasons"], result["reasons"])
        newer = self.event("run_end", at="2026-09-26T13:00:00Z", success=True, runKey="c" * 24)
        result = self.inspect([newer], retained=retained)
        self.assertEqual(result["reasons"], ["agent_blocked:cobros:provider_timeout"])
        self.assertEqual(self.inspect([], retained=result)["taskHealth"]["pendingReviewCount"], 1)
        newer.update(runKey="a" * 24, revision=2, responseCompleted=True)
        resolved = self.inspect([newer], retained=retained)
        self.assertEqual(resolved["reasons"], [])
        self.assertEqual(resolved["outcomeEvidence"][0]["resolution"]["kind"], "same_run_response")
        self.assertEqual(self.inspect([], retained=resolved)["taskHealth"]["pendingReviewCount"], 0)
        self.assertFalse(resolved["taskHealth"]["businessCompletionVerified"])

    def test_operator_disposition_is_exact_incident_bound_and_not_a_success(self):
        failed = self.event("run_end", success=False, code="provider_timeout")
        incident = self.inspect([failed])["agents"]["cobros"]["evidenceId"]
        entry = {"agent": "cobros", "evidenceId": incident, "resolution": "closed_without_retry", "recordedAt": "2026-09-26T13:00:00Z"}
        def check(value):
            return self.inspect([failed], dispositions={"schemaVersion": 1, "dispositions": [value]})
        result = check(entry)
        self.assertEqual(result["reasons"], [])
        self.assertFalse(result["agents"]["cobros"]["success"])
        self.assertEqual(result["agents"]["cobros"]["evidenceState"], "resolved")
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

    def test_response_receipt_requires_same_agent_run_new_revision_and_completed_text(self):
        failure = self.event("run_end", success=False, code="transport", revision=2)
        receipt = self.event("run_end", at="2026-09-26T13:59:00Z", success=True, responseCompleted=True, revision=3)
        for changes in ({"responseCompleted": False}, {"responseCompleted": None}, {"revision": 2},
                        {"runKey": "b" * 24}, {"agent": "cotiza"}):
            with self.subTest(changes=changes):
                self.assertEqual(self.inspect([failure, {**receipt, **changes}])["taskHealth"]["pendingReviewCount"], 1)
        resolved = self.inspect([receipt, failure])  # out-of-order journal preserves and resolves original evidence
        self.assertEqual(resolved["taskHealth"]["pendingReviewCount"], 0)
        self.assertEqual(resolved["taskHealth"]["resolvedEvidenceCount"], 1)
        self.assertEqual(resolved["availability"]["cobros"]["state"], "response_observed")
        self.assertFalse(resolved["availability"]["cobros"]["deliveryVerified"])

    def test_artifact_receipt_does_not_claim_whole_request_completion(self):
        failure = self.event("run_end", success=False, code="transport")
        receipt = self.event("business_outcome", at="2026-09-26T13:59:00Z", outcome="quote_draft_created", taskKey="d" * 24)
        result = self.inspect([failure, receipt])
        self.assertEqual(result["taskHealth"]["pendingReviewCount"], 1)
        self.assertEqual(result["availability"]["cobros"]["state"], "unknown")

    def test_old_positive_and_negative_evidence_have_unknown_availability(self):
        for success in (True, False):
            result = self.inspect([self.event("run_end", success=success, responseCompleted=success)])
            self.assertEqual(result["availability"]["cobros"]["state"], "unknown")
        fresh = self.inspect([self.event("run_end", at="2026-09-26T13:59:00Z", success=False, code="transport")])
        self.assertEqual(fresh["availability"]["cobros"]["state"], "failure_observed")

    def test_all_unfinished_runs_survive_rotation_even_with_newer_canary(self):
        starts = [self.event("run_start", runKey=key * 24) for key in ("a", "b")]
        retained = self.inspect(starts)
        result = self.inspect([self.event("run_end", runKey="c" * 24, success=True)], retained=retained)
        self.assertEqual(result["taskHealth"]["pendingReviewCount"], 2)
        result = self.inspect([self.event("run_end", runKey="a" * 24, at="2026-09-26T13:00:00Z", success=True)], retained=result)
        self.assertEqual(result["taskHealth"]["pendingReviewCount"], 1)
        self.assertEqual(result["unfinishedRuns"]["cobros"]["runKey"], "b" * 24)

    def test_cached_outcome_fields_are_allowlisted(self):
        retained = self.inspect([self.event("run_end", success=False, code="transport")])
        retained["outcomeEvidence"][0].update(prompt="private", resolution={"kind": "operator_disposition", "resolution": [], "at": "2026-09-26T13:00:00Z"})
        with self.assertRaisesRegex(ValueError, "^retained_outcome_evidence_invalid$"):
            self.inspect([], retained=retained)

    def test_persisted_ledger_validation_rejects_any_invalid_or_dropped_row(self):
        result = self.inspect([self.event("run_start"), self.event("run_end", success=False, code="transport")])
        self.assertTrue(health.validate_outcome_evidence(result["outcomeEvidence"], self.NOW))
        resolved = self.inspect([self.event("run_end", at="2026-09-26T13:59:00Z", success=True,
                              responseCompleted=True, revision=3)], retained=result)
        self.assertTrue(health.validate_outcome_evidence(resolved["outcomeEvidence"], self.NOW))
        row = next(entry for entry in result["outcomeEvidence"] if entry["kind"] == "failed_run")
        for rows in (None, [None], [*result["outcomeEvidence"], None], [row, row],
                     [{**row, "state": "resolved"}], [{**row, "evidenceId": "f" * 24}],
                     [{**row, "prompt": "private"}], [{**row, "revision": True}],
                     [{**row, "resolution": {"kind": "fabricated"}}]):
            with self.subTest(rows=rows):
                self.assertFalse(health.validate_outcome_evidence(rows, self.NOW))

    def test_exact_operator_disposition_survives_log_rotation(self):
        retained = self.inspect([self.event("run_end", success=False, code="transport")])
        entry = retained["outcomeEvidence"][0]
        result = self.inspect([], retained=retained, dispositions={"schemaVersion": 1, "dispositions": [{
            "agent": "cobros", "evidenceId": entry["evidenceId"], "resolution": "resolved_externally",
            "recordedAt": "2026-09-26T13:00:00Z"}]})
        self.assertTrue(health.validate_outcome_evidence(result["outcomeEvidence"], self.NOW))
        self.assertEqual(self.inspect([], retained=result)["taskHealth"]["pendingReviewCount"], 0)

    def test_corrupt_observer_state_cannot_be_silently_replaced_by_empty_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "monitor").mkdir()
            path = root / "monitor/telegram-health-state.json"
            for value in ("{broken", "[]", json.dumps({"runtimeEvidence": []})):
                path.write_text(value)
                with self.assertRaises(ValueError):
                    health.inspect_runtime(root, self.NOW)
                self.assertEqual(path.read_text(), value)

    def test_end_one_second_after_record_time_still_produces_roundtrippable_evidence(self):
        result = self.inspect([self.event("run_end", success=False, code="transport", endedAt="2026-09-26T12:00:01Z")])
        self.assertTrue(health.validate_outcome_evidence(result["outcomeEvidence"], self.NOW))
        self.assertEqual(self.inspect([], retained=result)["taskHealth"]["pendingReviewCount"], 1)

    def test_operator_resolution_before_late_snapshot_remains_valid(self):
        failure = self.event("run_end", at="2026-09-26T13:00:00Z", endedAt="2026-09-26T12:00:00Z", success=False, code="transport")
        retained = self.inspect([failure])
        incident = retained["outcomeEvidence"][0]["evidenceId"]
        result = self.inspect([failure], dispositions={"schemaVersion": 1, "dispositions": [{
            "agent": "cobros", "evidenceId": incident, "resolution": "resolved_externally", "recordedAt": "2026-09-26T12:30:00Z"}]})
        self.assertTrue(health.validate_outcome_evidence(result["outcomeEvidence"], self.NOW))
        self.assertEqual(self.inspect([], retained=result)["taskHealth"]["pendingReviewCount"], 0)


if __name__ == "__main__":
    unittest.main()
