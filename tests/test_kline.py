import unittest

from kenpi.core.errors import ProtocolError
from kenpi.vehicle.kline import (
    Style,
    checksum,
    decode_pid,
    decode_response,
    encode_request,
)


def frame(prefix: bytes) -> bytes:
    return prefix + bytes([checksum(prefix)])


class KLineTests(unittest.TestCase):
    def test_known_request_frames(self) -> None:
        # 68 6A F1 01 00 is the standard ISO 9141-2 "supported PIDs" request; checksum C4.
        self.assertEqual(encode_request(Style.ISO_9141_2, 1, 0).hex(), "686af10100c4")
        self.assertEqual(
            encode_request(Style.ISO_14230, 1, 0x0C).hex(),
            "c233f1010c" + "%02x" % checksum(bytes.fromhex("c233f1010c")),
        )

    def test_only_read_only_services_can_be_encoded(self) -> None:
        for service in (0x04, 0x08, 0x10, 0x11, 0x27, 0x2E, 0x31, 0x34, 0x3B, 0x85):
            with self.assertRaises(ValueError):
                encode_request(Style.ISO_9141_2, service)

    def test_decode_values(self) -> None:
        rpm = decode_response(
            Style.ISO_9141_2, frame(bytes([0x48, 0x6B, 0x11, 0x41, 0x0C, 0x1A, 0xF8]))
        )
        self.assertEqual(decode_pid(rpm), ("engine_speed", 1726.0, "rpm"))
        coolant = decode_response(
            Style.ISO_14230, frame(bytes([0x83, 0xF1, 0x11, 0x41, 0x05, 0x7B]))
        )
        self.assertEqual(decode_pid(coolant), ("coolant_temperature", 83.0, "C"))

    def test_rejects_bad_frames(self) -> None:
        good = frame(bytes([0x48, 0x6B, 0x11, 0x41, 0x0D, 0x3C]))
        corrupted = good[:-1] + bytes([good[-1] ^ 1])
        for bad in (b"", good[:4], corrupted):
            with self.assertRaises(ProtocolError):
                decode_response(Style.ISO_9141_2, bad)
        wrong_length = frame(bytes([0x85, 0xF1, 0x11, 0x41, 0x0D, 0x3C]))
        with self.assertRaises(ProtocolError):
            decode_response(Style.ISO_14230, wrong_length)
        negative = frame(bytes([0x48, 0x6B, 0x11, 0x7F, 0x01, 0x12]))
        with self.assertRaisesRegex(ProtocolError, "negative"):
            decode_response(Style.ISO_9141_2, negative)

    def test_undecoded_pid_is_an_error_not_a_guess(self) -> None:
        response = decode_response(
            Style.ISO_9141_2, frame(bytes([0x48, 0x6B, 0x11, 0x41, 0x99, 0x01]))
        )
        with self.assertRaises(ProtocolError):
            decode_pid(response)
