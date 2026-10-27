import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/monitor-http-budget.py"
spec = importlib.util.spec_from_file_location("monitor_budget", SCRIPT)
budget = importlib.util.module_from_spec(spec)
spec.loader.exec_module(budget)
T = 1_800_000_000_000


class MonitorBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / "budget.sqlite3"
        self.addCleanup(self.temp.cleanup)

    def init(self, now=T):
        self.assertEqual(budget.initialize(self.path, now_ms=now)["status"], "initialized")

    def reserve(self, category="balance", now=T, profile="owlswatch"):
        return budget.reserve(self.path, category, profile, now_ms=now)

    def test_missing_state_is_not_implicitly_initialized(self):
        self.assertEqual(self.reserve()["reason"], "state_missing")
        self.assertEqual(budget.inspect(self.path, now_ms=T)["status"], "budget_unavailable")
        self.assertFalse(self.path.exists())

    def test_init_cannot_reset_and_status_is_read_only(self):
        self.init()
        self.assertEqual(self.reserve()["status"], "reserved")
        before = self.path.read_bytes()
        status = budget.inspect(self.path, now_ms=T + 1)
        self.assertEqual(status["used"]["balance_24h"], 1)
        self.assertEqual(before, self.path.read_bytes())
        again = budget.initialize(self.path, now_ms=T + 1)
        self.assertFalse(again["created"])
        self.assertEqual(again["remaining"]["balance_24h"], 2)
        self.assertEqual(before, self.path.read_bytes())

    def test_100_concurrent_callers_share_one_fleet_budget(self):
        self.init()
        barrier = threading.Barrier(100)
        def attempt(index):
            barrier.wait(timeout=20)
            return self.reserve(budget.CATEGORIES[index % 3], profile=budget.PROFILES[index % len(budget.PROFILES)])
        with concurrent.futures.ThreadPoolExecutor(max_workers=100) as pool:
            results = list(pool.map(attempt, range(100)))
        granted = [x for x in results if x["status"] == "reserved"]
        self.assertEqual(len(granted), 7)
        self.assertEqual(len({x["reservationId"] for x in granted}), 7)
        self.assertTrue(all(x["status"] in ("reserved", "budget_exhausted", "budget_unavailable") for x in results))
        used = budget.inspect(self.path, now_ms=T)["used"]
        self.assertEqual(used, {"balance_24h": 3, "recovery_24h": 2, "diagnostic_24h": 5,
                               "diagnostic_30d": 5, "owner_alert_24h": 2, "owner_alert_30d": 2})

    def test_rolling_boundaries_do_not_reset_at_midnight(self):
        self.init()
        for _ in range(3):
            self.assertEqual(self.reserve()["status"], "reserved")
        denied = self.reserve(now=T + budget.DAY - 1)
        self.assertEqual(denied["status"], "budget_exhausted")
        self.assertEqual(denied["retryAt"], (T + budget.DAY) / 1000)
        self.assertEqual(self.reserve(now=T + budget.DAY)["status"], "reserved")
        self.assertEqual(budget.inspect(self.path, now_ms=T + budget.DAY)["used"]["diagnostic_30d"], 4)

    def test_thirty_days_preserve_separate_diagnostic_and_alert_counts(self):
        self.init()
        for day in range(30):
            for category in ("balance",) * 3 + ("recovery",) * 2 + ("owner_alert",) * 2:
                self.assertEqual(self.reserve(category, T + day * budget.DAY)["status"], "reserved")
        status = budget.inspect(self.path, now_ms=T + 29 * budget.DAY)
        self.assertEqual(status["used"]["diagnostic_30d"], 150)
        self.assertEqual(status["used"]["owner_alert_30d"], 60)
        self.assertIn("diagnostic_30d", self.reserve(now=T + 29 * budget.DAY)["limits"])
        self.assertIn("owner_alert_30d", self.reserve("owner_alert", T + 29 * budget.DAY)["limits"])
        self.assertEqual(self.reserve(now=T + 30 * budget.DAY)["status"], "reserved")

    def test_monthly_gate_is_enforced_even_when_daily_count_is_empty(self):
        self.init()
        with sqlite3.connect(self.path) as db:
            db.executemany("INSERT INTO reservations VALUES(?,?,?,?,?)",
                [(i + 1, str(i), "balance", "hotel", T) for i in range(150)])
            db.execute("UPDATE metadata SET event_count=150")
        denied = self.reserve(now=T + 2 * budget.DAY)
        self.assertEqual(denied["limits"], ["diagnostic_30d"])
        self.assertEqual(denied["retryAt"], (T + 30 * budget.DAY) / 1000)

    def test_crash_after_commit_is_spent_across_new_process_and_reinstall(self):
        self.assertEqual(budget.initialize(self.path)["status"], "initialized")
        code = """import importlib.util,os,sys
spec=importlib.util.spec_from_file_location('budget',sys.argv[1]); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
r=m.reserve(sys.argv[2],'balance','hotel'); os._exit(73 if r['status']=='reserved' else 74)
"""
        process = subprocess.run([sys.executable, "-c", code, str(SCRIPT), str(self.path)], capture_output=True)
        self.assertEqual(process.returncode, 73)
        result = json.loads(subprocess.check_output([sys.executable, str(SCRIPT), "status", "--state", str(self.path)]))
        self.assertEqual(result["used"]["balance_24h"], 1)
        self.assertEqual(budget.initialize(self.path)["used"]["balance_24h"], 1)

    def test_rollback_blocks_without_reset_or_refund(self):
        self.init()
        self.reserve(now=T + 1000)
        for action in (lambda: self.reserve(now=T), lambda: budget.inspect(self.path, now_ms=T),
                       lambda: budget.initialize(self.path, now_ms=T)):
            self.assertEqual(action(), {"status": "budget_unavailable", "reason": "clock_rollback"})
        self.assertEqual(self.reserve(now=T + 1000)["remaining"]["balance_24h"], 1)

    def test_corrupt_empty_or_partially_deleted_state_never_resets(self):
        for contents in (b"", b"invalid SQLite fixture"):
            self.path.write_bytes(contents)
            self.path.chmod(0o600)
            self.assertEqual(self.reserve()["status"], "budget_unavailable")
            self.assertEqual(budget.inspect(self.path, now_ms=T)["status"], "budget_unavailable")
            self.assertEqual(budget.initialize(self.path, now_ms=T)["status"], "budget_unavailable")
            self.assertEqual(self.path.read_bytes(), contents)
            self.path.unlink()
        self.init()
        self.reserve()
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM reservations")
        self.assertEqual(self.reserve()["status"], "budget_unavailable")

    def test_lost_ledger_or_marker_cannot_reset_on_reinstall(self):
        self.init()
        self.reserve()
        marker = budget.initialization_marker(self.path)
        self.path.unlink()
        self.assertEqual(budget.initialize(self.path, now_ms=T)["reason"], "state_missing")
        self.assertFalse(self.path.exists())
        self.assertTrue(marker.exists())

    def test_missing_marker_on_existing_ledger_blocks_status_and_reservation(self):
        self.init()
        budget.initialization_marker(self.path).unlink()
        for operation in (lambda: self.reserve(), lambda: budget.inspect(self.path, now_ms=T),
                          lambda: budget.initialize(self.path, now_ms=T)):
            self.assertEqual(operation()["status"], "budget_unavailable")

    def test_unwritable_and_unsafe_state_fail_closed(self):
        self.init()
        self.path.chmod(0o400)
        self.assertEqual(self.reserve()["reason"], "state_unwritable")
        self.path.chmod(0o600)
        self.root.chmod(0o500)
        try:
            self.assertEqual(self.reserve()["reason"], "state_unwritable")
        finally:
            self.root.chmod(0o700)
        alias = self.root / "alias.sqlite3"
        alias.symlink_to(self.path)
        self.assertEqual(budget.reserve(alias, "balance", "hotel", now_ms=T)["status"], "budget_unavailable")
        self.path.chmod(0o644)
        self.assertEqual(self.reserve()["status"], "budget_unavailable")

    def test_paused_nomina_denied_but_historical_attempts_remain_counted(self):
        self.init()
        for category in budget.CATEGORIES:
            self.assertEqual(self.reserve(category, profile="nomina")["reason"], "scope_invalid")
        self.assertEqual(budget.inspect(self.path, now_ms=T)["used"]["diagnostic_24h"], 0)
        # Prior policy allowed four daily balances. Preserve that history after
        # a pause/cap reduction; do not erase it or grant a fresh allowance.
        with sqlite3.connect(self.path) as db:
            db.executemany("INSERT INTO reservations VALUES(?,?,?,?,?)",
                [(i + 1, str(i), "balance", "nomina", T) for i in range(4)])
            db.execute("UPDATE metadata SET event_count=4")
        before = self.path.read_bytes()
        status = budget.inspect(self.path, now_ms=T)
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["used"]["balance_24h"], 4)
        self.assertEqual(status["remaining"]["balance_24h"], 0)
        self.assertEqual(budget.initialize(self.path, now_ms=T)["used"]["balance_24h"], 4)
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(self.reserve()["status"], "budget_exhausted")
        self.assertEqual(self.reserve("recovery")["status"], "reserved")
        self.assertIn("diagnostic_24h", self.reserve("recovery")["limits"])
        self.assertEqual(self.reserve(now=T + budget.DAY)["status"], "reserved")
        self.assertEqual(budget.inspect(self.path, now_ms=T + budget.DAY)["used"]["diagnostic_30d"], 6)

    def test_unexpected_trigger_or_identity_cannot_bypass_budget(self):
        self.init()
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TRIGGER unexpected AFTER INSERT ON reservations BEGIN DELETE FROM reservations; END")
        self.assertEqual(self.reserve()["status"], "budget_unavailable")
        self.assertEqual(self.reserve(profile="outside-scope")["reason"], "scope_invalid")
        self.assertEqual(self.reserve(category="unmetered")["reason"], "scope_invalid")


if __name__ == "__main__":
    unittest.main()
