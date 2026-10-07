import importlib.util
import json
import os
from pathlib import Path
import plistlib
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maintenance import Maintenance, health_probe, rotate_logs
from security import BoundaryError
from store import Store


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.root.chmod(0o700)
        self.clock = 1000
        self.config = {"archive": {"enabled": False}, "telegram": {"account_id": "default",
            "allowed_sender_ids": ["101"], "allowed_routes": [{"chat_id": "-201", "thread_id": ""}]}}
        self.worker = Maintenance(self.root, self.config, lambda: self.clock)
        self.addCleanup(lambda: self.worker.close())

    def restart(self):
        self.worker.close()
        self.worker = Maintenance(self.root, self.config, lambda: self.clock)

    def test_no_payroll_data_does_not_create_a_ledger_or_claim_backup(self):
        self.assertEqual(self.worker.backup()["status"], "no_payroll_data")
        self.assertFalse((self.root / "state/payroll.sqlite3").exists())

    def test_backup_missing_setup_notifies_once_without_financial_change(self):
        Store(self.root / "state/payroll.sqlite3")
        self.worker.backup()
        self.restart()
        self.worker.backup()
        self.assertEqual(self.worker.db.execute("SELECT count(*) FROM notices").fetchone()[0], 1)

    def test_brief_health_blip_is_quiet_and_persistent_failure_alert_is_once(self):
        self.worker.channel_check(False)
        self.clock += 899
        self.worker.channel_check(False)
        self.assertEqual(self.worker.deliver_notices(lambda _: "sent"), "idle")
        self.clock += 1
        self.worker.channel_check(False)
        self.restart()
        self.worker.channel_check(False)
        self.assertEqual(self.worker.deliver_notices(lambda _: "sent"), "sent")
        self.worker.channel_check(True)
        self.assertEqual(self.worker.deliver_notices(lambda _: "sent"), "idle")

    def test_unknown_notice_send_is_not_automatically_duplicated(self):
        self.worker.notice("one", "Safe notice")
        self.assertEqual(self.worker.deliver_notices(lambda _: "unverified"), "unverified")
        self.restart()
        self.clock += 10000
        self.assertEqual(self.worker.deliver_notices(lambda _: "sent"), "idle")

    def test_rate_limit_retries_are_delayed_and_bounded(self):
        self.worker.notice("one", "Safe notice")
        for attempt in range(3):
            self.assertEqual(self.worker.deliver_notices(lambda _: "retryable"), "retryable")
            self.assertEqual(self.worker.deliver_notices(lambda _: "retryable"), "idle")
            self.clock += 3601
        self.assertEqual(self.worker.deliver_notices(lambda _: "retryable"), "idle")

    def test_backup_repairs_commit_to_enqueue_gap_with_bounded_retry(self):
        Store(self.root / "state/payroll.sqlite3")
        with patch("maintenance.Archive") as constructor:
            archive = constructor.return_value
            archive.enabled.return_value = True
            archive.status.side_effect = [{"needs_backup": True}, {"off_machine_current": True}]
            archive.retry.side_effect = [{"status": "uploaded"}, {"status": "idle"}]
            self.assertEqual(self.worker.backup()["status"], "current")
            archive.enqueue.assert_called_once()
            self.assertEqual(archive.retry.call_count, 2)

    def test_permanent_backup_error_backs_off_and_notifies_without_replaying_payroll(self):
        Store(self.root / "state/payroll.sqlite3")
        with patch("maintenance.Archive") as constructor:
            archive = constructor.return_value
            archive.enabled.return_value = True
            archive.status.return_value = {"needs_backup": False}
            archive.retry.side_effect = BoundaryError("PRIVATE_FILE_REQUIRED", "Secret-free", False)
            self.assertEqual(self.worker.backup()["status"], "failed")
            self.assertEqual(self.worker.backup()["status"], "backoff")
            archive.retry.assert_called_once()
            self.assertEqual(self.worker.db.execute("SELECT count(*) FROM notices").fetchone()[0], 1)

    def test_journal_unanswered_warning_is_scoped_and_durable(self):
        path = self.root / "state/delivery.sqlite3"
        db = sqlite3.connect(path)
        path.chmod(0o600)
        db.executescript("CREATE TABLE deliveries(id TEXT, account_id TEXT, chat_id TEXT, thread_id TEXT, sender_id TEXT, status TEXT, received_at REAL, alerted_at REAL);")
        db.executemany("INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,NULL)", [
            ("a", "default", "-201", "", "101", "received", 0),
            ("b", "default", "-201", "", "101", "delivered", 0),
            ("c", "other", "-201", "", "101", "received", 0)])
        db.commit()
        db.close()
        self.assertEqual(self.worker.delivery_check(), 1)
        self.restart()
        self.assertEqual(self.worker.delivery_check(), 0)
        self.assertEqual(self.worker.db.execute("SELECT count(*) FROM notices").fetchone()[0], 1)

    def test_probe_has_timeout_and_never_restarts_the_gateway(self):
        with patch("maintenance.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = json.dumps({"channelAccounts": {"telegram": [{"accountId": "default", "running": True, "connected": True}]}})
            self.assertTrue(health_probe("/synthetic/openclaw", "/synthetic/config"))
            self.assertNotIn("restart", run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs["timeout"], 30)

    def test_log_rotation_retains_active_inode_and_three_private_archives(self):
        logs = self.root / "logs"
        logs.mkdir(mode=0o700)
        path = logs / "gateway.stderr.log"
        path.touch(mode=0o600)
        inode = path.stat().st_ino
        for index in range(5):
            path.write_text(str(index) * 20)
            rotate_logs(self.root, limit=10)
        self.assertEqual(path.stat().st_ino, inode)
        self.assertEqual(path.read_text(), "")
        self.assertEqual((logs / "gateway.stderr.log.1").read_text(), "4" * 20)
        self.assertEqual(len(list(logs.glob("gateway.stderr.log.*"))), 3)
        self.assertTrue(all(not p.stat().st_mode & 0o077 for p in logs.iterdir()))

    def test_log_rotation_rejects_symlink_target(self):
        logs = self.root / "logs"
        logs.mkdir(mode=0o700)
        path = logs / "gateway.stderr.log"
        path.touch(mode=0o600)
        path.write_text("x" * 20)
        (logs / "gateway.stderr.log.1").symlink_to(path)
        with self.assertRaises(BoundaryError):
            rotate_logs(self.root, limit=10)

    def test_installer_retains_gateway_environment_and_scopes_schedule_to_nomina(self):
        script = Path(__file__).resolve().parents[3] / "scripts/install-nomina-maintenance.py"
        spec = importlib.util.spec_from_file_location("install_nomina", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        home = self.root / "home"
        plist_dir = home / "Library/LaunchAgents"
        plist_dir.mkdir(parents=True)
        (home / ".openclaw-nomina").mkdir(mode=0o700)
        workspace = home / "workspace"
        (workspace / "tools/owlswatch_payroll").mkdir(parents=True)
        (workspace / "tools/owlswatch_payroll/maintenance.py").touch()
        (workspace / ".venv/bin").mkdir(parents=True)
        (workspace / ".venv/bin/python3").touch()
        gateway = {"Label": "ai.openclaw.nomina", "EnvironmentVariables": {"RETAIN": "synthetic"}, "StandardErrorPath": "/dev/null"}
        path = plist_dir / "ai.openclaw.nomina.plist"
        path.write_bytes(plistlib.dumps(gateway))
        result = module.install(workspace, home, "/synthetic/openclaw", enable=True, activate=False)
        updated = plistlib.loads(path.read_bytes())
        self.assertEqual(updated["EnvironmentVariables"], gateway["EnvironmentVariables"])
        self.assertNotEqual(updated["StandardErrorPath"], "/dev/null")
        self.assertTrue(updated["KeepAlive"] and updated["RunAtLoad"])
        self.assertTrue(result["enabled"])
        schedule = plistlib.loads((plist_dir / "ai.openclaw.nomina.maintenance.plist").read_bytes())
        self.assertEqual(schedule["StartInterval"], 300)
        self.assertEqual((workspace / "maintenance.enabled").stat().st_mode & 0o777, 0o600)
        with patch.object(module.subprocess, "run") as run:
            run.return_value.returncode = 0
            module.reload_gateway(path)
            commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual([cmd[1] for cmd in commands], ["print", "bootout", "enable", "bootstrap"])
        self.assertTrue(all("nomina" in " ".join(cmd) for cmd in commands))
        with patch.object(module.subprocess, "run") as run:
            run.return_value.returncode = 1
            run.return_value.stderr = "Permission denied"
            with self.assertRaises(RuntimeError):
                module.reload_gateway(path)
            self.assertEqual(run.call_count, 1)


if __name__ == "__main__":
    unittest.main()
