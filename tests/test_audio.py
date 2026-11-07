import math
import stat
import struct
import tempfile
import unittest
from pathlib import Path

from kenpi.voice.audio import (
    AudioUnavailable,
    WhisperCppTranscriber,
    run_listener,
    contains_wake_word,
    discover_devices,
    pcm_to_wav,
    segment_utterances,
    strip_wake_word,
    wav_to_pcm,
)

FRAME = 480  # 30 ms at 16 kHz


def tone(frames, amp):
    out = []
    for f in range(frames):
        out.append(
            struct.pack(
                f"<{FRAME}h",
                *(
                    int(amp * math.sin(2 * math.pi * 440 * (f * FRAME + i) / 16000))
                    for i in range(FRAME)
                ),
            )
        )
    return out


class VadTests(unittest.TestCase):
    def test_segments_speech_between_silence(self) -> None:
        stream = (
            tone(20, 0) + tone(15, 4000) + tone(30, 0) + tone(12, 4000) + tone(30, 0)
        )
        found = list(segment_utterances(stream))
        self.assertEqual(len(found), 2)
        self.assertGreater(len(found[0]), 15 * FRAME * 2 - 1)

    def test_silence_and_blips_produce_nothing(self) -> None:
        self.assertEqual(list(segment_utterances(tone(50, 0))), [])
        self.assertEqual(list(segment_utterances(tone(1, 4000) + tone(40, 0))), [])

    def test_wav_roundtrip_and_validation(self) -> None:
        pcm = b"".join(tone(3, 1000))
        self.assertEqual(wav_to_pcm(pcm_to_wav(pcm)), (pcm, 16000))
        with self.assertRaises(ValueError):
            wav_to_pcm(b"not a wav")

    def test_wake_word(self) -> None:
        words = ["hey kenpi"]
        self.assertTrue(contains_wake_word("Hey, Kenpi show status", words))
        self.assertFalse(contains_wake_word("show status", words))
        self.assertEqual(
            strip_wake_word("Hey Kenpi, show status.", words), "show status"
        )


class ListenerTests(unittest.TestCase):
    def test_wake_word_gates_commands_and_failures_are_skipped(self) -> None:
        stream = (
            tone(20, 0)
            + tone(15, 4000)
            + tone(30, 0)
            + tone(15, 4000)
            + tone(30, 0)
            + tone(15, 4000)
            + tone(30, 0)
        )
        replies = iter(["hey kenpi status please", "just chatter", None])

        def transcribe(_):
            reply = next(replies)
            if reply is None:
                raise AudioUnavailable("down")
            return reply

        got = []
        count = run_listener(stream, transcribe, got.append, ["hey kenpi"])
        self.assertEqual((count, got), (1, ["status please"]))


class ToolTests(unittest.TestCase):
    def test_missing_arecord_is_unavailable(self) -> None:
        with self.assertRaises(AudioUnavailable):
            discover_devices("definitely-not-installed")

    def test_whisper_runs_real_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            script = Path(d) / "whisper"
            script.write_text('#!/bin/sh\necho "  hello   there "\n')
            script.chmod(script.stat().st_mode | stat.S_IEXEC)
            model = Path(d) / "m.bin"
            model.write_bytes(b"x")
            audio = pcm_to_wav(b"".join(tone(3, 1000)))
            self.assertEqual(
                WhisperCppTranscriber(str(script), model).transcribe(audio),
                "hello there",
            )
            with self.assertRaises(AudioUnavailable):
                WhisperCppTranscriber(str(script), Path(d) / "none").transcribe(audio)
            script.write_text("#!/bin/sh\nexit 3\n")
            with self.assertRaises(AudioUnavailable):
                WhisperCppTranscriber(str(script), model).transcribe(audio)


if __name__ == "__main__":
    unittest.main()
