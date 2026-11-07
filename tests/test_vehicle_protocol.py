import unittest

from kenpi.core.errors import ProtocolError
from kenpi.vehicle.protocol import (
    MAX_PAYLOAD_LENGTH,
    SequenceTracker,
    VehicleMessage,
    VehicleMessageDecoder,
    _crc16,
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

    def test_crc16_ccitt_false_known_vectors(self) -> None:
        self.assertEqual(_crc16(b"123456789"), 0x29B1)
        self.assertEqual(_crc16(b""), 0xFFFF)
        # Independently calculated CCITT-FALSE checksum, not an encoder round trip.
        frame = bytes.fromhex("4b500102ffff00007e35")
        expected = VehicleMessage(2, 65535)
        self.assertEqual(encode_message(expected), frame)
        self.assertEqual(VehicleMessageDecoder().feed(frame), (expected,))

    def test_payload_and_integer_boundaries(self) -> None:
        for expected in (
            VehicleMessage(0, 0),
            VehicleMessage(255, 65535, b"x" * MAX_PAYLOAD_LENGTH),
        ):
            self.assertEqual(
                VehicleMessageDecoder().feed(encode_message(expected)), (expected,)
            )
        for message_type, sequence, payload, version in (
            (-1, 0, b"", 1),
            (256, 0, b"", 1),
            (0, -1, b"", 1),
            (0, 65536, b"", 1),
            (0, 0, b"x" * (MAX_PAYLOAD_LENGTH + 1), 1),
            (0, 0, b"", 0),
            (0, 0, b"", 2),
        ):
            with self.subTest(
                message_type=message_type,
                sequence=sequence,
                length=len(payload),
                version=version,
            ):
                with self.assertRaises(ValueError):
                    VehicleMessage(message_type, sequence, payload, version)

    def test_every_uart_split_including_crc_and_magic(self) -> None:
        expected = VehicleMessage(255, 42, b"embedded:KP\x00")
        frame = encode_message(expected)
        for split in range(len(frame) + 1):
            with self.subTest(split=split):
                decoder = VehicleMessageDecoder()
                messages = decoder.feed(frame[:split]) + decoder.feed(frame[split:])
                self.assertEqual(messages, (expected,))
        decoder = VehicleMessageDecoder()
        messages = ()
        for byte in frame:
            messages += decoder.feed(bytes((byte,)))
        self.assertEqual(messages, (expected,))

    def test_noise_and_multiple_frames_resynchronize(self) -> None:
        first = VehicleMessage(0, 0, b"")
        second = VehicleMessage(3, 1, b"data")
        decoder = VehicleMessageDecoder()
        self.assertEqual(decoder.feed(b"noiseKK"), ())
        self.assertEqual(
            decoder.feed(encode_message(first)[1:] + encode_message(second)),
            (first, second),
        )
        self.assertEqual(decoder.feed(b""), ())

    def test_oversize_header_and_bad_crc_resynchronize(self) -> None:
        valid = VehicleMessage(4, 7, b"ok")
        oversize = bytes.fromhex("4b50010000000201")
        bad_crc = bytearray(encode_message(VehicleMessage(0, 2)))
        bad_crc[-2] ^= 1
        decoder = VehicleMessageDecoder()
        self.assertEqual(
            decoder.feed(oversize + bytes(bad_crc) + encode_message(valid)), (valid,)
        )

    def test_unsupported_version_with_valid_crc_is_discarded(self) -> None:
        # Version 2, zero-length payload, independently calculated CRC.
        invalid = bytes.fromhex("4b500200000000007096")
        self.assertEqual(_crc16(invalid[:-2]), int.from_bytes(invalid[-2:], "big"))
        valid = VehicleMessage(1, 3)
        decoder = VehicleMessageDecoder()
        self.assertEqual(decoder.feed(invalid + encode_message(valid)), (valid,))

    def test_truncated_frame_waits_for_payload_and_crc(self) -> None:
        valid = VehicleMessage(3, 12, b"hello")
        encoded = encode_message(valid)
        decoder = VehicleMessageDecoder()
        self.assertEqual(decoder.feed(encoded[:-2]), ())
        self.assertEqual(decoder.feed(encoded[-2:-1]), ())
        self.assertEqual(decoder.feed(encoded[-1:]), (valid,))

    def test_sequence_half_range_boundary_does_not_advance_on_rejection(self) -> None:
        tracker = SequenceTracker()
        self.assertTrue(tracker.accept(0))
        self.assertFalse(tracker.accept(0x8000))
        self.assertFalse(tracker.accept(0xFFFF))
        self.assertTrue(tracker.accept(0x7FFF))
        self.assertFalse(tracker.accept(0xFFFF))
        self.assertTrue(tracker.accept(0x8000))
        for sequence in (-1, 65536):
            with self.assertRaises(ProtocolError):
                tracker.accept(sequence)
        self.assertTrue(tracker.accept(0x8001))


if __name__ == "__main__":
    unittest.main()
