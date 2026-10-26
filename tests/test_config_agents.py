"""Config-only checks: deployment shell scripts and the runtime are never run."""
import copy
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from config_agents import configured_agents, single_agent_section, uat_agent_format


def convert_to_keyed(profile):
    profile = copy.deepcopy(profile)
    entries = {}
    for agent in profile["agents"].pop("list"):
        identity = agent.pop("id")
        agent.pop("default", None)
        entries[identity] = agent
    profile["agents"]["entries"] = entries
    return profile


def run_config_block(script, *args):
    # Execute only the first embedded config-validation/update block, never the
    # enclosing deploy script (which copies source and operates live services).
    source = re.search(r"<<'PY'\n(.*?)\nPY", (ROOT / "scripts" / script).read_text(), re.S).group(1)
    return subprocess.run([sys.executable, "-c", source, *map(str, args)],
                          capture_output=True, text=True, timeout=10)


class ConfigAgentsTests(unittest.TestCase):
    def test_both_formats_return_original_entries_without_injecting_identity(self):
        for section in ({"list": [{"id": "nomina", "tools": {}}]},
                        {"entries": {"nomina": {"tools": {}}}}):
            with self.subTest(section=section):
                profile = {"agents": section}
                before = copy.deepcopy(profile)
                agent = configured_agents(profile)["nomina"]
                self.assertEqual(profile, before)
                agent["tools"]["profile"] = "minimal"
                self.assertEqual(configured_agents(profile)["nomina"]["tools"]["profile"], "minimal")
                if "entries" in section:
                    self.assertNotIn("id", agent)

    def test_invalid_or_ambiguous_identity_is_rejected(self):
        cases = [[], {"agents": []}, {"agents": {"entries": []}},
                 {"agents": {"entries": {"": {}}}}, {"agents": {"entries": {"nomina": None}}},
                 {"agents": {"entries": {"nomina": {"id": "cuenta"}}}},
                 {"agents": {"entries": {"nomina": {}}, "list": []}},
                 {"agents": {"list": [{"id": "nomina"}, {"id": "nomina"}]}},
                 {"agents": {"list": [{}]}}]
        for profile in cases:
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                configured_agents(profile)

    def test_disposable_uat_config_uses_requested_schema_and_copies_input(self):
        agent = {"id": "source", "default": True, "workspace": "/synthetic", "tools": {"profile": "minimal"}}
        defaults = {"model": {"primary": "provider/model"}}
        before = copy.deepcopy((agent, defaults))
        keyed = single_agent_section("cuenta", agent, defaults, "entries")
        self.assertEqual(set(keyed), {"defaults", "entries"})
        self.assertNotIn("id", keyed["entries"]["cuenta"])
        self.assertNotIn("default", keyed["entries"]["cuenta"])
        legacy = single_agent_section("cuenta", agent, defaults, "list")
        self.assertEqual(legacy["list"][0]["id"], "cuenta")
        self.assertTrue(legacy["list"][0]["default"])
        keyed["defaults"]["model"]["primary"] = "changed"
        self.assertEqual((agent, defaults), before)
        with self.assertRaises(ValueError):
            single_agent_section("cuenta", agent, defaults, "unknown")

    def test_uat_schema_detection_is_local_and_fails_closed_for_wrappers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "package.json"
            executable = root / "openclaw.mjs"
            executable.touch()
            link = root / "command"
            link.symlink_to(executable)
            for version, expected in (("2026.7.1-2", "list"), ("2026.10.5", "entries"), ("2026.11.1", "entries")):
                package.write_text(json.dumps({"name": "openclaw", "version": version}))
                self.assertEqual(uat_agent_format(link), expected)
            package.unlink()
            with self.assertRaisesRegex(ValueError, "OPENCLAW_UAT_AGENT_FORMAT"):
                uat_agent_format(link)
            self.assertEqual(uat_agent_format(link, "entries"), "entries")
            with self.assertRaises(ValueError):
                uat_agent_format(link, "guess")


class DeploymentConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def nomina_fixture(self, keyed):
        workspace = self.root / ("keyed" if keyed else "legacy")
        workspace.mkdir(mode=0o700, exist_ok=True)
        config_path = workspace / "payroll-config.json"
        config = {"schema_version": 1, "enabled": True, "telegram": {
            "allowed_sender_ids": ["101"], "allowed_routes": [{"chat_id": "-201", "thread_id": ""}]}}
        config_path.write_text(json.dumps(config))
        config_path.chmod(0o600)
        profile = json.loads((ROOT / "openclaw/profiles/nomina/openclaw.example.json").read_text())
        profile["agents"]["list"][0]["workspace"] = str(workspace)
        channel = profile["channels"]["telegram"]
        channel.update({"enabled": True, "allowFrom": ["101"], "botToken": "fixture" * 4})
        profile["gateway"]["auth"]["token"] = "fixture" * 4
        profile["env"]["vars"].update({"OWLSWATCH_PAYROLL_WORKSPACE": str(workspace),
                                           "OWLSWATCH_PAYROLL_PYTHON": str(workspace / ".venv/bin/python3")})
        if keyed:
            profile = convert_to_keyed(profile)
        return workspace, config_path, workspace / "openclaw.json", profile

    def run_nomina_check(self, workspace, config, path, profile):
        path.write_text(json.dumps(profile))
        path.chmod(0o600)
        return run_config_block("deploy-nomina-to-mac-mini.sh", workspace, config, path, "0", ROOT)

    def test_nomina_preflight_preserves_route_policy_and_isolation_for_both_formats(self):
        for keyed in (False, True):
            workspace, config, path, profile = self.nomina_fixture(keyed)
            with self.subTest(keyed=keyed):
                result = self.run_nomina_check(workspace, config, path, profile)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(path.read_text()), profile)
                wrong_route = copy.deepcopy(profile)
                wrong_route["channels"]["telegram"]["allowFrom"] = ["999"]
                result = self.run_nomina_check(workspace, config, path, wrong_route)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("allowlists must match", result.stderr)
                expanded = copy.deepcopy(profile)
                configured_agents(expanded)["nomina"]["tools"]["alsoAllow"].append("exec")
                result = self.run_nomina_check(workspace, config, path, expanded)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("authority differs", result.stderr)
                wrong_workspace = copy.deepcopy(profile)
                configured_agents(wrong_workspace)["nomina"]["workspace"] = str(workspace / "other")
                result = self.run_nomina_check(workspace, config, path, wrong_workspace)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("workspace differs", result.stderr)

    def test_cobros_config_update_retains_representation_and_unrelated_agent(self):
        for keyed in (False, True):
            profile = {"agents": {"list": [{"id": "cobros", "tools": {"alsoAllow": ["fixture_read"]}},
                                            {"id": "cuenta", "workspace": "/synthetic"}]},
                       "channels": {"telegram": {"allowFrom": ["101"]}}}
            if keyed:
                profile = convert_to_keyed(profile)
            path = self.root / "config.json"
            path.write_text(json.dumps(profile))
            result = run_config_block("deploy-cobros.sh", ROOT, path)
            self.assertEqual(result.returncode, 0, result.stderr)
            after = json.loads(path.read_text())
            self.assertEqual(set(after["agents"]), set(profile["agents"]))
            agents = configured_agents(after)
            self.assertEqual(agents["cuenta"], configured_agents(profile)["cuenta"])
            self.assertEqual(agents["cobros"]["tools"]["alsoAllow"], ["fixture_read"])
            self.assertTrue(agents["cobros"]["tools"]["loopDetection"]["enabled"])
            self.assertEqual(after["channels"], profile["channels"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_cobros_ambiguous_identity_is_rejected_before_writing(self):
        path = self.root / "config.json"
        profile = {"agents": {"list": [{"id": "cobros", "tools": {}}], "entries": {"cobros": {"tools": {}}}}}
        original = json.dumps(profile)
        path.write_text(original)
        result = run_config_block("deploy-cobros.sh", ROOT, path)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(path.read_text(), original)
        self.assertFalse(path.with_suffix(".cobros-deploy.tmp").exists())


if __name__ == "__main__":
    unittest.main()
