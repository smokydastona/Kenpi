import asyncio
import threading
import time
import unittest

from kenpi.assistant import AssistantService
from kenpi.core.capabilities import INITIAL_CAPABILITIES
from kenpi.intelligence.tools import Tool, cancelled
from kenpi.core.permissions import Permission

CONNECTED = {"link_state": "connected"}


class Link:
    def __init__(self, result=None, delay=0.0):
        self.result = result
        self.delay = delay
        self.calls = []
        self.cancelled = False

    async def diagnose(self, service, pid, timeout=3.0):
        self.calls.append((service, pid))
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return self.result


def make(link, state=CONNECTED):
    return AssistantService(
        None,
        None,
        lambda: {},
        INITIAL_CAPABILITIES,
        vehicle=lambda: state,
        diagnose=link.diagnose if link else None,
    )


async def ask(assistant, text):
    return await asyncio.to_thread(assistant.orchestrator.handle, text, "t")


OK_RPM = {
    "status": "ok",
    "data": "410c1af8",
    "decoded": {"name": "engine_speed", "value": 1726.0, "unit": "rpm"},
}


class VehicleToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_engine_speed_through_policy(self):
        link = Link(OK_RPM)
        assistant = make(link)
        await assistant.start()
        response = await ask(assistant, "what are the rpm")
        await assistant.stop()
        self.assertEqual(link.calls, [(0x01, 0x0C)])
        self.assertIn("1726", response.message)
        self.assertTrue(response.outcomes[0].executed)

    async def test_ecu_failure_is_not_reported_as_a_value(self):
        link = Link({"status": "timeout", "data": None})
        assistant = make(link)
        await assistant.start()
        response = await ask(assistant, "what is the coolant temperature")
        await assistant.stop()
        self.assertIn("did not return", response.message)
        self.assertNotIn("°", response.message)

    async def test_refused_when_controller_disconnected(self):
        link = Link(OK_RPM)
        assistant = make(link, {"link_state": "no_data"})
        await assistant.start()
        response = await ask(assistant, "what is the engine speed")
        await assistant.stop()
        self.assertEqual(link.calls, [])
        self.assertFalse(response.outcomes[0].executed)

    async def test_unconfigured_vehicle_is_refused(self):
        assistant = make(None)
        await assistant.start()
        response = await ask(assistant, "what is the engine speed")
        await assistant.stop()
        self.assertFalse(response.outcomes[0].executed)

    async def test_stop_cancels_in_flight_read(self):
        link = Link(OK_RPM, delay=30)
        assistant = make(link)
        await assistant.start()
        task = asyncio.create_task(ask(assistant, "what are the rpm"))
        await asyncio.sleep(0.4)
        await assistant.stop()
        response = await asyncio.wait_for(task, 3)
        await asyncio.sleep(0.1)
        self.assertFalse(response.outcomes[0].executed)
        self.assertTrue(link.cancelled)


class CancellationTests(unittest.TestCase):
    def test_timeout_signals_cooperative_handler(self):
        assistant = make(None)
        seen = threading.Event()

        def slow(_):
            while not cancelled():
                time.sleep(0.01)
            seen.set()
            return {"message": "late"}

        assistant.tools.register(
            Tool("slow_probe", "x", Permission.AUTOMATIC, slow, timeout=0.2)
        )
        outcome = assistant.orchestrator._execute("slow_probe", {}, False, True)
        self.assertFalse(outcome.executed)
        self.assertIn("timed out", outcome.reason)
        self.assertTrue(seen.wait(2))

    def test_cancel_running_with_nothing_active(self):
        self.assertEqual(make(None).orchestrator.cancel_running(), 0)


if __name__ == "__main__":
    unittest.main()
