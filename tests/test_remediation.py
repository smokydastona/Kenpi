import time
import unittest

from kenpi.core.permissions import Permission
from kenpi.diagnostics.remediation import RemediationAction, RemediationRegistry


def run(**kw):
    reg = RemediationRegistry()
    base = dict(
        id="restart_voice",
        description="x",
        permission=Permission.ASK,
        handler=lambda: "done",
    )
    base.update(kw)
    reg.register(RemediationAction(**base))
    return reg.execute("restart_voice", approved=True)


class RemediationTests(unittest.TestCase):
    def test_precondition_blocks(self) -> None:
        calls = []
        r = run(
            precondition=lambda: "recording in progress",
            handler=lambda: calls.append(1) or "x",
        )
        self.assertFalse(r.executed)
        self.assertEqual(calls, [])
        self.assertIn("recording in progress", r.detail)

    def test_verify_failure_rolls_back(self) -> None:
        undone = []
        r = run(verify=lambda: "still down", rollback=lambda: undone.append(1))
        self.assertFalse(r.executed)
        self.assertEqual((r.verified, r.rolled_back, undone), (False, True, [1]))

    def test_verified_success(self) -> None:
        r = run(verify=lambda: None)
        self.assertEqual((r.executed, r.verified), (True, True))

    def test_timeout_and_exception(self) -> None:
        r = run(handler=lambda: time.sleep(2) or "x", timeout=0.1)
        self.assertEqual((r.executed, r.detail), (False, "action timed out"))

        def boom():
            raise OSError

        undone = []
        r = run(handler=boom, rollback=lambda: undone.append(1))
        self.assertFalse(r.executed)
        self.assertTrue(r.rolled_back)

    def test_approval_still_required(self) -> None:
        reg = RemediationRegistry()
        reg.register(
            RemediationAction("restart_voice", "x", Permission.ASK, lambda: "d")
        )
        self.assertFalse(reg.execute("restart_voice").executed)


if __name__ == "__main__":
    unittest.main()
