import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch, MagicMock
spec = importlib.util.spec_from_file_location("cobros", Path(__file__).resolve().parents[1] / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

class RetrievalTests(unittest.TestCase):
    def test_large_thread_is_bounded(self):
        thread = {"threadId": "19c8acf805edfd93", "sourceUrl": "https://example.test", "rawText": "x" * 90000,
                  "messages": [{"bodyText": "x" * 30000, "attachments": []} for _ in range(20)]}
        result = server.bounded_thread_preview(thread)
        self.assertTrue(result["truncated"])
        self.assertNotIn("rawText", result)
        self.assertLess(len(json.dumps(result)), 25000)

    def test_skill_is_not_a_thread(self):
        with self.assertRaises(server.ToolError):
            server.tool_read_gmail_thread({"threadId": "skill"})

    def test_identity_lookup_is_targeted(self):
        service = MagicMock()
        service.users.return_value.messages.return_value.list.return_value.execute.return_value = {"messages": []}
        with patch.object(server, "google_build_service", return_value=service), patch.object(server, "load_config", return_value={}):
            result = server.tool_search_gmail_threads({"query": '"Example agency"', "purpose": "billing_identity"})
        self.assertNotIn("cuenta de cobro", result["query"])
        self.assertIn("Example agency", result["query"])
        with self.assertRaises(server.ToolError):
            server.tool_search_gmail_threads({"purpose": "billing_identity", "query": "NIT"})
        with self.assertRaises(server.ToolError):
            server.tool_search_gmail_threads({"maxResults": 20})

if __name__ == "__main__":
    unittest.main()
