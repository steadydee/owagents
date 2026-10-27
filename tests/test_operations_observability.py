import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
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
                self.assertEqual(command[2:], ["hotel", "--sdk-root", record["openclawPackage"],
                    "--budget-state", str(monitor.default_budget_state(home)), "--budget-python", sys.executable])
                self.assertEqual(options["env"]["HOME"], str(home))
                self.assertNotIn("NODE_OPTIONS", options["env"])
                self.assertNotIn("NODE_PATH", options["env"])
                self.assertEqual(options["timeout"], 20)
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
            other_path, other = self.runtime_fixture(home, "owlswatch")
            cases = [{**record, "profile": "owlswatch"}, {**record, "nodePath": other["nodePath"]},
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

    def test_budget_exhaustion_is_local_monitor_state_not_provider_failure(self):
        for status in ("budget_exhausted", "budget_unavailable", "budget_unconfigured"):
            with self.subTest(status=status):
                self.assertEqual(monitor.balance_alert("hotel", {"status": status}), "monitor:" + status)
        self.assertEqual(monitor.balance_alert("hotel", {"status": "provider_error"}), "hotel:balance_unverified")
        self.assertEqual(monitor.balance_alert("hotel", {"status": "ok", "available": False}), "hotel:provider_balance_low")
        self.assertIsNone(monitor.balance_alert("hotel", {"status": "ok", "available": True}))

    def test_budget_failure_is_preserved_and_cached_without_more_http(self):
        calls = []
        def blocked(command, **options):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, '{"status":"budget_exhausted"}')
        with patch.object(monitor, "balance_runtime", return_value={"nodePath": "/fixture/node", "openclawPackage": "/fixture/sdk"}):
            result = monitor.check_balance("hotel", {}, 100, blocked, budget_state=Path('/fixture/shared.sqlite3'))
            cached = monitor.check_balance("hotel", {"balances": {"hotel": result}}, 101, blocked)
        self.assertEqual(result["status"], "budget_exhausted")
        self.assertEqual(cached, result)
        self.assertEqual(len(calls), 1)
        self.assertIn('/fixture/shared.sqlite3', calls[0])

    def test_corrupt_outcome_ledger_is_not_treated_as_empty_or_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); output = home / 'report'; output.mkdir()
            ledger = output / 'outcomes.json'; ledger.write_text('{interrupted')
            old_alert = 'hotel:task_pending_review:hotel'
            (output / 'state.json').write_text(json.dumps({'activeAlerts': [old_alert]}))
            def inspect(*args, **kwargs):
                return {'reasons': [], 'instrumented': True, 'outcomeEvidence': [], 'taskHealth': {'state': 'unknown'}}
            with patch.object(Path, 'home', return_value=home), patch.object(monitor.health, 'inspect_runtime', side_effect=inspect), \
                 patch.object(sys, 'argv', ['monitor', '--profile', 'hotel', '--output-dir', str(output)]):
                monitor.main()
            self.assertEqual(ledger.read_text(), '{interrupted')
            result = json.loads((output / 'report.json').read_text())
            self.assertIn('monitor:outcome_state_unavailable', result['activeAlerts'])
            self.assertIn(old_alert, result['activeAlerts'])
            transitions = json.loads((output / 'alerts.json').read_text())
            self.assertFalse(any(e['code'] == old_alert and e['state'] == 'resolved' for e in transitions))
            self.assertEqual(result['profiles']['hotel']['health']['taskHealth']['persistence'], 'unavailable')

    def test_local_report_preserves_outcomes_between_runs_and_profile_filters(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); output = home / 'report'; output.mkdir()
            at = '2026-10-17T10:00:00+00:00'
            def evidence(agent, key):
                return {'evidenceId': monitor.health.evidence_id(agent, at, 'provider_timeout', key), 'agent': agent,
                        'kind': 'failed_run', 'at': at, 'observedAt': at, 'runKey': key, 'revision': 0,
                        'code': 'provider_timeout', 'state': 'pending_review'}
            old = {'outcomeEvidence': [evidence('hotel', 'a' * 24)]}
            retained = {'hotel': old, 'bailey-finance': {'outcomeEvidence': [evidence('ledger', 'b' * 24)]}}
            (output / 'outcomes.json').write_text(json.dumps({'schemaVersion': 1, 'profiles': retained}))
            ledger_alert = 'bailey-finance:task_pending_review:ledger'
            (output / 'state.json').write_text(json.dumps({'activeAlerts': [ledger_alert]}))
            def inspect(*args, **kwargs):
                self.assertEqual(kwargs['previous'], old)
                return {'reasons': [], 'instrumented': True, 'outcomeEvidence': old['outcomeEvidence'], 'taskHealth': {'state': 'pending_review'}}
            with patch.object(Path, 'home', return_value=home), patch.object(monitor.health, 'inspect_runtime', side_effect=inspect), \
                 patch.object(sys, 'argv', ['monitor', '--profile', 'hotel', '--output-dir', str(output)]):
                monitor.main()
            saved = json.loads((output / 'outcomes.json').read_text())
            self.assertEqual(saved['profiles']['bailey-finance'], retained['bailey-finance'])
            self.assertEqual(saved['profiles']['hotel']['outcomeEvidence'], old['outcomeEvidence'])
            self.assertEqual((output / 'outcomes.json').stat().st_mode & 0o777, 0o600)
            report = json.loads((output / 'report.json').read_text())
            self.assertIn(ledger_alert, report['activeAlerts'])
            transitions = json.loads((output / 'alerts.json').read_text())
            self.assertFalse(any(e['code'] == ledger_alert and e['state'] == 'resolved' for e in transitions))

    def test_health_html_does_not_conflate_service_and_task_status(self):
        report = {'at': 'fixture', 'activeAlerts': [], 'profiles': {'hotel': {'agents': {}, 'health': {
            'availability': {'hotel': {'state': 'response_observed'}}, 'taskHealth': {'state': 'pending_review'},
            'outcomeReasons': ['original task needs review']}}}, 'balances': {}, 'recovery': {}}
        rendered = monitor.html_report(report)
        self.assertIn('response_observed', rendered)
        self.assertIn('pending_review', rendered)
        self.assertIn('original task needs review', rendered)

    def test_json_valid_but_malformed_outcomes_are_preserved_as_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'outcomes.json'
            for entries in ([None], [{'runKey': 'a' * 24}], ['not evidence']):
                text = json.dumps({'schemaVersion': 1, 'profiles': {'hotel': {'outcomeEvidence': entries}}})
                path.write_text(text)
                self.assertEqual(monitor.load_outcome_state(path), ({}, False))
                self.assertEqual(path.read_text(), text)

    def test_daily_balance_waits_for_lock_but_local_report_skips_contention(self):
        with patch.object(monitor.fcntl, 'flock', side_effect=[BlockingIOError(), None]) as lock, \
             patch.object(monitor.time, 'sleep') as sleep:
            self.assertTrue(monitor.acquire_monitor_lock('fixture', True, clock=lambda: 100, sleep=sleep))
            sleep.assert_called_once_with(.1)
            self.assertEqual(lock.call_count, 2)
        with patch.object(monitor.fcntl, 'flock', side_effect=BlockingIOError()), \
             patch.object(monitor.time, 'sleep') as sleep:
            self.assertFalse(monitor.acquire_monitor_lock('fixture', False, clock=lambda: 100, sleep=sleep))
            sleep.assert_not_called()
            times = iter([100, 161])
            self.assertFalse(monitor.acquire_monitor_lock('fixture', True, clock=lambda: next(times), sleep=sleep))

    def test_budget_status_failure_never_becomes_zero_usage(self):
        def invalid(*args, **kwargs):
            return subprocess.CompletedProcess(args, 0, '{invalid')
        self.assertEqual(monitor.budget_status(Path('/fixture/no-budget'), invalid), {'status': 'budget_unavailable'})

    def test_paused_nomina_is_not_checked_or_falsely_resolved(self):
        self.assertNotIn('nomina', monitor.PROFILES)
        with self.assertRaises(ValueError):
            monitor.balance_runtime('nomina')
        old = 'nomina:task_pending_review:nomina'
        self.assertEqual(monitor.transition_alerts([old], [], 123), [{'at': 123, 'state': 'monitoring_paused', 'code': old}])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'outcomes.json'
            path.write_text(json.dumps({'schemaVersion': 1, 'profiles': {'nomina': {'outcomeEvidence': []}}}))
            self.assertEqual(monitor.load_outcome_state(path), ({'nomina': {'outcomeEvidence': []}}, True))


class CorreoScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.state = self.home / ".openclaw/workspace-owlswatch-correo/tasks/email_runtime"
        self.state.mkdir(parents=True)
        self.profile = self.home / ".openclaw-owlswatch"
        self.profile.mkdir()
        self.now = monitor.health.timestamp("2026-10-27T15:00:00Z")  # 10:00 Bogotá.

    def put(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def enable(self, ago=7200):
        marker = self.profile / "email-agent.enabled"
        marker.touch()
        os.utime(marker, (self.now - ago, self.now - ago))

    def record(self, mode="polling_30m", status="completed", ago=60, **extra):
        at = monitor.health.iso(self.now - ago)
        value = {"mode": mode, "status": status, "startedAt": at, "observedAt": at,
                 "enabled": True, "enabledSource": "marker"}
        if status in {"completed", "unchanged"}:
            value["completedAt"] = at
        value.update(extra)
        self.put(self.state / f"schedule-status-{mode}.json", value)
        return value

    def receipt(self, mode="daily_summary", day="2026-10-27", at="2026-10-27T13:00:05Z", **extra):
        value = {"completedAt": at, "notification": {"ok": True, "message_id": 123, "suppressed": False}}
        value.update(extra)
        self.put(self.state / f"{mode}-{day}.json", value)

    def digest_attempt(self, status, **extra):
        value = {"mode": "daily_summary", "day": "2026-10-27", "startedAt": monitor.health.iso(self.now - 120),
                 "status": status, "attempts": 1}
        value.update(extra)
        self.put(self.state / "digest-attempts/daily_summary-2026-10-27.json", value)

    def report(self):
        return monitor.correo_schedule(self.home, self.now)

    def test_disabled_is_separate_from_missing_evidence_and_marker_override(self):
        unknown = self.report()
        self.assertEqual(unknown["enablement"]["state"], "unknown")
        self.assertEqual(unknown["polling"]["state"], "unknown")
        self.record(status="disabled", enabled=False, enabledSource="none")
        disabled = self.report()
        self.assertEqual(disabled["enablement"]["state"], "disabled_observed")
        self.assertEqual(disabled["polling"]["state"], "disabled")
        self.assertEqual(disabled["digests"]["daily_summary"]["state"], "disabled")
        # Removing the marker does not disprove the runner's environment gate.
        self.record(status="unchanged", enabledSource="environment")
        self.assertEqual(self.report()["enablement"]["state"], "enabled_observed")

    def test_reenabled_marker_gets_one_poll_period_plus_execution_grace(self):
        self.record(status="disabled", enabled=False, enabledSource="none", ago=120)
        self.enable(ago=30)
        self.assertEqual(self.report()["polling"]["state"], "awaiting_attempt")
        self.assertEqual(self.report()["digests"]["daily_summary"]["state"], "awaiting_receipt")
        self.now += 2800
        self.assertEqual(self.report()["polling"]["state"], "stale_disabled_observation")
        self.assertEqual(self.report()["digests"]["daily_summary"]["state"], "overdue")

    def test_poll_completion_checkpoint_and_digest_delivery_are_independent(self):
        self.enable()
        self.record(status="incomplete", intakeStatus="unchanged")
        # An independent digest can fail after a successful empty mailbox batch.
        self.record(status="incomplete", intakeStatus="unchanged", completedAt=monitor.health.iso(self.now - 60))
        self.put(self.state / "checkpoint.json", {"lastAcknowledgedAt": monitor.health.iso(self.now - 7200),
                                                  "historyId": "DO_NOT_OUTPUT_PRIVATE_SOURCE_ID"})
        self.receipt()
        result = self.report()
        self.assertEqual(result["polling"]["state"], "attempt_completed")
        self.assertEqual(result["polling"]["checkpoint"]["state"], "stale")
        self.assertEqual(result["digests"]["daily_summary"]["state"], "delivered")
        self.assertNotIn("DO_NOT_OUTPUT", json.dumps(result))

    def test_failed_or_incomplete_poll_never_turns_healthy_when_stale(self):
        self.enable()
        for status in ("failed", "incomplete"):
            self.record(status=status, intakeStatus=status, pendingCount=3, error={"code": "http_error", "message": "PRIVATE_BODY"})
            result = self.report()
            self.assertEqual(result["polling"]["state"], status)
            self.assertEqual(result["polling"]["lastAttempt"]["pendingCount"], 3)
            self.assertNotIn("PRIVATE_BODY", json.dumps(result))
            self.record(status=status, ago=2800)
            result = self.report()
            self.assertEqual(result["polling"]["state"], "attempt_overdue")
            self.assertEqual(result["polling"]["lastAttempt"]["status"], status)

    def test_running_attempt_has_shorter_timeout_than_poll_interval(self):
        self.enable()
        self.record(status="running", ago=500)
        self.assertEqual(self.report()["polling"]["state"], "running")
        self.record(status="running", ago=1000)
        self.assertEqual(self.report()["polling"]["state"], "attempt_stale")

    def test_bogota_due_dates_and_grace_cross_midnight_and_morning_boundaries(self):
        self.enable(ago=86400)
        self.now = monitor.health.timestamp("2026-10-28T00:05:00Z")  # Still Oct27 Bogotá.
        self.assertEqual(self.report()["digests"]["daily_summary"]["expectedDay"], "2026-10-27")
        self.now = monitor.health.timestamp("2026-10-27T12:59:00Z")
        self.assertEqual(self.report()["digests"]["daily_summary"]["expectedDay"], "2026-10-26")
        self.now = monitor.health.timestamp("2026-10-27T13:05:00Z")
        result = self.report()["digests"]
        self.assertEqual(result["daily_summary"]["state"], "awaiting_receipt")
        self.assertEqual(result["daily_summary"]["expectedDay"], "2026-10-27")
        self.assertEqual(result["unanswered_7d"]["expectedDay"], "2026-10-26")
        self.now = monitor.health.timestamp("2026-10-27T13:16:00Z")
        result = self.report()["digests"]
        self.assertEqual(result["daily_summary"]["state"], "overdue")
        self.assertEqual(result["unanswered_7d"]["state"], "awaiting_receipt")

    def test_only_same_day_confirmed_message_receipt_proves_delivery(self):
        self.enable()
        for notification in ({"ok": True}, {"ok": True, "message_id": True}, {"ok": True, "message_id": 0},
                             {"ok": False, "message_id": 123}, {"ok": True, "suppressed": True}):
            self.receipt(notification=notification)
            self.assertEqual(self.report()["digests"]["daily_summary"]["state"], "delivery_unverified")
        self.receipt(at="2026-10-26T13:00:00Z")
        self.assertEqual(self.report()["digests"]["daily_summary"]["state"], "delivery_unverified")
        self.receipt(notification={"ok": True, "message_id": 123, "suppressed": True})
        self.digest_attempt("outcome_unknown")
        self.assertEqual(self.report()["digests"]["daily_summary"]["state"], "delivered")

    def test_legacy_stamp_is_unverified_without_reading_its_text(self):
        self.enable()
        stamp = self.profile / "schedule-stamps/correo-daily-summary-2026-10-27.stamp"
        stamp.parent.mkdir()
        stamp.write_text("PRIVATE_MODEL_TEXT")
        result = self.report()
        self.assertEqual(result["digests"]["daily_summary"]["state"], "legacy_stamp_unverified")
        self.assertNotIn("PRIVATE_MODEL_TEXT", json.dumps(result))

    def test_retry_wait_unknown_outcome_and_prejournal_failure_remain_visible(self):
        self.enable()
        self.digest_attempt("failed_not_sent", nextRetryAt=monitor.health.iso(self.now + 60), error={"code": "telegram_error"})
        result = self.report()["digests"]["daily_summary"]
        self.assertEqual(result["state"], "retry_wait")
        self.assertEqual(result["lastAttempt"]["errorCode"], "telegram_error")
        self.now += 61
        self.assertEqual(self.report()["digests"]["daily_summary"]["state"], "failed_not_sent")
        for status in ("sending", "outcome_unknown", "delivered"):
            self.digest_attempt(status)
            self.assertEqual(self.report()["digests"]["daily_summary"]["state"], "delivery_unknown")
        (self.state / "digest-attempts/daily_summary-2026-10-27.json").unlink()
        self.record(status="incomplete", digests={"daily_summary": {"status": "unavailable"}})
        self.assertEqual(self.report()["digests"]["daily_summary"]["state"], "failed")

    def test_per_mode_records_survive_unrelated_latest_status_and_never_scan_archive(self):
        self.enable()
        self.record(status="failed")
        self.put(self.state / "schedule-status.json", {"mode": "daily_summary", "status": "completed",
                 "startedAt": monitor.health.iso(self.now - 5), "completedAt": monitor.health.iso(self.now - 5)})
        with patch.object(Path, "glob", side_effect=AssertionError("Must not scan archives")), \
             patch.object(subprocess, "run", side_effect=AssertionError("Local evidence cannot launch checks")):
            result = self.report()
        self.assertEqual(result["polling"]["state"], "failed")
        self.assertEqual(result["digests"]["daily_summary"]["lastScheduleObservation"]["status"], "completed")
        self.assertEqual(result["digests"]["daily_summary"]["state"], "overdue")

    def test_corrupt_future_large_and_wrong_mode_records_are_unknown(self):
        self.enable()
        path = self.state / "schedule-status-polling_30m.json"
        records = [{"mode": "polling_30m", "status": []}, {"mode": "other", "status": "completed"},
                   {"mode": "polling_30m", "status": "completed", "startedAt": "2026-10-28T15:00:00Z"}]
        for value in records:
            self.put(path, value)
            self.assertEqual(self.report()["polling"]["state"], "unknown")
        path.write_text("x" * 262145)
        self.assertEqual(self.report()["polling"]["state"], "unknown")
        path.write_text("{broken")
        self.assertEqual(self.report()["polling"]["state"], "unknown")

    def test_html_explains_schedule_status_without_claiming_gateway_outage(self):
        self.record(status="disabled", enabled=False, enabledSource="none")
        rendered = monitor.correo_schedule_html(self.report())
        self.assertIn("disabled", rendered)
        self.assertIn("Bogotá", rendered)
        self.assertIn("08:00", rendered)
        self.assertIn("08:15", rendered)
        self.assertIn("does not establish a gateway outage", rendered)
        self.assertIn("does not complete intake", rendered)

    def test_aggregate_codes_surface_schedule_gaps_without_gateway_or_task_claims(self):
        self.record(status="disabled", enabled=False, enabledSource="none")
        codes = monitor.correo_schedule_alerts(self.report())
        self.assertIn("owlswatch:correo_schedule:disabled", codes)
        self.assertIn("owlswatch:correo_schedule:checkpoint_unverified", codes)
        self.enable(ago=120)
        self.record(status="failed", ago=60)
        self.receipt()
        codes = monitor.correo_schedule_alerts(self.report())
        self.assertIn("owlswatch:correo_schedule:poll_failed", codes)
        self.assertFalse(any(":daily_summary:" in code for code in codes))
        self.assertFalse(any("agent_blocked" in code or "task_pending_review" in code for code in codes))
        self.now += 3000
        self.assertIn("owlswatch:correo_schedule:poll_overdue", monitor.correo_schedule_alerts(self.report()))

    def test_digest_alert_transition_needs_matching_receipt_to_resolve(self):
        code = "owlswatch:correo_schedule:daily_summary:2026-10-27:delivery_unknown"
        scope = lambda day, state: {"digests": {"daily_summary": {"expectedDay": day, "state": state}}}
        cases = [("2026-10-27", "delivered", "resolved"),
                 ("2026-10-28", "delivered", "schedule_window_elapsed"),
                 ("2026-10-27", "awaiting_receipt", "evidence_changed"),
                 ("2026-10-27", "disabled", "evidence_changed")]
        for day, state, expected in cases:
            event = monitor.transition_alerts([code], [], self.now, scope(day, state))
            self.assertEqual(event, [{"at": self.now, "state": expected, "code": code}])
        self.assertEqual(monitor.transition_alerts([code], [], self.now)[0]["state"], "evidence_unavailable")

    def test_poll_failure_does_not_resolve_when_its_evidence_becomes_stale(self):
        code = "owlswatch:correo_schedule:poll_failed"
        for status in ("attempt_overdue", "unknown", "disabled", "awaiting_attempt"):
            scope = {"polling": {"state": status}}
            current = ["owlswatch:correo_schedule:poll_overdue"] if status == "attempt_overdue" else []
            events = monitor.transition_alerts([code], current, self.now, scope)
            self.assertEqual(next(event for event in events if event["code"] == code)["state"], "evidence_changed")
        scope = {"polling": {"state": "attempt_completed"}}
        self.assertEqual(monitor.transition_alerts([code], [], self.now, scope)[0]["state"], "resolved")

    def test_each_non_digest_schedule_code_needs_its_own_positive_evidence(self):
        codes = ["owlswatch:correo_schedule:checkpoint_stale", "owlswatch:correo_schedule:disabled"]
        for code in codes:
            self.assertEqual(monitor.transition_alerts([code], [], self.now)[0]["state"], "evidence_unavailable")
            scope = {"polling": {"state": "attempt_completed", "checkpoint": {"state": "unknown"}},
                     "enablement": {"state": "unknown"}}
            self.assertEqual(monitor.transition_alerts([code], [], self.now, scope)[0]["state"], "evidence_changed")
        scope = {"polling": {"checkpoint": {"state": "current"}}, "enablement": {"state": "enabled_by_marker"}}
        self.assertTrue(all(event["state"] == "resolved" for event in monitor.transition_alerts(codes, [], self.now, scope)))

    def test_symlinked_workspace_metadata_is_not_followed(self):
        original = self.state.parent.parent
        moved = self.home / "other-workspace"
        original.rename(moved)
        original.symlink_to(moved, target_is_directory=True)
        with patch.object(Path, "read_text", side_effect=AssertionError("Do not follow foreign state")):
            result = self.report()
        self.assertEqual(result["polling"]["state"], "unknown")
        self.assertEqual(result["polling"]["evidence"], "invalid")

    def test_aggregate_includes_correo_codes_and_preserves_business_incident(self):
        self.record(status="disabled", enabled=False, enabledSource="none")
        incident = "owlswatch:task_pending_review:correo"
        with patch.object(Path, "home", return_value=self.home), \
             patch.object(monitor.time, "time", return_value=self.now), \
             patch.object(monitor, "budget_status", return_value={"status": "ok"}), \
             patch.object(monitor, "report_profile", return_value={"health": {"instrumented": True,
                 "outcomeReasons": ["task_pending_review:correo"]}, "agents": {}}), \
             patch.object(sys, "argv", ["monitor", "--profile", "owlswatch", "--output-dir", str(self.home / "report")]):
            monitor.main()
        report = json.loads((self.home / "report/report.json").read_text())
        self.assertIn("owlswatch:correo_schedule:disabled", report["activeAlerts"])
        self.assertIn(incident, report["activeAlerts"])
        self.assertEqual(report["correoSchedule"]["polling"]["state"], "disabled")

    def test_filtered_report_does_not_inspect_correo_or_paused_profiles(self):
        output = self.home / "report"
        self.put(output / "state.json", {"activeAlerts": ["owlswatch:correo_schedule:poll_failed"]})
        with patch.object(Path, "home", return_value=self.home), \
             patch.object(monitor, "correo_schedule", side_effect=AssertionError("Correo not selected")), \
             patch.object(monitor, "budget_status", return_value={"status": "ok"}), \
             patch.object(monitor, "report_profile", return_value={"health": {"instrumented": True}, "agents": {}}) as inspect, \
             patch.object(sys, "argv", ["monitor", "--profile", "hotel", "--output-dir", str(self.home / "report")]):
            monitor.main()
        self.assertEqual(inspect.call_count, 1)
        self.assertEqual(inspect.call_args.args[0], self.home / ".openclaw-hotel")
        report = json.loads((self.home / "report/report.json").read_text())
        self.assertNotIn("correoSchedule", report)
        self.assertIn("owlswatch:correo_schedule:poll_failed", report["activeAlerts"])
        transitions = json.loads((output / "alerts.json").read_text())
        self.assertFalse(any(event["code"] == "owlswatch:correo_schedule:poll_failed" for event in transitions))
        self.assertNotIn("nomina", monitor.PROFILES)
        self.assertNotIn("finca", monitor.PROFILES)


if __name__ == "__main__":
    unittest.main()
