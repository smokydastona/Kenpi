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

    def test_connection_failure_can_restart_discovery(self) -> None:
        for state in (
            CameraState.DISCOVERING,
            CameraState.INITIALIZING,
            CameraState.ACTIVE,
            CameraState.STREAMING,
        ):
            machine = CameraLifecycleStateMachine()
            self.assertTrue(machine.transition_to(CameraState.DISCOVERING))
            if state is not CameraState.DISCOVERING:
                self.assertTrue(machine.transition_to(CameraState.INITIALIZING))
            if state in {CameraState.ACTIVE, CameraState.STREAMING}:
                self.assertTrue(machine.transition_to(CameraState.ACTIVE))
            if state is CameraState.STREAMING:
                self.assertTrue(machine.transition_to(CameraState.STREAMING))
            self.assertTrue(machine.transition_to(CameraState.CONNECTION_FAILED))
            self.assertTrue(machine.transition_to(CameraState.RECONNECTING))
            self.assertTrue(machine.transition_to(CameraState.DISCOVERING))
            self.assertIs(machine.current_state, CameraState.DISCOVERING)

    def test_initialization_failures_and_session_expiry_are_recoverable(self) -> None:
        cases = (
            (CameraState.DISCOVERING, CameraState.CONNECTION_FAILED),
            (CameraState.INITIALIZING, CameraState.AUTHENTICATION_FAILED),
            (CameraState.ACTIVE, CameraState.SESSION_EXPIRED),
            (CameraState.STREAMING, CameraState.SESSION_EXPIRED),
        )
        for stage, failure in cases:
            with self.subTest(stage=stage, failure=failure):
                machine = CameraLifecycleStateMachine()
                self.assertTrue(machine.transition_to(CameraState.DISCOVERING))
                if stage is not CameraState.DISCOVERING:
                    self.assertTrue(machine.transition_to(CameraState.INITIALIZING))
                if stage in {CameraState.ACTIVE, CameraState.STREAMING}:
                    self.assertTrue(machine.transition_to(CameraState.ACTIVE))
                if stage is CameraState.STREAMING:
                    self.assertTrue(machine.transition_to(CameraState.STREAMING))
                self.assertTrue(machine.transition_to(failure))
                self.assertTrue(machine.transition_to(CameraState.RECONNECTING))
                self.assertTrue(machine.transition_to(CameraState.DISCOVERING))

    def test_degraded_transport_can_recover_to_prior_operating_phase(self) -> None:
        for operating_state in (CameraState.ACTIVE, CameraState.STREAMING):
            with self.subTest(operating_state=operating_state):
                machine = CameraLifecycleStateMachine()
                for state in (
                    CameraState.DISCOVERING,
                    CameraState.INITIALIZING,
                    CameraState.ACTIVE,
                ):
                    self.assertTrue(machine.transition_to(state))
                if operating_state is CameraState.STREAMING:
                    self.assertTrue(machine.transition_to(CameraState.STREAMING))
                self.assertTrue(machine.transition_to(CameraState.DEGRADED))
                self.assertTrue(machine.transition_to(operating_state))
                self.assertIs(machine.current_state, operating_state)

    def test_failure_state_can_reset_then_restart_from_disconnected(self) -> None:
        self.assertTrue(self.machine.transition_to(CameraState.DISCOVERING))
        self.assertTrue(self.machine.transition_to(CameraState.CONNECTION_FAILED))
        self.assertTrue(self.machine.transition_to(CameraState.DISCONNECTED))
        self.assertTrue(self.machine.transition_to(CameraState.DISCOVERING))

    def test_shutdown_is_graceful_and_terminal(self) -> None:
        paths_to_states = {
            CameraState.DISCONNECTED: (),
            CameraState.DISCOVERING: (CameraState.DISCOVERING,),
            CameraState.INITIALIZING: (
                CameraState.DISCOVERING,
                CameraState.INITIALIZING,
            ),
            CameraState.ACTIVE: (
                CameraState.DISCOVERING,
                CameraState.INITIALIZING,
                CameraState.ACTIVE,
            ),
            CameraState.STREAMING: (
                CameraState.DISCOVERING,
                CameraState.INITIALIZING,
                CameraState.ACTIVE,
                CameraState.STREAMING,
            ),
            CameraState.CONNECTION_FAILED: (
                CameraState.DISCOVERING,
                CameraState.CONNECTION_FAILED,
            ),
            CameraState.AUTHENTICATION_FAILED: (
                CameraState.DISCOVERING,
                CameraState.INITIALIZING,
                CameraState.AUTHENTICATION_FAILED,
            ),
            CameraState.RECONNECTING: (
                CameraState.DISCOVERING,
                CameraState.CONNECTION_FAILED,
                CameraState.RECONNECTING,
            ),
            CameraState.SESSION_EXPIRED: (
                CameraState.DISCOVERING,
                CameraState.INITIALIZING,
                CameraState.ACTIVE,
                CameraState.SESSION_EXPIRED,
            ),
            CameraState.DEGRADED: (
                CameraState.DISCOVERING,
                CameraState.INITIALIZING,
                CameraState.ACTIVE,
                CameraState.DEGRADED,
            ),
        }
        for expected_state, path in paths_to_states.items():
            with self.subTest(expected_state=expected_state):
                machine = CameraLifecycleStateMachine()
                for state in path:
                    self.assertTrue(machine.transition_to(state))
                self.assertIs(machine.current_state, expected_state)
                self.assertTrue(machine.transition_to(CameraState.SHUTTING_DOWN))
                self.assertTrue(machine.transition_to(CameraState.SHUTDOWN))
                self.assertFalse(machine.transition_to(CameraState.DISCOVERING))
                self.assertIs(machine.current_state, CameraState.SHUTDOWN)

    def test_shutdown_cannot_skip_graceful_phase(self) -> None:
        self.assertFalse(self.machine.transition_to(CameraState.SHUTDOWN))
        self.assertIs(self.machine.current_state, CameraState.DISCONNECTED)
        self.assertTrue(self.machine.transition_to(CameraState.SHUTTING_DOWN))
        self.assertTrue(self.machine.transition_to(CameraState.SHUTDOWN))
        self.assertFalse(self.machine.transition_to(CameraState.DISCOVERING))
        self.assertIs(self.machine.current_state, CameraState.SHUTDOWN)

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
