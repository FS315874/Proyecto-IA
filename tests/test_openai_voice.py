import io
import logging
import tempfile
import threading
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace

from desktop_agent.audio_capture import (
    AudioCaptureCancelled,
    CapturedAudio,
    NoSpeechDetected,
)
from desktop_agent.openai_voice import (
    BudgetedOpenAIVoiceBackend,
    OpenAITranscriptionProvider,
    TranscriptionProviderError,
    TranscriptionResult,
    classify_provider_error,
    estimate_transcription_cost,
)
from desktop_agent.usage_budget import MonthlyUsageLedger
from desktop_agent.voice import VoiceFailureReason, VoiceResultStatus
from desktop_agent.voice_transcription_config import VoiceTranscriptionConfig


def captured_audio(duration: float = 4.0) -> CapturedAudio:
    sample_rate = 8_000
    target = io.BytesIO()
    with wave.open(target, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x01\x00" * int(sample_rate * duration))
    return CapturedAudio(target.getvalue(), duration, duration * 1_000, sample_rate)


class FakeTranscriptions:
    def __init__(self, response=None, error=None) -> None:
        self.response = response
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeRecorder:
    def __init__(self, result, on_capture=None) -> None:
        self.result = result
        self.on_capture = on_capture
        self.calls = 0

    def capture(self, cancellation):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        if self.on_capture is not None:
            self.on_capture(cancellation)
        return self.result


class FakeProvider:
    def __init__(self, result=None, error=None) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    def transcribe(self, audio):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


class FakeStatusError(RuntimeError):
    def __init__(self, status_code: int, private_message: str) -> None:
        self.status_code = status_code
        super().__init__(private_message)


class OpenAIVoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.usage_path = Path(self.temporary_directory.name) / "usage.json"
        self.logger_stream = io.StringIO()
        self.logger = logging.Logger(self.id())
        self.logger.addHandler(logging.StreamHandler(self.logger_stream))

    @staticmethod
    def config() -> VoiceTranscriptionConfig:
        return VoiceTranscriptionConfig(enabled=True, _api_key="test-key")

    def ledger(self, budget=1.0) -> MonthlyUsageLedger:
        return MonthlyUsageLedger(
            budget,
            path=self.usage_path,
            month_provider=lambda: "2026-08",
            reservation_id_factory=lambda: "voice-reservation",
        )

    def test_provider_sends_only_bounded_wav_language_and_model(self) -> None:
        resource = FakeTranscriptions(SimpleNamespace(text="abrí calculadora"))
        client = SimpleNamespace(audio=SimpleNamespace(transcriptions=resource))
        provider = OpenAITranscriptionProvider(self.config(), client=client)

        result = provider.transcribe(captured_audio())

        self.assertEqual(result.transcript, "abrí calculadora")
        self.assertEqual(len(resource.calls), 1)
        call = resource.calls[0]
        self.assertEqual(call["model"], "gpt-transcribe")
        self.assertEqual(call["language"], "es")
        self.assertEqual(call["file"][0], "voice.wav")
        self.assertEqual(call["file"][2], "audio/wav")
        for forbidden in ("prompt", "keywords", "messages", "store"):
            self.assertNotIn(forbidden, call)

    def test_provider_error_is_redacted(self) -> None:
        private = "PRIVATE-PROVIDER-DETAIL"
        resource = FakeTranscriptions(error=FakeStatusError(401, private))
        client = SimpleNamespace(audio=SimpleNamespace(transcriptions=resource))
        provider = OpenAITranscriptionProvider(self.config(), client=client)

        with self.assertRaises(TranscriptionProviderError) as context:
            provider.transcribe(captured_audio())

        self.assertEqual(
            context.exception.reason,
            VoiceFailureReason.AUTHENTICATION,
        )
        self.assertNotIn(private, str(context.exception))

    def test_provider_errors_are_classified_without_parsing_messages(self) -> None:
        cases = (
            (FakeStatusError(401, "private"), VoiceFailureReason.AUTHENTICATION),
            (FakeStatusError(403, "private"), VoiceFailureReason.PERMISSION),
            (FakeStatusError(404, "private"), VoiceFailureReason.MODEL_UNAVAILABLE),
            (
                FakeStatusError(429, "private"),
                VoiceFailureReason.QUOTA_OR_RATE_LIMIT,
            ),
            (FakeStatusError(422, "private"), VoiceFailureReason.REQUEST_REJECTED),
            (
                FakeStatusError(503, "private"),
                VoiceFailureReason.SERVICE_UNAVAILABLE,
            ),
            (TimeoutError("private"), VoiceFailureReason.TIMEOUT),
            (ConnectionError("private"), VoiceFailureReason.NETWORK),
            (RuntimeError("private"), VoiceFailureReason.UNKNOWN),
        )

        for error, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(classify_provider_error(error), expected)

    def test_success_is_metered_by_duration_without_token_invention(self) -> None:
        snapshots = []
        private = "orden de voz privada"
        backend = BudgetedOpenAIVoiceBackend(
            FakeRecorder(captured_audio(4)),
            FakeProvider(TranscriptionResult(private, 120)),
            self.ledger(),
            self.logger,
            usage_callback=snapshots.append,
        )

        result = backend.recognize(threading.Event())

        self.assertEqual(result.status, VoiceResultStatus.READY)
        self.assertEqual(result.transcript, private)
        self.assertIsNone(result.confidence)
        self.assertAlmostEqual(result.estimated_cost_usd, 0.0003)
        self.assertEqual(snapshots[-1].request_count, 1)
        self.assertEqual(snapshots[-1].total_tokens, 0)
        self.assertAlmostEqual(snapshots[-1].estimated_cost_usd, 0.0003)
        self.assertNotIn(private, self.usage_path.read_text(encoding="utf-8"))
        self.assertNotIn(private, self.logger_stream.getvalue())

    def test_silence_and_cancel_never_call_provider_or_create_usage(self) -> None:
        for error, expected in (
            (NoSpeechDetected("none"), VoiceResultStatus.NO_SPEECH),
            (AudioCaptureCancelled("cancel"), VoiceResultStatus.CANCELLED),
        ):
            with self.subTest(expected=expected):
                provider = FakeProvider()
                backend = BudgetedOpenAIVoiceBackend(
                    FakeRecorder(error),
                    provider,
                    self.ledger(),
                    self.logger,
                )

                result = backend.recognize(threading.Event())

                self.assertEqual(result.status, expected)
                self.assertEqual(provider.calls, 0)
                self.assertFalse(self.usage_path.exists())

    def test_budget_blocks_before_upload(self) -> None:
        ledger = self.ledger(budget=0.01)
        reservation_id, _ = ledger.reserve(0.0098)
        ledger.settle(reservation_id, None, 0.0098)
        provider = FakeProvider(TranscriptionResult("texto", 1))
        backend = BudgetedOpenAIVoiceBackend(
            FakeRecorder(captured_audio(4)),
            provider,
            ledger,
            self.logger,
        )

        result = backend.recognize(threading.Event())

        self.assertEqual(result.status, VoiceResultStatus.BUDGET_EXCEEDED)
        self.assertEqual(provider.calls, 0)

    def test_cancel_after_capture_never_reserves_or_uploads(self) -> None:
        provider = FakeProvider(TranscriptionResult("texto", 1))
        backend = BudgetedOpenAIVoiceBackend(
            FakeRecorder(
                captured_audio(4),
                on_capture=lambda cancellation: cancellation.set(),
            ),
            provider,
            self.ledger(),
            self.logger,
        )

        result = backend.recognize(threading.Event())

        self.assertEqual(result.status, VoiceResultStatus.CANCELLED)
        self.assertEqual(provider.calls, 0)
        self.assertFalse(self.usage_path.exists())

    def test_explicit_quota_rejection_releases_budget_and_is_redacted(self) -> None:
        private = "PRIVATE-TRANSCRIPTION-ERROR"
        snapshots = []
        backend = BudgetedOpenAIVoiceBackend(
            FakeRecorder(captured_audio(4)),
            FakeProvider(
                error=TranscriptionProviderError(
                    VoiceFailureReason.QUOTA_OR_RATE_LIMIT
                )
            ),
            self.ledger(),
            self.logger,
            usage_callback=snapshots.append,
        )

        result = backend.recognize(threading.Event())

        self.assertEqual(result.status, VoiceResultStatus.FAILED)
        self.assertIsNone(result.transcript)
        self.assertEqual(
            result.failure_reason,
            VoiceFailureReason.QUOTA_OR_RATE_LIMIT,
        )
        self.assertEqual(snapshots[-1].unmetered_request_count, 0)
        self.assertEqual(snapshots[-1].pending_reservation_count, 0)
        self.assertEqual(snapshots[-1].estimated_cost_usd, 0)
        self.assertEqual(result.estimated_cost_usd, 0)
        self.assertGreater(result.transcription_ms, 0)
        self.assertIn("reason=quota_or_rate_limit", self.logger_stream.getvalue())
        self.assertNotIn(private, self.logger_stream.getvalue())

    def test_timeout_keeps_conservative_estimate_without_transcript(self) -> None:
        ledger = self.ledger()
        backend = BudgetedOpenAIVoiceBackend(
            FakeRecorder(captured_audio(4)),
            FakeProvider(error=TranscriptionProviderError(VoiceFailureReason.TIMEOUT)),
            ledger, self.logger,
        )
        result = backend.recognize(threading.Event())
        self.assertIs(result.status, VoiceResultStatus.FAILED)
        self.assertIsNone(result.transcript)
        self.assertAlmostEqual(result.estimated_cost_usd, 0.0003)
        self.assertGreater(result.transcription_ms, 0)
        self.assertEqual(ledger.current_snapshot().unmetered_request_count, 1)

    def test_cost_uses_documented_per_minute_rate(self) -> None:
        self.assertAlmostEqual(estimate_transcription_cost(10), 0.00075)
        with self.assertRaises(ValueError):
            estimate_transcription_cost(0)


if __name__ == "__main__":
    unittest.main()
