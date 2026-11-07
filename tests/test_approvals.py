import unittest

from kenpi.core.permissions import Permission
from kenpi.intelligence.approvals import ApprovalStore
from kenpi.intelligence.orchestrator import Orchestrator
from kenpi.intelligence.planner import Planner
from kenpi.intelligence.tools import Tool, ToolRegistry


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.ran = []
        registry = ToolRegistry()
        registry.register(
            Tool(
                "clear_assistant_cache",
                "clear",
                Permission.ASK,
                lambda a: self.ran.append(a) or {"message": "cleared"},
            )
        )
        self.orch = Orchestrator(
            registry, Planner(registry), approvals=ApprovalStore(60, self.clock)
        )

    def _request(self, session="s"):
        # Drive the policy path directly: the planner rule set is not under test here.
        return self.orch._execute("clear_assistant_cache", {}, False, True, session)

    def test_request_creates_binding_and_does_not_run(self):
        outcome = self._request()
        self.assertTrue(outcome.needs_approval)
        self.assertIsNotNone(outcome.approval_id)
        self.assertEqual(self.ran, [])

    def test_approval_is_single_use(self):
        approval_id = self._request().approval_id
        self.assertTrue(self.orch.approve(approval_id).executed)
        self.assertFalse(self.orch.approve(approval_id).executed)
        self.assertEqual(len(self.ran), 1)

    def test_approval_expires(self):
        approval_id = self._request().approval_id
        self.clock.now = 61
        self.assertFalse(self.orch.approve(approval_id).executed)
        self.assertEqual(self.ran, [])

    def test_unknown_id_rejected(self):
        self.assertFalse(self.orch.approve("guess").executed)

    def test_never_tools_get_no_approval(self):
        registry = ToolRegistry()
        registry.register(Tool("stop_recording", "x", Permission.NEVER, lambda a: {}))
        orch = Orchestrator(registry, Planner(registry))
        outcome = orch._execute("stop_recording", {}, False, True)
        self.assertIsNone(outcome.approval_id)
        self.assertEqual(orch.pending_approvals(), [])

    def test_describe_does_not_leak_approval_id(self):
        from kenpi.intelligence.orchestrator import Response
        from kenpi.intelligence.planner import Plan

        outcome = self._request()
        text = str(Response("m", Plan((), 0.0, None, None), (outcome,)).describe())
        self.assertNotIn(outcome.approval_id, text)


if __name__ == "__main__":
    unittest.main()
