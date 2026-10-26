"""Disposable filesystem tests: no real profile, gateway or network is used."""
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/prepare-isolated-runtime.py"
spec = importlib.util.spec_from_file_location("isolated_runtime", SCRIPT)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class RuntimeStagingTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.system = self.root / "system"
        self.system.mkdir()
        self.candidate = self.root / "candidate"
        node = self.candidate / f"node-v{runtime.NODE}-darwin-arm64/bin/node"
        node.parent.mkdir(parents=True)
        node.write_bytes(b"synthetic node fixture")
        node.chmod(0o700)
        npm = self.candidate / f"openclaw-{runtime.OPENCLAW}"
        self.package = npm / "node_modules/openclaw"
        (self.package / "dist").mkdir(parents=True)
        (self.package / "package.json").write_text(json.dumps({"version": runtime.OPENCLAW}))
        (self.package / "openclaw.mjs").write_text("// synthetic fixture\n")
        (self.package / "dist/index.js").write_text("// synthetic fixture\n")
        (npm / "package-lock.json").write_text(json.dumps({"packages": {
            "node_modules/openclaw": {"version": runtime.OPENCLAW, "integrity": "sha512-synthetic-fixture"}}}))
        (npm / "node_modules/.bin").mkdir()
        (npm / "node_modules/.bin/openclaw").symlink_to("../openclaw/openclaw.mjs")

    def profile(self, name="owlswatch"):
        state = self.home / f".openclaw-{name}"
        state.mkdir()
        workspace = self.home / ".openclaw" / runtime.PROFILES[name][2][0]
        plugin = workspace / "tools/fixture"
        (plugin / "node_modules").mkdir(parents=True)
        (plugin / "index.mjs").write_text("// synthetic fixture")
        (plugin / "node_modules/openclaw").symlink_to(self.home / ".npm-global/lib/node_modules/openclaw")
        (state / "openclaw.json").write_text(json.dumps({
            "agents": {"list": [{"id": "fixture"}], "defaults": {"models": {}}},
            "plugins": {"load": {"paths": [str(plugin / "index.mjs")]}},
            "secretFixture": "private value that must stay out of plans"}))
        auth = state / "agents/fixture/agent/auth-profiles.json"
        auth.parent.mkdir(parents=True)
        auth.write_text("{}")
        directory = self.system if name == "hotel" else self.home / "Library/LaunchAgents"
        directory.mkdir(parents=True, exist_ok=True)
        label = f"ai.openclaw.{name}"
        gateway = {"Label": label, "ProgramArguments": ["/bin/sh", str(state / "service-env/wrapper.sh"),
            str(state / "service-env/private.env"), "/opt/homebrew/opt/node/bin/node",
            str(self.home / ".npm-global/lib/node_modules/openclaw/dist/index.js"), "gateway", "--port", "19999"],
            "EnvironmentVariables": {"KEEP_EXISTING": "yes"}, "KeepAlive": True}
        (directory / f"{label}.plist").write_bytes(plistlib.dumps(gateway))
        observer = {"Label": label + ".telegram-observer", "ProgramArguments": ["/usr/bin/python3", "observer.py",
            "--openclaw", str(self.home / ".npm-global/bin/openclaw")], "EnvironmentVariables": {"KEEP_EXISTING": "yes"}}
        (directory / f"{label}.telegram-observer.plist").write_bytes(plistlib.dumps(observer))
        return state, plugin

    def plan(self, name="owlswatch"):
        return runtime.build_plan(self.home, name, self.candidate, self.system)

    def stage(self, values):
        plan, snapshots, info = values
        return runtime.stage(plan, snapshots, info, node_sha256=info["nodeSha256"],
                             tree_sha256=info["npmTreeSha256"], npm_integrity=info["npmIntegrity"])

    def test_only_explicit_four_profiles_are_accepted(self):
        for name in ["luna", "finca", "../hotel", "other", ""]:
            with self.subTest(profile=name), self.assertRaises(runtime.Refusal):
                runtime.profile_paths(self.home, name)
        for name in runtime.PROFILES:
            with self.subTest(profile=name):
                self.profile(name)
                plan, _, _ = self.plan(name)
                self.assertEqual(plan["profile"], name)
                self.assertFalse(plan["activationReady"])

    def test_plan_is_read_only_and_preserves_wrappers_and_schedule_environment(self):
        state, plugin = self.profile()
        before = runtime.tree_manifest(self.home / "Library")
        link = plugin / "node_modules/openclaw"
        previous = os.readlink(link)
        plan, snapshots, _ = self.plan()
        self.assertEqual(before, runtime.tree_manifest(self.home / "Library"))
        self.assertEqual(os.readlink(link), previous)
        self.assertFalse((state / "runtime").exists())
        self.assertNotIn("private value", json.dumps(plan))
        source = plistlib.loads(snapshots["ai.openclaw.owlswatch"]["original"])
        proposed = plistlib.loads(snapshots["ai.openclaw.owlswatch"]["proposed"])
        self.assertEqual(source["ProgramArguments"][:3], proposed["ProgramArguments"][:3])
        self.assertEqual(source["ProgramArguments"][5:], proposed["ProgramArguments"][5:])
        self.assertEqual(source["EnvironmentVariables"], proposed["EnvironmentVariables"])
        observer = plistlib.loads(snapshots["ai.openclaw.owlswatch.telegram-observer"]["proposed"])
        self.assertEqual(observer["EnvironmentVariables"]["OPENCLAW_BIN"], plan["scopedCli"])
        self.assertEqual(observer["ProgramArguments"][-1], plan["scopedCli"])
        self.assertEqual(observer["EnvironmentVariables"]["KEEP_EXISTING"], "yes")
        self.assertTrue(any("auth-profiles" in gate for gate in plan["requiredGates"]))

    def test_hotel_preserves_system_owner_and_does_not_read_or_rewrite_gui_duplicate(self):
        self.profile("hotel")
        gui = self.home / "Library/LaunchAgents/ai.openclaw.hotel.plist"
        gui.parent.mkdir(parents=True)
        gui.write_bytes(b"duplicate intentionally not parsed")
        plan, _, _ = self.plan("hotel")
        plists = [x for x in plan["operations"] if x["kind"].startswith("replace_plist")]
        self.assertEqual(plan["launchdDomain"], "system")
        self.assertTrue(all(x["service"].startswith("system/") for x in plists))
        self.assertTrue(all(Path(x["path"]).parent == self.system for x in plists))
        self.assertEqual(gui.read_bytes(), b"duplicate intentionally not parsed")
        self.assertIn(str(gui), plan["untouchedHotelGuiPlists"])

    def test_unknown_plugin_domain_and_workspace_symlink_are_rejected(self):
        state, plugin = self.profile()
        config = state / "openclaw.json"
        data = json.loads(config.read_text())
        data["plugins"]["load"]["paths"] = [str(self.home / ".openclaw/workspace-luna/plugin")]
        config.write_text(json.dumps(data))
        with self.assertRaises(runtime.Refusal): self.plan()
        data["plugins"]["load"]["paths"] = [str(plugin / "index.mjs")]
        config.write_text(json.dumps(data))
        workspace = plugin.parents[1]
        moved = self.root / "different-domain"
        workspace.rename(moved)
        workspace.symlink_to(moved)
        with self.assertRaises(runtime.Refusal): self.plan()

    def test_package_and_symlink_escape_are_rejected(self):
        external = self.root / "external"
        external.write_text("fixture")
        escape = self.package / "escape"
        escape.symlink_to(external)
        with self.assertRaises(runtime.Refusal): runtime.inspect_candidate(self.candidate)
        escape.unlink()
        escape.symlink_to("../../../external")
        with self.assertRaises(runtime.Refusal): runtime.inspect_candidate(self.candidate)
        escape.unlink()
        modules = self.package.parent
        moved = self.root / "other-modules"
        modules.rename(moved)
        modules.symlink_to(moved)
        with self.assertRaises(runtime.Refusal): runtime.inspect_candidate(self.candidate)

    def test_wrong_package_version_and_unexpected_gateway_are_rejected(self):
        self.profile()
        (self.package / "package.json").write_text('{"version":"unapproved"}')
        with self.assertRaises(runtime.Refusal): self.plan()
        (self.package / "package.json").write_text(json.dumps({"version": runtime.OPENCLAW}))
        plist = self.home / "Library/LaunchAgents/ai.openclaw.owlswatch.plist"
        data = plistlib.loads(plist.read_bytes())
        data["ProgramArguments"][4] = "some-other-runtime"
        plist.write_bytes(plistlib.dumps(data))
        with self.assertRaises(runtime.Refusal): self.plan()

    def test_stage_release_gate_refusal_makes_no_files_and_runs_no_binary(self):
        state, _ = self.profile()
        values = self.plan()
        with patch.object(runtime, "release_gate", side_effect=runtime.Refusal("unreleased")), patch.object(runtime, "verify_node") as verify:
            with self.assertRaises(runtime.Refusal): self.stage(values)
        verify.assert_not_called()
        self.assertFalse((state / "runtime").exists())

    def test_unapproved_digest_refuses_before_executing_or_copying(self):
        state, _ = self.profile()
        plan, snapshots, info = self.plan()
        with patch.object(runtime, "release_gate"), patch.object(runtime, "verify_node") as verify:
            with self.assertRaises(runtime.Refusal):
                runtime.stage(plan, snapshots, info, node_sha256="wrong", tree_sha256=info["npmTreeSha256"], npm_integrity=info["npmIntegrity"])
        verify.assert_not_called()
        self.assertFalse((state / "runtime").exists())

    def test_stage_is_immutable_idempotent_and_does_not_activate(self):
        state, plugin = self.profile()
        values = self.plan()
        plan, snapshots, _ = values
        with patch.object(runtime, "release_gate"), patch.object(runtime, "verify_node"):
            self.assertEqual(self.stage(values), "staged_not_activated")
            self.assertEqual(self.stage(values), "already_staged")
            destination = Path(plan["runtimeRelease"])
            self.assertEqual((destination / "activation-plan.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual((destination / "bin/openclaw-profile").stat().st_mode & 0o777, 0o700)
            self.assertEqual(os.readlink(plugin / "node_modules/openclaw"), str(self.home / ".npm-global/lib/node_modules/openclaw"))
            for operation in plan["operations"]:
                if operation["kind"].startswith("replace_plist"):
                    self.assertEqual(Path(operation["path"]).read_bytes(), snapshots[operation["snapshot"]]["original"])
            (destination / "node_modules/openclaw/dist/index.js").write_text("tampered")
            with self.assertRaises(runtime.Refusal): self.stage(values)
            self.assertEqual((destination / "node_modules/openclaw/dist/index.js").read_text(), "tampered")
        self.assertTrue((state / "openclaw.json").exists())

    def test_partial_copy_is_rolled_back_without_touching_old_release(self):
        state, _ = self.profile()
        values = self.plan()
        old = state / "runtime/releases/previous"
        old.mkdir(parents=True)
        (old / "preserved").write_text("previous release")
        def fail_copy(source, target, **kwargs):
            target.mkdir()
            (target / "partial").write_text("partial")
            raise OSError("simulated storage failure")
        with patch.object(runtime, "release_gate"), patch.object(runtime, "verify_node"), patch.object(runtime.shutil, "copytree", side_effect=fail_copy):
            with self.assertRaises(OSError): self.stage(values)
        self.assertEqual(list((state / "runtime/releases").iterdir()), [old])
        self.assertEqual((old / "preserved").read_text(), "previous release")

    def test_existing_staged_wrapper_or_snapshot_tampering_is_not_accepted(self):
        self.profile()
        values = self.plan()
        with patch.object(runtime, "release_gate"), patch.object(runtime, "verify_node"):
            self.stage(values)
            destination = Path(values[0]["runtimeRelease"])
            for relative in ("bin/openclaw-profile", "snapshots/ai.openclaw.owlswatch.proposed.plist"):
                with self.subTest(file=relative):
                    path = destination / relative
                    original = path.read_bytes()
                    path.write_bytes(b"tampered")
                    with self.assertRaises(runtime.Refusal): self.stage(values)
                    path.write_bytes(original)

    def test_destination_symlink_refuses_before_writing_other_domain(self):
        state, _ = self.profile()
        values = self.plan()
        other = self.root / "other-domain"
        other.mkdir()
        (state / "runtime").symlink_to(other)
        with patch.object(runtime, "release_gate"), patch.object(runtime, "verify_node"):
            with self.assertRaises(runtime.Refusal): self.stage(values)
        self.assertEqual(list(other.iterdir()), [])

    def test_node_verification_uses_bounded_clean_environment(self):
        with patch.object(runtime.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps([runtime.NODE, "darwin", "arm64"]))) as run:
            runtime.verify_node(Path("/synthetic/node"))
        self.assertEqual(run.call_args.kwargs["timeout"], 10)
        self.assertEqual(run.call_args.kwargs["env"], {"PATH": "/usr/bin:/bin"})
        with patch.object(runtime.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, '["0.0.0","linux","x64"]')):
            with self.assertRaises(runtime.Refusal): runtime.verify_node(Path("/synthetic/node"))

    def test_activate_is_always_refused_without_reading_candidate(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "activate", "--profile", "hotel",
            "--candidate-root", str(self.root / "does-not-exist"), "--home", str(self.home)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Activation unavailable", result.stdout)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_scoped_cli_pins_profile_and_refuses_cross_domain_overrides(self):
        fake_node = self.root / "fake-node"
        fake_node.write_text("#!" + sys.executable + "\nimport json,os,sys\nprint(json.dumps({'argv':sys.argv[1:],'state':os.environ['OPENCLAW_STATE_DIR'],'config':os.environ['OPENCLAW_CONFIG_PATH']}))\n")
        fake_node.chmod(0o700)
        state = self.home / ".openclaw-owlswatch"
        wrapper = self.root / "openclaw-profile"
        wrapper.write_bytes(runtime.scoped_cli(fake_node, self.root / "entry.mjs", state, "owlswatch"))
        def invoke(*args):
            return subprocess.run([sys.executable, str(wrapper), *args], capture_output=True, text=True, timeout=10,
                env={**os.environ, "OPENCLAW_STATE_DIR": "/other-domain", "OPENCLAW_CONFIG_PATH": "/other-domain/config"})
        accepted = invoke("--profile", "owlswatch", "agent", "--message", "synthetic fixture")
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        payload = json.loads(accepted.stdout)
        self.assertEqual(payload["argv"][1:3], ["--profile", "owlswatch"])
        self.assertEqual(payload["state"], str(state))
        self.assertEqual(payload["config"], str(state / "openclaw.json"))
        for arguments in [("--profile", "luna", "status"), ("--profile=hotel", "status"), ("--dev", "status"),
                          ("--config=/other", "status"), ("--state-dir", "/other", "status"), ("update",)]:
            with self.subTest(arguments=arguments):
                self.assertNotEqual(invoke(*arguments).returncode, 0)


if __name__ == "__main__":
    unittest.main()
