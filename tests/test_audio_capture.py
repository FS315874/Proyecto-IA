import io
import threading
import unittest
import wave

from desktop_agent.audio_capture import (
    AudioCaptureCancelled,
    AudioCaptureError,
    NoSpeechDetected,
    SoundDeviceWavRecorder,
)


def pcm_block(value: int, frames: int) -> bytes:
    return int(value).to_bytes(2, "little", signed=True) * frames


class FakeStream:
    def __init__(self, blocks, on_read=None) -> None:
        self.blocks = list(blocks)
        self.on_read = on_read
        self.read_calls = 0
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def read(self, frames):
        self.read_calls += 1
        if self.on_read is not None:
            self.on_read(self.read_calls)
        if not self.blocks:
            return pcm_block(0, frames), False
        return self.blocks.pop(0), False


class FailingStream(FakeStream):
    def read(self, frames):
        raise RuntimeError("PRIVATE-DEVICE-DETAIL")


class OverflowingStream(FakeStream):
    def read(self, frames):
        return pcm_block(2_000, frames), True


class AudioCaptureTests(unittest.TestCase):
    sample_rate = 8_000
    block_frames = 400

    def recorder(self, stream):
        return SoundDeviceWavRecorder(
            max_seconds=1,
            initial_silence_seconds=0.25,
            trailing_silence_seconds=0.1,
            preroll_seconds=0.05,
            block_seconds=0.05,
            speech_rms_threshold=100,
            sample_rate_provider=lambda: self.sample_rate,
            stream_factory=lambda _rate, _block: stream,
        )

    def test_captures_one_phrase_and_encodes_bounded_mono_wav(self) -> None:
        silence = pcm_block(0, self.block_frames)
        voice = pcm_block(2_000, self.block_frames)
        stream = FakeStream(
            [silence, voice, voice, voice, silence, silence]
        )

        captured = self.recorder(stream).capture(threading.Event())

        self.assertTrue(stream.closed)
        self.assertGreater(captured.capture_ms, 0)
        self.assertAlmostEqual(captured.duration_seconds, 0.20)
        with wave.open(io.BytesIO(captured.wav_bytes), "rb") as wav:
            self.assertEqual(wav.getnchannels(), 1)
            self.assertEqual(wav.getsampwidth(), 2)
            self.assertEqual(wav.getframerate(), self.sample_rate)
            self.assertEqual(wav.getnframes(), 1_600)

    def test_silence_never_produces_an_uploadable_wav(self) -> None:
        silence = pcm_block(0, self.block_frames)
        stream = FakeStream([silence] * 8)

        with self.assertRaises(NoSpeechDetected):
            self.recorder(stream).capture(threading.Event())

        self.assertTrue(stream.closed)

    def test_finish_keeps_phrase_and_resets_meter(self) -> None:
        levels = []
        stream = FakeStream([pcm_block(2000, self.block_frames)] * 10)
        recorder = self.recorder(stream)
        self.assertFalse(recorder.finish())

        def on_read(call):
            if call == 4:
                levels.append(recorder.level)
                self.assertTrue(recorder.recording)
                self.assertEqual(recorder.phase, "recording")
                self.assertTrue(recorder.finish())

        stream.on_read = on_read
        audio = recorder.capture(threading.Event())
        self.assertEqual(stream.read_calls, 4)
        self.assertGreater(len(audio.wav_bytes), 44)
        self.assertGreater(levels[0], 0)
        self.assertFalse(recorder.recording)
        self.assertEqual(recorder.level, 0)
        self.assertEqual(recorder.phase, "finished")
        self.assertTrue(stream.closed)

    def test_cancellation_closes_stream_and_discards_partial_audio(self) -> None:
        cancellation = threading.Event()
        voice = pcm_block(2_000, self.block_frames)
        stream = FakeStream(
            [voice] * 8,
            on_read=lambda call: cancellation.set() if call == 1 else None,
        )

        with self.assertRaises(AudioCaptureCancelled):
            self.recorder(stream).capture(cancellation)

        self.assertTrue(stream.closed)

    def test_device_error_is_redacted(self) -> None:
        stream = FailingStream([])

        with self.assertRaises(AudioCaptureError) as context:
            self.recorder(stream).capture(threading.Event())

        self.assertNotIn("PRIVATE-DEVICE-DETAIL", str(context.exception))

    def test_overflow_discards_incomplete_audio(self) -> None:
        stream = OverflowingStream([])

        with self.assertRaises(AudioCaptureError):
            self.recorder(stream).capture(threading.Event())

        self.assertTrue(stream.closed)


if __name__ == "__main__":
    unittest.main()
