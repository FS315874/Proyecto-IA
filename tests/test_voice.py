import io
import logging
import threading
import time
import unittest

from desktop_agent.voice import (
    VoiceBackendResult,
    VoiceController,
    VoiceError,
    VoiceResultStatus,
    VoiceState,
    is_voice_cancel_command,
    normalize_transcript,
)


def ready_result(
    transcript: str = "abrir calculadora",
) -> VoiceBackendResult:
    return VoiceBackendResult(
        VoiceResultStatus.READY,
        transcript,
        0.9,
        "es-UY",
        120,
        15,
    )


class ImmediateBackend:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls = 0

    def recognize(self, cancellation: threading.Event) -> VoiceBackendResult:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class BlockingBackend:
    def __init__(self) -> None:
        self.started = threading.Event()

    def recognize(self, cancellation: threading.Event) -> VoiceBackendResult:
        self.started.set()
        cancellation.wait(2)
        return VoiceBackendResult(
            VoiceResultStatus.CANCELLED,
            None,
            0,
            "none",
            0,
            0,
        )


class VoiceContractTests(unittest.TestCase):
    def test_normalizes_and_recognizes_explicit_cancel_phrase(self) -> None:
        self.assertEqual(
            normalize_transcript("  abrí   la calculadora "),
            "abrí la calculadora",
        )
        self.assertTrue(is_voice_cancel_command("CANCELAR AGENTE"))
        self.assertTrue(is_voice_cancel_command("detener agente"))
        self.assertFalse(is_voice_cancel_command("cancelar la canción"))
        self.assertFalse(is_voice_cancel_command("sí"))

    def test_rejects_empty_oversized_or_invalid_result_contract(self) -> None:
        for transcript in ("", "x" * 501):
            with self.subTest(length=len(transcript)), self.assertRaises(VoiceError):
                normalize_transcript(transcript)
        with self.assertRaises(VoiceError):
            VoiceBackendResult(
                VoiceResultStatus.READY,
                None,
                0.8,
                "es-UY",
                1,
                1,
            )
        with self.assertRaises(VoiceError):
            VoiceBackendResult(
                VoiceResultStatus.NO_SPEECH,
                "unexpected",
                0,
                "es-UY",
                1,
                1,
            )


class VoiceControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stream = io.StringIO()
        self.logger = logging.Logger(self.id())
        self.logger.addHandler(logging.StreamHandler(self.stream))

    @staticmethod
    def wait_for(controller: VoiceController, timeout: float = 2):
        deadline = time.monotonic() + timeout
        updates = []
        while time.monotonic() < deadline:
            updates.extend(controller.drain_updates())
            if any(update.result is not None for update in updates):
                return updates
            time.sleep(0.01)
        raise AssertionError(f"No terminó la voz: {updates!r}")

    def test_explicit_start_emits_listening_then_editable_transcript(self) -> None:
        private = "orden ficticia privada"
        controller = VoiceController(
            ImmediateBackend(ready_result(private)),
            self.logger,
        )

        capture_id = controller.start()
        updates = self.wait_for(controller)

        self.assertEqual(capture_id, "voice-1")
        self.assertEqual(updates[0].state, VoiceState.LISTENING)
        self.assertEqual(updates[-1].state, VoiceState.READY)
        assert updates[-1].result is not None
        self.assertEqual(updates[-1].result.transcript, private)
        self.assertNotIn(private, self.stream.getvalue())
        self.assertTrue(controller.close())

    def test_second_start_is_rejected_while_listening_and_button_cancels(self) -> None:
        backend = BlockingBackend()
        controller = VoiceController(backend, self.logger)
        controller.start()
        self.assertTrue(backend.started.wait(1))

        with self.assertRaises(VoiceError):
            controller.start()
        self.assertTrue(controller.cancel())
        updates = self.wait_for(controller)

        self.assertEqual(updates[-1].state, VoiceState.CANCELLED)
        self.assertFalse(controller.cancel())
        self.assertTrue(controller.close())

    def test_low_confidence_no_speech_and_backend_failure_are_structured(self) -> None:
        cases = (
            VoiceBackendResult(
                VoiceResultStatus.AMBIGUOUS,
                "frase incompleta",
                0.2,
                "es-UY",
                100,
                10,
            ),
            VoiceBackendResult(
                VoiceResultStatus.NO_SPEECH,
                None,
                0,
                "es-UY",
                100,
                0,
            ),
            RuntimeError("PRIVATE-BACKEND-DETAIL"),
        )
        expected = (VoiceState.AMBIGUOUS, VoiceState.ERROR, VoiceState.ERROR)

        for index, (result, state) in enumerate(zip(cases, expected, strict=True)):
            with self.subTest(index=index):
                controller = VoiceController(ImmediateBackend(result), self.logger)
                controller.start()
                updates = self.wait_for(controller)
                self.assertEqual(updates[-1].state, state)
                controller.close()

        self.assertNotIn("PRIVATE-BACKEND-DETAIL", self.stream.getvalue())

    def test_close_cancels_active_capture_and_prevents_restart(self) -> None:
        backend = BlockingBackend()
        controller = VoiceController(backend, self.logger)
        controller.start()
        self.assertTrue(backend.started.wait(1))

        self.assertTrue(controller.close(2))
        self.assertEqual(controller.state, VoiceState.CLOSED)
        with self.assertRaises(VoiceError):
            controller.start()


if __name__ == "__main__":
    unittest.main()
