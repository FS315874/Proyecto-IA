import json
import subprocess
import threading
import unittest
from pathlib import Path

from desktop_agent.voice import VoiceResultStatus
from desktop_agent.windows_speech import WindowsSpeechBackend


def payload(
    status: str = "ready",
    transcript: str = "abrir calculadora",
    confidence: float = 0.9,
    culture: str = "es-UY",
) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "status": status,
            "transcript": transcript,
            "confidence": confidence,
            "culture": culture,
            "capture_ms": 120.0,
            "transcription_ms": 10.0,
        }
    )


class FakeProcess:
    def __init__(
        self,
        stdout: str = "",
        timeouts: int = 0,
        on_timeout=None,
    ) -> None:
        self.stdout = stdout
        self.timeouts = timeouts
        self.on_timeout = on_timeout
        self.terminated = False
        self.killed = False

    def communicate(self, timeout=None):
        if self.timeouts > 0:
            self.timeouts -= 1
            if self.on_timeout is not None:
                self.on_timeout()
            raise subprocess.TimeoutExpired("voice-helper", timeout)
        return self.stdout, "PRIVATE-STDERR-IGNORED"

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True


class MutableClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class WindowsSpeechBackendTests(unittest.TestCase):
    def backend(self, process, **options):
        self.command = None

        def factory(command):
            self.command = command
            return process

        existing = Path(__file__).resolve()
        return WindowsSpeechBackend(
            script_path=existing,
            powershell_path=existing,
            process_factory=factory,
            **options,
        )

    def test_parses_strict_local_result_and_uses_only_fixed_command(self) -> None:
        private = "orden de voz ficticia privada"
        backend = self.backend(FakeProcess(payload(transcript=private)))

        result = backend.recognize(threading.Event())

        self.assertEqual(result.status, VoiceResultStatus.READY)
        self.assertEqual(result.transcript, private)
        assert self.command is not None
        self.assertEqual(self.command[-4:], ("-Culture", "es-UY", "-MaxSeconds", "10"))
        self.assertNotIn(private, self.command)
        self.assertNotIn("-Command", self.command)

    def test_low_confidence_is_ambiguous_and_remains_editable(self) -> None:
        backend = self.backend(
            FakeProcess(payload(confidence=0.3)),
            min_confidence=0.55,
        )

        result = backend.recognize(threading.Event())

        self.assertEqual(result.status, VoiceResultStatus.AMBIGUOUS)
        self.assertEqual(result.transcript, "abrir calculadora")

    def test_no_speech_and_unavailable_are_structured(self) -> None:
        no_speech = self.backend(
            FakeProcess(payload("no_speech", "", 0, "es-UY"))
        ).recognize(threading.Event())
        missing = Path(__file__).with_name("missing-helper.ps1")
        unavailable = WindowsSpeechBackend(
            script_path=missing,
            powershell_path=missing,
            process_factory=lambda _: FakeProcess(),
        ).recognize(threading.Event())

        self.assertEqual(no_speech.status, VoiceResultStatus.NO_SPEECH)
        self.assertEqual(unavailable.status, VoiceResultStatus.UNAVAILABLE)

    def test_cancellation_terminates_helper_without_partial_text(self) -> None:
        cancellation = threading.Event()
        process = FakeProcess(
            payload(transcript="partial private text"),
            timeouts=1,
            on_timeout=cancellation.set,
        )
        backend = self.backend(process)

        result = backend.recognize(cancellation)

        self.assertEqual(result.status, VoiceResultStatus.CANCELLED)
        self.assertIsNone(result.transcript)
        self.assertTrue(process.terminated)

    def test_timeout_terminates_helper_and_fails_closed(self) -> None:
        clock = MutableClock()
        process = FakeProcess(
            payload(),
            timeouts=1,
            on_timeout=lambda: clock.advance(20),
        )
        backend = self.backend(process, max_seconds=1, clock=clock)

        result = backend.recognize(threading.Event())

        self.assertEqual(result.status, VoiceResultStatus.FAILED)
        self.assertTrue(process.terminated)

    def test_malformed_unknown_or_private_error_output_is_redacted(self) -> None:
        invalid_outputs = (
            "not-json PRIVATE-DETAIL",
            json.dumps({"schema_version": 1}),
            payload(status="unknown"),
            payload(transcript="x" * 501),
        )
        for index, output in enumerate(invalid_outputs):
            with self.subTest(index=index):
                result = self.backend(FakeProcess(output)).recognize(
                    threading.Event()
                )
                self.assertEqual(result.status, VoiceResultStatus.FAILED)
                self.assertIsNone(result.transcript)

    def test_invalid_configuration_is_rejected_before_process(self) -> None:
        existing = Path(__file__).resolve()
        invalid_options = (
            {"culture": "en-US"},
            {"max_seconds": 0},
            {"min_confidence": 2},
        )
        for options in invalid_options:
            with self.subTest(options=options), self.assertRaises(ValueError):
                WindowsSpeechBackend(
                    script_path=existing,
                    powershell_path=existing,
                    **options,
                )


if __name__ == "__main__":
    unittest.main()
