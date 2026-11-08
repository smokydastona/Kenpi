import unittest

from kenpi.core.errors import ProtocolError
from kenpi.vehicle.kline import Style, checksum, decode_pid
from kenpi.vehicle.kline_session import (
    InitError,
    KLineSession,
    START_COMMUNICATION,
    classify_keywords,
)


class FakeEcu:
    """Simulated ECU. Echo is not modelled; the session drains it anyway."""

    def __init__(self, style, *, silent=False):
        self.style = style
        self.silent = silent
        self.rx = bytearray()
        self.pending = bytearray()
        self.tx = bytearray()
        self.events = []

    def set_baud(self, baud):
        self.events.append(("baud", baud))

    def drain(self):
        # Discard the echo, then deliver what the ECU sent in reply.
        self.rx.clear()
        self.rx += self.pending
        self.pending.clear()

    def read(self, count, timeout):
        out = bytes(self.rx[:count])
        del self.rx[:count]
        return out

    def send_five_baud(self, byte):
        self.events.append(("5baud", byte))
        if not self.silent and byte == 0x33:
            kw = b"\x08\x08" if self.style is Style.ISO_9141_2 else b"\x6b\x8f"
            self.pending += b"\x55" + kw

    def pulse_low(self, seconds):
        self.events.append(("pulse", seconds))

    def write(self, data):
        self.tx += data
        t = bytes(self.tx)
        kb2 = 0x08 if self.style is Style.ISO_9141_2 else 0x8F
        if len(t) == 1 and t[0] == (~kb2 & 0xFF):
            self.tx.clear()
            self.pending += b"\xcc"
        elif t == START_COMMUNICATION and not self.silent:
            self.tx.clear()
            body = bytes([0x83, 0xF1, 0x11, 0xC1, 0x6B, 0x8F])
            self.pending += body + bytes([checksum(body)])
        elif self.style is Style.ISO_9141_2 and len(t) == 6 and t[0] == 0x68:
            self.tx.clear()
            self._reply_9141(t)
        elif self.style is Style.ISO_14230 and len(t) >= 5 and t[0] & 0xC0 == 0xC0:
            if len(t) == (t[0] & 0x3F) + 4:
                self.tx.clear()
                self._reply_14230(t)

    def _reply_9141(self, req):
        body = bytes([0x48, 0x6B, 0x10, 0x41, req[4], 0x7B])
        self.pending += body + bytes([checksum(body)])

    def _reply_14230(self, req):
        body = bytes([0x83, 0xF1, 0x10, 0x41, req[4], 0x7B])
        self.pending += body + bytes([checksum(body)])


def session(ecu):
    return KLineSession(ecu, clock=lambda: 0.0, sleep=lambda s: None)


class SessionTests(unittest.TestCase):
    def test_keyword_classification(self):
        self.assertIs(classify_keywords(8, 8), Style.ISO_9141_2)
        self.assertIs(classify_keywords(0x6B, 0x8F), Style.ISO_14230)
        with self.assertRaises(InitError):
            classify_keywords(1, 2)

    def test_five_baud_init_and_read_9141(self):
        ecu = FakeEcu(Style.ISO_9141_2)
        s = session(ecu)
        result = s.init_five_baud()
        self.assertIs(result.style, Style.ISO_9141_2)
        self.assertIn(("5baud", 0x33), ecu.events)
        name, value, unit = decode_pid(s.request(0x01, 0x05))
        self.assertEqual((name, value, unit), ("coolant_temperature", 83.0, "C"))

    def test_fast_init_and_read_14230(self):
        ecu = FakeEcu(Style.ISO_14230)
        s = session(ecu)
        result = s.init_fast()
        self.assertTrue(result.fast)
        self.assertEqual(result.keywords, (0x6B, 0x8F))
        self.assertEqual(s.request(0x01, 0x05).data, bytes([0x41, 0x05, 0x7B]))

    def test_silent_ecu_fails_init(self):
        with self.assertRaises(InitError):
            session(FakeEcu(Style.ISO_14230, silent=True)).init_fast()
        with self.assertRaises(InitError):
            session(FakeEcu(Style.ISO_9141_2, silent=True)).init_five_baud()

    def test_request_needs_init_and_stays_read_only(self):
        ecu = FakeEcu(Style.ISO_14230)
        s = session(ecu)
        with self.assertRaises(ProtocolError):
            s.request(0x01, 0x05)
        s.init_fast()
        with self.assertRaises(ValueError):
            s.request(0x04)  # clear trouble codes is not permitted
        self.assertNotIn(0x04, ecu.tx)


if __name__ == "__main__":
    unittest.main()
