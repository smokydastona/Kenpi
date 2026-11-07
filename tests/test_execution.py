import time
import unittest

from kenpi.core.permissions import Permission
from kenpi.intelligence.orchestrator import Orchestrator
from kenpi.intelligence.planner import Planner
from kenpi.intelligence.tools import Tool, ToolRegistry


def build(*tools):
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return Orchestrator(registry, Planner(registry))


class ExecutionTests(unittest.TestCase):
    def test_timeout_does_not_block_and_is_recorded(self) -> None:
        orch = build(
            Tool(
                "slow",
                "x",
                Permission.AUTOMATIC,
                lambda a: time.sleep(2) or {},
                timeout=0.1,
            )
        )
        start = time.monotonic()
        out = orch._execute("slow", {}, False, True)
        self.assertLess(time.monotonic() - start, 1)
        self.assertFalse(out.executed)
        self.assertIn("timed out", out.reason)
        self.assertEqual(orch.history[-1]["tool"], "slow")

    def test_verification_failure_is_not_success(self) -> None:
        tool = Tool(
            "t",
            "x",
            Permission.AUTOMATIC,
            lambda a: {"ok": False},
            verify=lambda r: None if r["ok"] else "state unchanged",
        )
        out = build(tool)._execute("t", {}, False, True)
        self.assertFalse(out.executed)
        self.assertIn("state unchanged", out.reason)

    def test_history_has_arg_names_not_values(self) -> None:
        tool = Tool("t", "x", Permission.AUTOMATIC, lambda a: {}, args={"k": str})
        orch = build(tool)
        orch._execute("t", {"k": "secret-value"}, False, True)
        self.assertEqual(orch.history[-1]["arg_names"], ["k"])
        self.assertNotIn("secret-value", str(orch.history[-1]))

    def test_exception_is_failure(self) -> None:
        def boom(a):
            raise RuntimeError("x")

        out = build(Tool("t", "x", Permission.AUTOMATIC, boom))._execute(
            "t", {}, False, True
        )
        self.assertFalse(out.executed)


if __name__ == "__main__":
    unittest.main()
