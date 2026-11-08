import unittest

from kenpi.voice.audio import AudioUnavailable, pcm_to_wav, wav_to_pcm
from kenpi.voice.engines import EngineUnavailable, SynthesisRequest
from kenpi.voice.moonshine import MoonshineTranscriber, MoonshineTTSEngine


class Line:
    def __init__(self, text):
        self.text = text


class FakeTranscript:
    lines = [Line("hey  kenpi"), Line("is the camera recording")]


class FakeSTT:
    def __init__(self):
        self.calls = []

    def transcribe_without_streaming(self, samples, rate):
        self.calls.append((len(samples), rate))
        return FakeTranscript()


class FakeTTS:
    def __init__(self):
        self.texts = []

    def synthesize(self, text):
        self.texts.append(text)
        return [0.0, 0.5, -0.5, 2.0], 22050


class TranscriberTests(unittest.TestCase):
    def test_transcribes_wav_to_joined_text(self):
        stt = FakeSTT()
        transcriber = MoonshineTranscriber(factory=lambda: stt)
        text = transcriber.transcribe(pcm_to_wav(b"\x00\x01" * 1600))
        self.assertEqual(text, "hey kenpi is the camera recording")
        self.assertEqual(stt.calls, [(1600, 16000)])

    def test_unconfigured_is_unavailable(self):
        transcriber = MoonshineTranscriber()
        self.assertFalse(transcriber.available())
        with self.assertRaises(AudioUnavailable):
            transcriber.transcribe(pcm_to_wav(b"\x00\x00" * 160))

    def test_too_long_audio_rejected(self):
        transcriber = MoonshineTranscriber(factory=FakeSTT)
        with self.assertRaises(ValueError):
            transcriber.transcribe(pcm_to_wav(b"\x00\x00" * 16000 * 31))

    def test_backend_error_becomes_unavailable(self):
        class Broken:
            def transcribe_without_streaming(self, *a):
                raise RuntimeError("boom")

        with self.assertRaises(AudioUnavailable):
            MoonshineTranscriber(factory=Broken).transcribe(
                pcm_to_wav(b"\x00\x00" * 160)
            )


class TTSTests(unittest.TestCase):
    def test_disabled_by_default(self):
        engine = MoonshineTTSEngine()
        self.assertEqual(engine.availability(), (False, "not enabled"))
        with self.assertRaises(EngineUnavailable):
            engine.synthesize(SynthesisRequest("hi"))

    def test_synthesize_returns_clipped_wav_and_caches_engine(self):
        made = []

        def factory(language, voice):
            made.append((language, voice))
            return FakeTTS()

        engine = MoonshineTTSEngine(True, factory)
        audio = engine.synthesize(SynthesisRequest("hi", "en", base_speaker="v1"))
        engine.synthesize(SynthesisRequest("again", "en", base_speaker="v1"))
        pcm, rate = wav_to_pcm(audio)
        self.assertEqual(rate, 22050)
        self.assertEqual(len(pcm), 8)
        self.assertEqual(made, [("en-us", "v1")])

    def test_unsupported_language_and_backend_error(self):
        engine = MoonshineTTSEngine(True, lambda *a: FakeTTS())
        with self.assertRaises(EngineUnavailable):
            engine.synthesize(SynthesisRequest("hi", "xx"))

        class Broken:
            def synthesize(self, text):
                raise RuntimeError("x")

        broken = MoonshineTTSEngine(True, lambda *a: Broken())
        with self.assertRaises(EngineUnavailable):
            broken.synthesize(SynthesisRequest("hi", "en"))


if __name__ == "__main__":
    unittest.main()
