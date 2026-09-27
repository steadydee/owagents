import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LABELS = ["ai.openclaw.finca.daily-checkin", "ai.openclaw.finca.daily-report"]


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.bin = self.home / "fake-bin"
        self.bin.mkdir()
        self.state_path = self.home / "launchctl.json"
        self.state = {"loaded": {}, "disabled": [f"gui/{os.getuid()}/{label}" for label in LABELS], "calls": []}
        executable = self.bin / "launchctl"
        executable.write_text(f"#!{sys.executable}\n" + '''import json, os, plistlib, sys
from pathlib import Path
path = Path(os.environ["FAKE_LAUNCH_STATE"])
state = json.loads(path.read_text())
args = sys.argv[1:]
state["calls"].append(args)
code = 0
if args[0] == "print":
    if state.get("inspect_fail"):
        print("Operation not permitted", file=sys.stderr); code = 1
    elif args[1] in state["loaded"]:
        status = state["loaded"][args[1]]
        print("state = " + status)
        if status == "running": print("pid = 12345")
    else:
        print("Could not find service", file=sys.stderr); code = 113
elif args[0] == "bootout":
    if state.get("stop_fail"): code = 1
    else: state["loaded"].pop(args[1], None)
elif args[0] == "enable":
    state["disabled"] = [item for item in state["disabled"] if item != args[1]]
elif args[0] == "bootstrap":
    payload = plistlib.loads(Path(args[2]).read_bytes())
    target = args[1] + "/" + payload["Label"]
    if target in state["disabled"] or target in state["loaded"]: code = 1
    else: state["loaded"][target] = "not running"
else: code = 2
path.write_text(json.dumps(state))
raise SystemExit(code)
''')
        executable.chmod(0o700)
        self.env = {"HOME": str(self.home), "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                    "FAKE_LAUNCH_STATE": str(self.state_path), "FINCA_SCHEDULE_LOG_DIR": str(self.home / "logs"),
                    "FINCA_SYSTEM_LAUNCH_DIR": str(self.home / "system-plists")}

    def invoke(self, action="install"):
        self.state_path.write_text(json.dumps(self.state))
        result = subprocess.run(["bash", str(ROOT / "scripts/install-finca-schedule.sh"), action],
                                env=self.env, capture_output=True, text=True, timeout=10)
        self.state = json.loads(self.state_path.read_text())
        return result

    def test_disabled_jobs_enable_before_bootstrap_and_existing_stamps_survive(self):
        stamps = self.home / ".openclaw-finca/schedule-stamps"
        stamps.mkdir(parents=True)
        stamp = stamps / "finca-daily-report-2026-09-26.stamp"
        stamp.write_text("legacy receipt")
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stamp.read_text(), "legacy receipt")
        self.assertTrue((self.home / ".openclaw-finca/bin/run-finca-tool-job.py").is_file())
        self.assertEqual(self.state["disabled"], [])
        verbs = [call[0] for call in self.state["calls"]]
        self.assertLess(max(i for i, verb in enumerate(verbs) if verb == "enable"), verbs.index("bootstrap"))
        for label, hour in zip(LABELS, [16, 7]):
            payload = plistlib.loads((self.home / "Library/LaunchAgents" / (label + ".plist")).read_bytes())
            self.assertEqual(payload["StartCalendarInterval"], {"Hour": hour, "Minute": 0})
            self.assertEqual(payload["StartInterval"], 900)

    def test_loaded_other_owner_blocks_install_and_uninstall_without_removing_helper(self):
        helper = self.home / ".openclaw-finca/bin/run-finca-tool-job.py"
        helper.parent.mkdir(parents=True)
        helper.write_text("preserve")
        for domain in ["system", f"user/{os.getuid()}"]:
            for action in ["install", "uninstall"]:
                with self.subTest(domain=domain, action=action):
                    self.state["loaded"] = {domain + "/" + LABELS[0]: "not running"}
                    self.state["calls"] = []
                    self.assertNotEqual(self.invoke(action).returncode, 0)
                    self.assertEqual(helper.read_text(), "preserve")
                    self.assertTrue(all(call[0] == "print" for call in self.state["calls"]))

    def test_unloaded_system_definition_also_blocks_second_owner(self):
        directory = Path(self.env["FINCA_SYSTEM_LAUNCH_DIR"])
        directory.mkdir()
        (directory / (LABELS[1] + ".plist")).touch()
        self.assertNotEqual(self.invoke().returncode, 0)
        self.assertFalse(any(call[0] == "bootstrap" for call in self.state["calls"]))

    def test_running_gui_job_is_not_interrupted(self):
        self.state["loaded"] = {f"gui/{os.getuid()}/{LABELS[0]}": "running"}
        self.assertNotEqual(self.invoke().returncode, 0)
        self.assertFalse(any(call[0] in {"bootout", "enable", "bootstrap"} for call in self.state["calls"]))

    def test_stop_failure_blocks_replacement(self):
        self.state["loaded"] = {f"gui/{os.getuid()}/{LABELS[0]}": "not running"}
        self.state["stop_fail"] = True
        self.assertNotEqual(self.invoke().returncode, 0)
        self.assertFalse(any(call[0] in {"enable", "bootstrap"} for call in self.state["calls"]))

    def test_inactive_gui_owner_is_removed_before_replacement(self):
        self.state["loaded"] = {f"gui/{os.getuid()}/{LABELS[0]}": "not running"}
        self.assertEqual(self.invoke().returncode, 0)
        verbs = [call[0] for call in self.state["calls"]]
        self.assertLess(verbs.index("bootout"), verbs.index("enable"))

    def test_inspection_failure_does_not_mean_absent(self):
        self.state["inspect_fail"] = True
        self.assertNotEqual(self.invoke().returncode, 0)
        self.assertFalse(any(call[0] == "bootstrap" for call in self.state["calls"]))


if __name__ == "__main__":
    unittest.main()
