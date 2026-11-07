import unittest

from kenpi.camera.lifecycle import CameraLifecycleStateMachine, CameraState


class CameraLifecycleStateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.transitions: list[tuple[CameraState, CameraState]] = []
        self.machine = CameraLifecycleStateMachine(
            lambda old, new: self.transitions.append((old, new))
        )

    def test_initial_state_is_disconnected(self) -> None:
        self.assertIs(self.machine.current_state, CameraState.DISCONNECTED)

    def test_valid_transitions_reach_streaming(self) -> None:
        expected = (
            CameraState.DISCOVERING,
            CameraState.INITIALIZING,
            CameraState.ACTIVE,
            CameraState.STREAMING,
        )
        for state in expected:
            with self.subTest(state=state):
                self.assertTrue(self.machine.transition_to(state))
                self.assertIs(self.machine.current_state, state)

        self.assertEqual(
            len(self.transitions),
            len(expected),
        )

    def test_invalid_transition_does_not_change_state(self) -> None:
        self.assertFalse(self.machine.transition_to(CameraState.STREAMING))
        self.assertIs(self.machine.current_state, CameraState.DISCONNECTED)
        self.assertEqual(self.transitions, [])

    def test_reset_to_disconnected_is_allowed_from_every_state(self) -> None:
        paths = (
            (),
            (CameraState.DISCOVERING,),
            (CameraState.DISCOVERING, CameraState.INITIALIZING),
            (
                CameraState.DISCOVERING,
                CameraState.INITIALIZING,
                CameraState.ACTIVE,
            ),
            (
                CameraState.DISCOVERING,
                CameraState.INITIALIZING,
                CameraState.ACTIVE,
                CameraState.STREAMING,
            ),
        )
        for path in paths:
            with self.subTest(path=path):
                machine = CameraLifecycleStateMachine()
                for state in path:
                    self.assertTrue(machine.transition_to(state))
                self.assertTrue(machine.transition_to(CameraState.DISCONNECTED))
                self.assertIs(machine.current_state, CameraState.DISCONNECTED)

    def test_repeating_current_state_is_idempotent(self) -> None:
        self.assertTrue(self.machine.transition_to(CameraState.DISCONNECTED))
        self.assertEqual(self.transitions, [])

    def test_non_state_input_is_rejected(self) -> None:
        self.assertFalse(self.machine.transition_to("streaming"))  # type: ignore[arg-type]
        self.assertIs(self.machine.current_state, CameraState.DISCONNECTED)

    def test_callback_failure_does_not_undo_transition(self) -> None:
        def fail_callback(old: CameraState, new: CameraState) -> None:
            raise RuntimeError("observer failure")

        machine = CameraLifecycleStateMachine(fail_callback)
        self.assertTrue(machine.transition_to(CameraState.DISCOVERING))
        self.assertIs(machine.current_state, CameraState.DISCOVERING)


if __name__ == "__main__":
    unittest.main()
