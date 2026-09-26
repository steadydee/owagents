import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch


def load(name):
    path = Path(__file__).resolve().parents[1] / "tools" / name / "server.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OutboundBoundaryTests(unittest.TestCase):
    def test_finca_rejects_redirect_before_any_network_call(self):
        server = load("finca_tasks")
        with patch.object(server, "notify_chat_id", return_value="staff"), patch.object(server, "http_json") as send:
            with self.assertRaises(server.ToolError):
                server.telegram_send({}, "elsewhere", "content")
            send.assert_not_called()

    def test_registro_rejects_chat_and_topic_redirect_before_network(self):
        server = load("registro_compliance")
        with patch.object(server, "load_config", return_value={}), patch.object(server, "default_chat_id", return_value="staff"), patch.object(server, "default_thread_id", return_value="topic"), patch.object(server, "http_json") as send:
            for args in [{"chat_id": "elsewhere"}, {"message_thread_id": "elsewhere"}]:
                with self.assertRaises(server.ToolError):
                    server.tool_registro_telegram_notify({"text": "content", **args})
            send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
