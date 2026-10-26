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

    def test_cotiza_24h_timeout_cannot_become_recovery_without_new_success(self):
        failure = self.write_failure()
        self.state_path.write_text(json.dumps(self.alerted(["agent_blocked:cotiza:provider_timeout"])))
        before = self.run_sample(self.NOW - 120)
        self.assertFalse(before["runtime"]["agents"]["cotiza"]["staleEvidence"])
        after = self.run_sample(self.NOW)
        self.assertTrue(after["runtime"]["agents"]["cotiza"]["staleEvidence"])
        self.assertEqual(after["health"], "unhealthy")
        self.assertIsNone(after["alertKind"])
        self.assertEqual(self.messages, [])
        # Both telemetry files can rotate out without clearing the incident.
        (self.root / "health/runs.jsonl").write_text("")
        self.assertIn("agent_blocked:cotiza:provider_timeout", self.run_sample(self.NOW + 120)["reasons"])
        successful = {**failure, "at": "2026-10-25T23:06:59Z", "runKey": "b" * 24, "success": True, "code": None}
        (self.root / "health/runs.jsonl").write_text(json.dumps(successful) + "\n")
        self.assertEqual(self.run_sample(self.NOW + 240)["health"], "recovering")
        self.assertIsNone(self.run_sample(self.NOW + 360)["alertKind"])
        recovered = self.run_sample(self.NOW + 480)
        self.assertEqual(recovered["alertKind"], "recovery")
        self.assertEqual(recovered["recoveryEvidence"]["newSuccessfulRuns"], ["cotiza"])
        self.assertFalse(recovered["recoveryEvidence"]["businessCompletionVerified"])
        self.assertNotIn("recuperado", self.messages[-1])

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
        self.assertEqual(result["recoveryEvidence"]["operatorDispositions"], ["cotiza"])
        self.assertEqual(result["recoveryEvidence"]["newSuccessfulRuns"], [])
        self.assertFalse(result["runtime"]["agents"]["cotiza"]["success"])
        self.assertIn("Incidencia cerrada por el operador", self.messages[-1])

    def test_legacy_outage_with_missing_telemetry_is_retained_on_upgrade(self):
        self.state_path.write_text(json.dumps(self.alerted(["agent_blocked:cotiza:provider_timeout"])))
        for offset in (0, 120, 240):
            result = self.run_sample(self.NOW + offset)
        self.assertEqual(result["health"], "unhealthy")
        self.assertIsNone(result["alertKind"])
        self.assertEqual(self.messages, [])

    def test_missing_runtime_module_cannot_erase_legacy_incident(self):
        self.state_path.write_text(json.dumps(self.alerted(["agent_blocked:cotiza:provider_timeout"])))
        with patch.object(observer, "__file__", str(self.root / "observe-telegram-channel.py")):
            unavailable = self.run_sample(self.NOW)
        self.assertIn("runtime_health_unavailable", unavailable["reasons"])
        for offset in (120, 240, 360):
            result = self.run_sample(self.NOW + offset)
        self.assertEqual(result["health"], "unhealthy")
        self.assertIn("agent_blocked:cotiza:provider_timeout", result["reasons"])
        self.assertEqual(self.messages, [])

    def test_legacy_failure_cannot_be_closed_by_an_older_success(self):
        self.state_path.write_text(json.dumps(self.alerted(["agent_blocked:cotiza:provider_timeout"])))
        (self.root / "health/runs.jsonl").write_text(json.dumps({"kind": "run_end", "agent": "cotiza",
            "runKey": "c" * 24, "at": "2026-10-23T23:02:02Z", "success": True}) + "\n")
        self.assertIn("agent_blocked:cotiza:provider_timeout", self.run_sample(self.NOW)["reasons"])

    def test_world_readable_disposition_does_not_close_incident(self):
        self.write_failure()
        first = self.run_sample(self.NOW)
        path = self.root / "health/run-dispositions.json"
        path.write_text(json.dumps({"schemaVersion": 1, "dispositions": [{"agent": "cotiza",
                        "evidenceId": first["runtime"]["agents"]["cotiza"]["evidenceId"],
                        "resolution": "resolved_externally", "recordedAt": "2026-10-25T23:02:59Z"}]}))
        path.chmod(0o644)
        self.assertIn("agent_blocked:cotiza:provider_timeout", self.run_sample(self.NOW + 120)["reasons"])


if __name__ == "__main__":
    unittest.main()
