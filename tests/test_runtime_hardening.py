import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("hardening", Path(__file__).resolve().parents[1] / "scripts/harden-runtime.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RuntimeHardeningTests(unittest.TestCase):
    def test_preserves_credentials_routing_and_model_and_disables_only_native_duplicates(self):
        before = {"agents": {"defaults": {"model": "provider/current"}, "list": [{"id": "correo", "tools": {"alsoAllow": ["read"]}}]},
                  "channels": {"telegram": {"allowFrom": ["user"], "botToken": "fixture"}},
                  "mcp": {"servers": {"registro_compliance": {"env": {"SECRET": "fixture"}}, "unrelated": {"enabled": True}}},
                  "plugins": {"entries": {"registro-compliance": {"enabled": True}}, "allow": ["registro-compliance"]}}
        result = module.harden(before, "/profile/tools/guard")
        self.assertEqual(result["channels"], before["channels"])
        self.assertEqual(result["agents"]["defaults"]["model"], "provider/current")
        self.assertEqual(result["mcp"]["servers"]["registro_compliance"]["env"], {"SECRET": "fixture"})
        self.assertFalse(result["mcp"]["servers"]["registro_compliance"]["enabled"])
        self.assertTrue(result["mcp"]["servers"]["unrelated"]["enabled"])
        self.assertEqual(result["agents"]["defaults"]["heartbeat"]["every"], "0m")
        self.assertEqual(result, module.harden(result, "/profile/tools/guard"))
        self.assertNotIn("heartbeat", before["agents"]["defaults"])

    def test_does_not_disable_mcp_without_enabled_plugin(self):
        result = module.harden({"mcp": {"servers": {"registro_compliance": {"enabled": True}}}}, "/guard")
        self.assertTrue(result["mcp"]["servers"]["registro_compliance"]["enabled"])

    def test_keyed_agents_preserve_identity_and_hardening_is_idempotent(self):
        guard = "/profile/tools/agent_runtime_guard"
        before = {"agents": {"entries": {"correo": {"model": "provider/current", "heartbeat": {"every": "15m"},
                   "tools": {"alsoAllow": ["owlswatch_email_search"]}}, "cuenta": {"workspace": "/private/receipts"}}},
                  "plugins": {"load": {"paths": [guard, guard + "/openclaw-plugin.js"]}}}
        after = module.harden(before, guard)
        self.assertNotIn("list", after["agents"])
        self.assertEqual(after["agents"]["entries"]["correo"]["heartbeat"]["every"], "0m")
        self.assertIn("owlswatch_email_acknowledge_item", after["agents"]["entries"]["correo"]["tools"]["alsoAllow"])
        self.assertNotIn("id", after["agents"]["entries"]["correo"])
        self.assertNotIn("default", after["agents"]["entries"]["correo"])
        self.assertEqual(after["agents"]["entries"]["cuenta"], before["agents"]["entries"]["cuenta"])
        self.assertEqual(after["plugins"]["load"]["paths"], [guard + "/openclaw-plugin.js"])
        self.assertEqual(module.harden(after, guard), after)
        self.assertEqual(before["agents"]["entries"]["correo"]["heartbeat"]["every"], "15m")

    def test_ambiguous_identity_does_not_produce_a_hardening_candidate(self):
        for agents in ({"entries": {"correo": {}}, "list": []},
                       {"entries": {"correo": {"id": "cuenta"}}},
                       {"list": [{"id": "correo"}, {"id": "correo"}]}):
            with self.assertRaises(ValueError):
                module.harden({"agents": agents}, "/profile/guard")


if __name__ == "__main__":
    unittest.main()
