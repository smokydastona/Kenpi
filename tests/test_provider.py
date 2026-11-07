import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from kenpi.core.permissions import Permission
from kenpi.intelligence.orchestrator import Orchestrator
from kenpi.intelligence.planner import Planner
from kenpi.intelligence.provider import ChatCompletionsProvider
from kenpi.intelligence.tools import Tool, ToolRegistry

REPLY = {"content": "[]"}
SEEN = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"data":[]}')

    def do_POST(self):
        SEEN.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        body = json.dumps(
            {"choices": [{"message": {"content": REPLY["content"]}}]}
        ).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)


class ProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.ran = []
        registry = ToolRegistry()
        registry.register(
            Tool(
                "ping",
                "ping",
                Permission.AUTOMATIC,
                lambda a: self.ran.append(a) or {"message": "pong"},
            )
        )
        registry.register(
            Tool(
                "clear_assistant_cache",
                "clear",
                Permission.ASK,
                lambda a: self.ran.append("c") or {"message": "c"},
            )
        )
        self.registry = registry
        self.provider = ChatCompletionsProvider(self.url, "m")
        self.orch = Orchestrator(registry, Planner(registry, self.provider))

    def test_health(self):
        self.assertTrue(self.provider.health()[0])
        self.assertFalse(
            ChatCompletionsProvider("http://127.0.0.1:1", "m", 1).health()[0]
        )

    def test_model_proposal_runs_through_policy(self):
        REPLY["content"] = 'Sure: [{"tool":"ping","args":{}}]'
        self.assertEqual(self.orch.handle("zxq blorp").message, "pong")

    def test_model_cannot_self_authorize(self):
        REPLY["content"] = '[{"tool":"clear_assistant_cache","args":{"approved":true}}]'
        response = self.orch.handle("zxq blorp")
        self.assertEqual(self.ran, [])
        REPLY["content"] = '[{"tool":"clear_assistant_cache","args":{}}]'
        response = self.orch.handle("zxq blorp")
        self.assertTrue(response.outcomes[0].needs_approval)
        self.assertEqual(self.ran, [])

    def test_garbage_and_unknown_tools_fall_back(self):
        for content in ("no json", '[{"tool":"rm_rf","args":{}}]', "{}"):
            REPLY["content"] = content
            self.assertIn("not sure", self.orch.handle("zxq blorp").message)

    def test_unreachable_model_falls_back_to_rules(self):
        dead = ChatCompletionsProvider("http://127.0.0.1:1", "m", 1)
        orch = Orchestrator(self.registry, Planner(self.registry, dead))
        self.assertIn("not sure", orch.handle("zxq blorp").message)

    def test_user_text_is_bounded_and_marked_untrusted(self):
        REPLY["content"] = "[]"
        SEEN.clear()
        self.provider.propose("x" * 5000, self.registry.describe())
        messages = SEEN[-1]["messages"]
        self.assertEqual(len(messages[1]["content"]), 500)
        self.assertIn("untrusted", messages[0]["content"])

    def test_remote_requires_https_and_opt_in(self):
        for url, remote in (
            ("http://10.0.0.5", True),
            ("https://10.0.0.5", False),
            ("ftp://x", False),
        ):
            with self.assertRaises(ValueError):
                ChatCompletionsProvider(url, "m", allow_remote=remote)
        ChatCompletionsProvider("https://10.0.0.5", "m", allow_remote=True)


if __name__ == "__main__":
    unittest.main()
