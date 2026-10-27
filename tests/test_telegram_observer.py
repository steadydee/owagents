import argparse
import contextlib
from datetime import datetime
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("telegram_observer", ROOT / "scripts/observe-telegram-channel.py")
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)

HEALTHY = {"channels": {"telegram": {"configured": True, "running": True, "probe": {"ok": True}}},
           "channelAccounts": {"telegram": [{"connected": True}]}}


class ObserverTests(unittest.TestCase):
    NOW = datetime.fromisoformat("2026-10-25T23:02:59+00:00").timestamp()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "health").mkdir()
        (self.root / "monitor").mkdir()
        self.state_path = self.root / "monitor/telegram-health-state.json"
        self.messages = []

    def alerted(self, reasons, now=None):
        now = self.NOW if now is None else now
        return {"health": "unhealthy", "outageStartedAtEpoch": now - 3600,
                "outageAlertSent": True, "lastAlertAtEpoch": now - 3600,
                "lastReasons": reasons, "outageReasons": reasons}

    def run_sample(self, now, status=HEALTHY):
        args = argparse.Namespace(profile="owlswatch", state_dir=str(self.root), dry_run=True, chat_id=None)
        if status is not None:
            status = {"observedAt": datetime.fromtimestamp(now).astimezone().isoformat(), **status}
        with patch.object(observer, "parse_args", return_value=args), \
             patch.object(observer, "get_status", return_value=(status, None)), \
             patch.object(observer, "resolve_alert_config", return_value=(None, None)), \
             patch.object(observer, "send_alert", side_effect=lambda _t, _c, text, _dry: self.messages.append(text) or True), \
             patch.object(observer.time, "time", return_value=now), contextlib.redirect_stdout(io.StringIO()):
            observer.main()
        return json.loads((self.root / "logs/telegram-health.jsonl").read_text().splitlines()[-1])

    def write_failure(self):
        failure = {"kind": "run_end", "agent": "cotiza", "runKey": "a" * 24,
                   "at": "2026-10-24T23:02:02.960Z", "success": False, "code": "provider_timeout"}
        (self.root / "health/runs.jsonl").write_text(json.dumps(failure) + "\n")
        return failure

    def test_channel_availability_and_unresolved_failure_are_independent(self):
        failure = self.write_failure()
        self.state_path.write_text(json.dumps(self.alerted(["agent_blocked:cotiza:provider_timeout"])))
        before = self.run_sample(self.NOW - 120)
        self.assertFalse(before["runtime"]["agents"]["cotiza"]["staleEvidence"])
        after = self.run_sample(self.NOW)
        self.assertTrue(after["runtime"]["agents"]["cotiza"]["staleEvidence"])
        self.assertEqual(after["health"], "healthy")
        self.assertEqual(after["taskHealth"]["state"], "pending_review")
        self.assertIsNone(after["alertKind"])
        self.assertEqual(self.messages, [])
        # Both telemetry files can rotate out without clearing the incident.
        (self.root / "health/runs.jsonl").write_text("")
        self.assertIn("task_pending_review:cotiza", self.run_sample(self.NOW + 120)["outcomeReasons"])
        successful = {**failure, "at": "2026-10-25T23:06:59Z", "runKey": "b" * 24, "success": True, "code": None}
        (self.root / "health/runs.jsonl").write_text(json.dumps(successful) + "\n")
        for offset in (240, 360, 480):
            sample = self.run_sample(self.NOW + offset)
            self.assertEqual(sample["health"], "healthy")
            self.assertEqual(sample["taskHealth"]["state"], "pending_review")
            self.assertIsNone(sample["alertKind"])
        self.assertEqual(self.messages, [])
        self.assertTrue(sample["runtime"]["agents"]["cotiza"]["success"])
        self.assertEqual(sample["taskHealth"]["pendingReviewCount"], 1)

    def test_flapping_resets_consecutive_health_and_preserves_outage(self):
        state = self.alerted(["telegram_not_connected"])
        for offset in (0, 120):
            state.update(observer.health_transition(state, [], self.NOW + offset))
            self.assertEqual(state["health"], "recovering")
            self.assertIsNone(state["alertKind"])
        started = state["outageStartedAtEpoch"]
        state.update(observer.health_transition(state, ["telegram_not_connected"], self.NOW + 240))
        self.assertEqual(state["consecutiveHealthy"], 0)
        self.assertEqual(state["outageStartedAtEpoch"], started)
        self.assertIsNone(state["alertKind"])  # no duplicate failure alert
        for offset in (360, 480):
            state.update(observer.health_transition(state, [], self.NOW + offset))
            self.assertIsNone(state["alertKind"])
        state.update(observer.health_transition(state, [], self.NOW + 600))
        self.assertEqual(state["alertKind"], "recovery")

    def test_sampling_gap_resets_recovery_and_brief_unalerted_outage_is_silent(self):
        state = self.alerted(["telegram_probe_failed"])
        for offset in (0, 120, 600):
            state.update(observer.health_transition(state, [], self.NOW + offset))
            self.assertIsNone(state["alertKind"])
        self.assertEqual(state["consecutiveHealthy"], 1)
        state = observer.health_transition({}, ["telegram_probe_failed"], self.NOW)
        for offset in (120, 240, 360):
            state.update(observer.health_transition(state, [], self.NOW + offset))
            self.assertIsNone(state["alertKind"])
        self.assertEqual(state["health"], "healthy")

    def test_transport_notice_never_claims_business_completion(self):
        self.state_path.write_text(json.dumps(self.alerted(["telegram_not_connected"])))
        for offset in (0, 120, 240):
            result = self.run_sample(self.NOW + offset)
        self.assertEqual(result["recoveryEvidence"]["scope"], "transport")
        self.assertEqual(result["recoveryEvidence"]["newSuccessfulRuns"], [])
        self.assertIn("Conexion de Telegram restablecida", self.messages[-1])
        self.assertIn("no confirma que las tareas pendientes", self.messages[-1])

    def test_operator_closure_remains_distinct_from_success(self):
        self.write_failure()
        self.state_path.write_text(json.dumps(self.alerted(["agent_blocked:cotiza:provider_timeout"])))
        first = self.run_sample(self.NOW)
        incident = first["runtime"]["agents"]["cotiza"]["evidenceId"]
        path = self.root / "health/run-dispositions.json"
        path.write_text(json.dumps({"schemaVersion": 1, "dispositions": [{"agent": "cotiza", "evidenceId": incident,
                        "resolution": "closed_without_retry", "recordedAt": "2026-10-25T23:02:59Z"}]}))
        path.chmod(0o600)
        for offset in (120, 240, 360):
            result = self.run_sample(self.NOW + offset)
        self.assertEqual(result["taskHealth"]["pendingReviewCount"], 0)
        self.assertEqual(result["taskHealth"]["state"], "unknown")
        self.assertEqual(result["taskHealth"]["resolvedEvidenceCount"], 1)
        self.assertFalse(result["runtime"]["agents"]["cotiza"]["success"])
        self.assertEqual(self.messages, [])

    def test_legacy_outage_with_missing_telemetry_is_retained_on_upgrade(self):
        self.state_path.write_text(json.dumps(self.alerted(["agent_blocked:cotiza:provider_timeout"])))
        for offset in (0, 120, 240):
            result = self.run_sample(self.NOW + offset)
        self.assertEqual(result["health"], "healthy")
        self.assertEqual(result["taskHealth"]["state"], "pending_review")
        self.assertIsNone(result["alertKind"])
        self.assertEqual(self.messages, [])

    def test_missing_runtime_module_cannot_erase_legacy_incident(self):
        self.state_path.write_text(json.dumps(self.alerted(["agent_blocked:cotiza:provider_timeout"])))
        with patch.object(observer, "__file__", str(self.root / "observe-telegram-channel.py")):
            unavailable = self.run_sample(self.NOW)
        self.assertEqual(unavailable["taskHealth"]["state"], "unknown")
        self.assertTrue(unavailable["taskHealth"]["evidenceUnavailable"])
        for offset in (120, 240, 360):
            result = self.run_sample(self.NOW + offset)
        self.assertEqual(result["health"], "healthy")
        self.assertEqual(result["taskHealth"]["state"], "pending_review")
        self.assertIn("task_pending_review:cotiza", result["outcomeReasons"])
        self.assertEqual(self.messages, [])

    def test_legacy_failure_cannot_be_closed_by_an_older_success(self):
        self.state_path.write_text(json.dumps(self.alerted(["agent_blocked:cotiza:provider_timeout"])))
        (self.root / "health/runs.jsonl").write_text(json.dumps({"kind": "run_end", "agent": "cotiza",
            "runKey": "c" * 24, "at": "2026-10-23T23:02:02Z", "success": True}) + "\n")
        self.assertIn("task_pending_review:cotiza", self.run_sample(self.NOW)["outcomeReasons"])

    def test_world_readable_disposition_does_not_close_incident(self):
        self.write_failure()
        first = self.run_sample(self.NOW)
        path = self.root / "health/run-dispositions.json"
        path.write_text(json.dumps({"schemaVersion": 1, "dispositions": [{"agent": "cotiza",
                        "evidenceId": first["runtime"]["agents"]["cotiza"]["evidenceId"],
                        "resolution": "resolved_externally", "recordedAt": "2026-10-25T23:02:59Z"}]}))
        path.chmod(0o644)
        self.assertIn("task_pending_review:cotiza", self.run_sample(self.NOW + 120)["outcomeReasons"])

    def test_stale_positive_and_negative_channel_snapshots_are_unknown(self):
        for connected in (True, False):
            status = {**HEALTHY, "observedAt": "2026-10-25T20:00:00Z",
                      "channelAccounts": {"telegram": [{"connected": connected}]}}
            result = self.run_sample(self.NOW, status)
            self.assertEqual(result["health"], "unknown")
            self.assertEqual(result["channelChecks"]["health"], "unknown")
            self.assertEqual(result["reasons"], ["status_evidence_stale"])
            self.assertIsNone(result["alertKind"])
        self.assertEqual(self.messages, [])

    def test_repeated_frozen_status_cannot_count_as_three_recovery_samples(self):
        self.state_path.write_text(json.dumps(self.alerted(["telegram_not_connected"])))
        status = {**HEALTHY, "ts": self.NOW * 1000, "observedAt": "2026-10-25T23:02:59+00:00"}
        first = self.run_sample(self.NOW, status)
        self.assertEqual(first["health"], "recovering")
        for offset in (120, 240):
            result = self.run_sample(self.NOW + offset, status)
            self.assertEqual(result["health"], "unknown")
            self.assertEqual(result["reasons"], ["status_evidence_repeated"])
            self.assertIsNone(result["alertKind"])
        self.assertEqual(self.messages, [])

    def test_missing_status_timestamp_and_command_failure_do_not_prove_outage(self):
        reasons, summary = observer.assess_health(HEALTHY, None, {"count": 0}, {}, int(self.NOW * 1000))
        self.assertEqual(reasons, ["status_evidence_missing"])
        self.assertEqual(summary["health"], "unknown")
        result = self.run_sample(self.NOW, None)
        self.assertEqual(result["health"], "unknown")
        self.assertIsNone(result["alertKind"])

    def test_task_resolution_evidence_never_uses_unrelated_latest_success(self):
        evidence = observer.recovery_evidence(["agent_blocked:cotiza:transport"], {"agents": {"cotiza": {"success": True}}})
        self.assertEqual(evidence["newSuccessfulRuns"], [])
        self.assertFalse(evidence["businessCompletionVerified"])

    def test_corrupt_retained_state_is_preserved_without_recovery_notice(self):
        for value in ("{broken", json.dumps({"runtimeEvidence": {"outcomeEvidence": [None]}})):
            self.state_path.write_text(value)
            args = argparse.Namespace(profile="owlswatch", state_dir=str(self.root), dry_run=True, chat_id=None)
            output = io.StringIO()
            with patch.object(observer, "parse_args", return_value=args), \
                 patch.object(observer, "get_status", return_value=({**HEALTHY, "ts": self.NOW * 1000}, None)), \
                 patch.object(observer, "send_alert") as send, \
                 patch.object(observer.time, "time", return_value=self.NOW), contextlib.redirect_stdout(output):
                code = observer.main()
            self.assertEqual(code, 2)
            self.assertEqual(self.state_path.read_text(), value)
            self.assertEqual(json.loads(output.getvalue())["health"], "unknown")
            send.assert_not_called()

    def test_observer_state_write_is_private_and_lock_blocks_parallel_writer(self):
        self.run_sample(self.NOW)
        self.assertEqual(self.state_path.stat().st_mode & 0o777, 0o600)
        before = self.state_path.read_bytes()
        with (self.root / "monitor/telegram-health.lock").open("a") as lock:
            observer.fcntl.flock(lock, observer.fcntl.LOCK_EX | observer.fcntl.LOCK_NB)
            args = argparse.Namespace(profile="owlswatch", state_dir=str(self.root), dry_run=True, chat_id=None)
            with patch.object(observer, "parse_args", return_value=args), patch.object(observer, "get_status") as probe, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(observer.main(), 2)
            probe.assert_not_called()
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_failed_atomic_replace_preserves_original_state(self):
        self.state_path.write_text('{"existing": true}')
        with patch.object(observer.os, "replace", side_effect=OSError("fixture")):
            with self.assertRaises(OSError):
                observer.atomic_write_json(self.state_path, {"new": True})
        self.assertEqual(self.state_path.read_text(), '{"existing": true}')
        self.assertEqual(list(self.state_path.parent.iterdir()), [self.state_path])

    def test_saved_report_validity_never_outlives_source_evidence(self):
        result = self.run_sample(self.NOW, {**HEALTHY, "observedAt": "2026-10-25T22:58:00+00:00"})
        self.assertEqual(result["health"], "healthy")
        self.assertEqual(result["validUntil"], "2026-10-25T23:03:00+00:00")
        self.assertEqual(result["validUntil"], result["channelChecks"]["validUntil"])


if __name__ == "__main__":
    unittest.main()
