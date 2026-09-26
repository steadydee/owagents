"""Plugin entrypoint regression: config checks plus optional real SDK discovery.

The SDK checks use only discoverConfiguredPluginLoadPaths: no plugin code is
registered, no shared roots are scanned, and HOME/state live in a temporary dir.
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

    def test_profile_examples_select_existing_native_entry_files(self):
        for profile in (ROOT / "openclaw/profiles").glob("*/openclaw.example.json"):
            data = json.loads(profile.read_text())
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
        cls.discovery = None
        for candidate in (package / "dist").glob("discovery-*.js"):
            match = re.search(r"discoverConfiguredPluginLoadPaths as (\w+)", candidate.read_text())
            if match:
                cls.discovery, cls.export = candidate, match.group(1)
                break
        if cls.discovery is None:
            raise AssertionError("Installed OpenClaw discovery export changed; update the compatibility test")

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


if __name__ == "__main__":
    unittest.main()
