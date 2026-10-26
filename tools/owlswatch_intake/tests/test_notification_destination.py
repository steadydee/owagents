"""Notification authorization tests: network calls are always mocked."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("intake_destination_test", Path(__file__).resolve().parents[1] / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class QuoteDestinationTests(unittest.TestCase):
    def setUp(self):
        self.config = {"channels": {"telegram": {"groups": {
            "-100123": {"topics": {"3": {"agentId": "cotiza"}, "2": {"agentId": "cuenta"}}}
        }}}}
        self.network = patch.object(server, "telegram_api", return_value={"message_id": 9, "message_thread_id": 3}).start()
        patch.object(server, "load_config", side_effect=lambda: self.config).start()
        self.addCleanup(patch.stopall)

    def test_configured_destination_and_reply_are_preserved(self):
        result = server.tool_telegram_send_message({"chat_id": -100123, "message_thread_id": 3, "reply_to_message_id": 7, "text": "Ready"})
        self.assertTrue(result["ok"])
        self.assertEqual(self.network.call_args.args[1], {"chat_id": "-100123", "message_thread_id": "3", "reply_to_message_id": "7", "text": "Ready"})

    def test_omitted_topic_is_pinned_not_sent_to_group_root(self):
        server.tool_telegram_send_message({"chat_id": "-100123", "text": "Ready"})
        self.assertEqual(self.network.call_args.args[1]["message_thread_id"], "3")

    def test_model_cannot_change_group_topic_or_use_alias(self):
        for change in [{"chat_id": "-100999"}, {"chat_id": "@other"}, {"message_thread_id": 2}, {"message_thread_id": True}]:
            with self.subTest(change=change), self.assertRaises(server.ToolError) as error:
                server.tool_telegram_send_message({"chat_id": "-100123", "text": "Ready", **change})
            self.assertEqual(error.exception.code, "destination_not_allowed")
        self.network.assert_not_called()

    def test_missing_disabled_ambiguous_or_invalid_route_fails_closed(self):
        valid = copy.deepcopy(self.config)
        cases = [{}, {"channels": {"telegram": {"enabled": False}}}]
        disabled = copy.deepcopy(valid)
        disabled["channels"]["telegram"]["groups"]["-100123"]["topics"]["3"]["enabled"] = False
        cases.append(disabled)
        duplicate = copy.deepcopy(valid)
        duplicate["channels"]["telegram"]["groups"]["-100123"]["topics"]["4"] = {"agentId": "cotiza"}
        cases.append(duplicate)
        cases.append({"channels": {"telegram": {"groups": {"<group>": {"topics": {"3": {"agentId": "cotiza"}}}}}}})
        for config in cases:
            self.config = config
            with self.subTest(config=config), self.assertRaises(server.ToolError):
                server.tool_telegram_send_message({"chat_id": "-100123", "text": "Ready"})
        self.network.assert_not_called()

    def test_only_cotiza_receives_shared_notification_grant(self):
        import json
        root = Path(__file__).resolve().parents[3]
        profile = json.loads((root / "openclaw/profiles/owlswatch/openclaw.example.json").read_text())
        granted = [a["id"] for a in profile["agents"]["list"] if "owlswatch_telegram_send_message" in a.get("tools", {}).get("alsoAllow", [])]
        self.assertEqual(granted, ["cotiza"])
