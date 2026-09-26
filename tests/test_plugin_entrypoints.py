"""Plugin entrypoint and hook-permission regression against the actual SDK.

Discovery checks do not register code. Registration checks load only an isolated
copy of our guard through loadOpenClawPlugins, with no model/tool invocation.
No shared roots are scanned, and HOME/state live in a temporary directory.
They skip on CI hosts without OpenClaw; configuration contract checks always run.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("entrypoint_hardening", ROOT / "scripts/harden-runtime.py")
hardening = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hardening)


class EntrypointContractTests(unittest.TestCase):
    def test_hardening_pins_known_native_entries_and_is_idempotent(self):
        packages = ["agent_runtime_guard", "hotel_pms", "finca_tasks", "owlswatch_cobros",
                    "owlswatch_email", "owlswatch_intake", "owlswatch_quotes", "registro_compliance"]
        paths = [f"/isolated/tools/{name}" for name in packages]
        paths.append("/isolated/custom-extension")
        config = {"plugins": {"load": {"paths": paths}}}
        result = hardening.harden(config, paths[0])
        actual = result["plugins"]["load"]["paths"]
        for name in packages:
            expected = f"/isolated/tools/{name}/openclaw-plugin.js"
            self.assertEqual(actual.count(expected), 1)
            self.assertNotIn(f"/isolated/tools/{name}", actual)
        self.assertIn("/isolated/custom-extension", actual)
        self.assertEqual(hardening.harden(result, paths[0]), result)
        self.assertEqual(config["plugins"]["load"]["paths"], paths)
        self.assertIs(result["plugins"]["entries"][hardening.GUARD_ID]["hooks"]["allowConversationAccess"], True)

    def test_profile_examples_select_existing_native_entry_files(self):
        for profile in (ROOT / "openclaw/profiles").glob("*/openclaw.example.json"):
            data = json.loads(profile.read_text())
            self.assertIs(data["plugins"]["entries"][hardening.GUARD_ID]["hooks"]["allowConversationAccess"], True)
            for configured in data.get("plugins", {}).get("load", {}).get("paths", []):
                with self.subTest(profile=profile.parent.name, path=configured):
                    self.assertEqual(Path(configured).name, "openclaw-plugin.js")
                    package = Path(configured).parent.name
                    self.assertTrue((ROOT / "tools" / package / "openclaw-plugin.js").is_file())


class InstalledOpenClawDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        binary = shutil.which("openclaw")
        if not binary or not shutil.which("node"):
            raise unittest.SkipTest("OpenClaw/Node unavailable; configuration contract tests still run")
        package = Path(binary).resolve().parent
        cls.package = package
        cls.discovery = None
        for candidate in (package / "dist").glob("discovery-*.js"):
            match = re.search(r"discoverConfiguredPluginLoadPaths as (\w+)", candidate.read_text())
            if match:
                cls.discovery, cls.export = candidate, match.group(1)
                break
        if cls.discovery is None:
            raise AssertionError("Installed OpenClaw discovery export changed; update the compatibility test")
        cls.loader = None
        for candidate in (package / "dist").glob("loader-*.js"):
            match = re.search(r"loadOpenClawPlugins as (\w+)", candidate.read_text())
            if match:
                cls.loader, cls.loader_export = candidate, match.group(1)
                break
        if cls.loader is None:
            raise AssertionError("Installed OpenClaw runtime loader export changed; update the compatibility test")

    def discover(self, paths, sandbox):
        script = """
import { pathToFileURL } from 'node:url';
const [modulePath, exportName, paths] = process.argv.slice(1);
const sdk = await import(pathToFileURL(modulePath).href);
const result = sdk[exportName]({ loadPaths: JSON.parse(paths), env: process.env });
console.log(JSON.stringify({sources: result.candidates.map(c => c.source), diagnostics: result.diagnostics}));
"""
        env = {**os.environ, "HOME": str(sandbox), "OPENCLAW_STATE_DIR": str(sandbox / "state"),
               "OPENCLAW_CONFIG_PATH": str(sandbox / "no-config.json")}
        result = subprocess.run(["node", "--input-type=module", "-e", script, str(self.discovery), self.export,
                                 json.dumps([str(path) for path in paths])], env=env,
                                capture_output=True, text=True, check=True, timeout=30)
        return json.loads(result.stdout)

    def test_explicit_real_entries_discover_one_correct_candidate_per_package(self):
        entries = sorted((ROOT / "tools").glob("*/openclaw-plugin.js"))
        self.assertTrue(entries)
        with tempfile.TemporaryDirectory() as tmp:
            result = self.discover(entries, Path(tmp))
        self.assertEqual(sorted(Path(source).resolve() for source in result["sources"]),
                         sorted(path.resolve() for path in entries))
        self.assertEqual(result["diagnostics"], [])

    def test_directory_scans_helpers_but_explicit_file_or_package_extensions_select_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp)
            plugin = sandbox / "fixture"
            plugin.mkdir()
            entry = plugin / "openclaw-plugin.js"
            entry.write_text("export default { register() {} };\n")
            helper = plugin / "aaa-helper.mjs"
            helper.write_text("export const helper = true;\n")
            (plugin / "openclaw.plugin.json").write_text(json.dumps({"id": "fixture", "configSchema": {"type": "object"}}))
            # A plugin manifest supplies identity, not an entrypoint. Directory
            # discovery produces both candidates, allowing a helper to shadow it.
            directory = self.discover([plugin], sandbox)
            self.assertEqual({Path(source).name for source in directory["sources"]}, {helper.name, entry.name})
            explicit = self.discover([entry], sandbox)
            self.assertEqual([Path(source).name for source in explicit["sources"]], [entry.name])
            (plugin / "package.json").write_text(json.dumps({"name": "fixture", "version": "1.0.0",
                "openclaw": {"extensions": ["./openclaw-plugin.js"]}}))
            declared = self.discover([plugin], sandbox)
            self.assertEqual([Path(source).name for source in declared["sources"]], [entry.name])
            self.assertEqual(declared["diagnostics"], [])

    def load_guard(self, allow_conversation):
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp)
            plugin = sandbox / "agent_runtime_guard"
            shutil.copytree(ROOT / "tools/agent_runtime_guard", plugin,
                            ignore=shutil.ignore_patterns("tests", "node_modules", "__pycache__"))
            (plugin / "node_modules").mkdir()
            (plugin / "node_modules/openclaw").symlink_to(self.package, target_is_directory=True)
            config = hardening.harden({"plugins": {"allow": [hardening.GUARD_ID]}}, str(plugin / "openclaw-plugin.js"))
            if not allow_conversation:
                config["plugins"]["entries"][hardening.GUARD_ID].pop("hooks", None)
            script = """
import { pathToFileURL } from 'node:url';
const [discoveryPath, discoveryExport, loaderPath, loaderExport, configText] = process.argv.slice(1);
const discoveryModule = await import(pathToFileURL(discoveryPath).href);
const loaderModule = await import(pathToFileURL(loaderPath).href);
const config = JSON.parse(configText);
const discovery = discoveryModule[discoveryExport]({ loadPaths: config.plugins.load.paths, env: process.env });
const registry = loaderModule[loaderExport]({
  config, env: process.env, discovery, onlyPluginIds: ['owlswatch-runtime-guard'],
  cache: false, activate: false, loadModules: true, mode: 'full',
  logger: {debug() {}, info() {}, warn() {}, error() {}},
});
console.log(JSON.stringify({
  plugins: registry.plugins.map(p => ({id: p.id, status: p.status})),
  hooks: registry.typedHooks.map(h => ({pluginId: h.pluginId, name: h.hookName})),
  diagnostics: registry.diagnostics,
}));
"""
            # Do not inherit provider credentials or live profile selection.
            env = {"PATH": os.environ.get("PATH", ""), "HOME": str(sandbox),
                   "OPENCLAW_STATE_DIR": str(sandbox / "state"),
                   "OPENCLAW_CONFIG_PATH": str(sandbox / "no-config.json")}
            result = subprocess.run(["node", "--input-type=module", "-e", script,
                                     str(self.discovery), self.export, str(self.loader), self.loader_export,
                                     json.dumps(config)], env=env, capture_output=True, text=True,
                                    check=True, timeout=30)
            return json.loads(result.stdout)

    def test_runtime_registers_all_five_guard_hooks_with_trusted_conversation_access(self):
        result = self.load_guard(allow_conversation=True)
        self.assertEqual(result["plugins"], [{"id": hardening.GUARD_ID, "status": "loaded"}])
        self.assertEqual({hook["name"] for hook in result["hooks"]},
                         {"before_agent_run", "before_tool_call", "model_call_ended", "llm_output", "agent_end"})
        self.assertTrue(all(hook["pluginId"] == hardening.GUARD_ID for hook in result["hooks"]))
        self.assertEqual(result["diagnostics"], [])

    def test_loaded_status_alone_does_not_prove_conversation_hooks_registered(self):
        result = self.load_guard(allow_conversation=False)
        self.assertEqual(result["plugins"], [{"id": hardening.GUARD_ID, "status": "loaded"}])
        self.assertEqual({hook["name"] for hook in result["hooks"]}, {"before_tool_call", "model_call_ended"})
        blocked = [item for item in result["diagnostics"] if "allowConversationAccess=true" in item.get("message", "")]
        self.assertEqual(len(blocked), 3)


if __name__ == "__main__":
    unittest.main()
