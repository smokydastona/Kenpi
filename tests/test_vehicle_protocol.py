import unittest

from kenpi.core.errors import ProtocolError
from kenpi.vehicle.protocol import (
    SequenceTracker,
    VehicleMessage,
    VehicleMessageDecoder,
    encode_message,
)


class VehicleProtocolTests(unittest.TestCase):
    def test_round_trip_across_partial_uart_reads(self) -> None:
        expected = VehicleMessage(
            message_type=2, sequence=65535, payload=b"ignition:on"
        )
        encoded = encode_message(expected)
        decoder = VehicleMessageDecoder()
        self.assertEqual(decoder.feed(encoded[:5]), ())
        self.assertEqual(decoder.feed(encoded[5:]), (expected,))

    def test_corrupt_frame_is_discarded_and_next_valid_frame_survives(self) -> None:
        invalid = bytearray(encode_message(VehicleMessage(1, 1, b"bad")))
        invalid[-1] ^= 0xFF
        valid = VehicleMessage(2, 2, b"ok")
        decoder = VehicleMessageDecoder()
        self.assertEqual(decoder.feed(bytes(invalid) + encode_message(valid)), (valid,))

    def test_sequence_tracker_handles_wraparound_and_rejects_replay(self) -> None:
        tracker = SequenceTracker()
        self.assertTrue(tracker.accept(65535))
        self.assertTrue(tracker.accept(0))
        self.assertFalse(tracker.accept(0))
        self.assertFalse(tracker.accept(65535))

    def test_invalid_sequence_is_rejected(self) -> None:
        with self.assertRaises(ProtocolError):
            SequenceTracker().accept(65536)


if __name__ == "__main__":
    unittest.main()
