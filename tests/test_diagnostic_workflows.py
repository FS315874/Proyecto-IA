import logging
import tempfile
import threading
import unittest
from pathlib import Path

from desktop_agent.command_processor import CommandProcessor
from desktop_agent.error_history import ErrorHistory, ErrorHistoryHandler
from desktop_agent.executor import ActionExecutionError, ActionExecutor
from desktop_agent.interpretation import HybridInterpreter
from desktop_agent.models import ToolResult
from desktop_agent.parser import parse_command
from desktop_agent.voice import VoiceController, VoiceBackendResult, VoiceResultStatus, VoiceFailureReason


class DiagnosticWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.history = ErrorHistory(Path(self.temporary.name) / "history.sqlite3")
        self.logger = logging.Logger(self.id())
        self.logger.addHandler(ErrorHistoryHandler(self.history))

    def test_failed_tool_is_recorded_once_not_again_by_command_processor(self):
        executor = ActionExecutor({"open_application": lambda **_: ToolResult(False, "private reason", "app_missing")}, self.logger)
        result = CommandProcessor(executor, HybridInterpreter(), self.logger).execute("abrir calculadora", output=lambda _: None)
        self.assertFalse(result.success)
        incident, = self.history.list()
        self.assertEqual(incident.code, "app_missing")
        self.assertNotIn(b"private reason", self.history.path.read_bytes())

    def test_unsupported_command_is_recorded_without_command_text(self):
        result = CommandProcessor(ActionExecutor({}, self.logger), HybridInterpreter(), self.logger).execute("un pedido privado", output=lambda _: None)
        self.assertFalse(result.success)
        incident, = self.history.list()
        self.assertEqual(incident.code, "unsupported")
        self.assertNotIn(b"privado", self.history.path.read_bytes())

    def test_success_and_cancelled_interpretation_are_not_errors(self):
        executor = ActionExecutor({"open_application": lambda **_: ToolResult(True, "ok")}, self.logger)
        processor = CommandProcessor(executor, HybridInterpreter(), self.logger)
        self.assertTrue(processor.execute("abrir calculadora", output=lambda _: None).success)
        self.assertTrue(processor.execute("not supported", output=lambda _: None, cancelled=lambda: True).cancelled)
        self.assertEqual(self.history.list(), ())

    def test_exception_and_invalid_tool_contract_are_classified(self):
        def fail(**_):
            raise RuntimeError("secret not to persist")
        for tool, expected in ((fail, "tool_exception"), (lambda **_: None, "invalid_tool_result")):
            with self.assertRaises(ActionExecutionError):
                ActionExecutor({"open_application": tool}, self.logger).execute(parse_command("abrir calculadora"))
            self.assertEqual(self.history.list()[0].code, expected)
        self.assertNotIn(b"secret", self.history.path.read_bytes())

    def test_voice_failure_reason_is_preserved_without_audio(self):
        class Backend:
            def recognize(self, cancellation):
                return VoiceBackendResult(VoiceResultStatus.FAILED, None, None, "es-UY", 100, 200,
                                          failure_reason=VoiceFailureReason.QUOTA_OR_RATE_LIMIT)
        controller = VoiceController(Backend(), self.logger)
        controller.start()
        controller._thread.join(2)
        incident, = self.history.list()
        self.assertEqual(incident.code, "voice_quota_or_rate_limit")
        self.assertEqual(incident.stage, "transcription")
        controller.close()

    def test_voice_cancelled_never_appears_as_failure(self):
        entered = threading.Event()
        class Backend:
            def recognize(self, cancellation):
                entered.set()
                cancellation.wait(2)
                return VoiceBackendResult(VoiceResultStatus.FAILED, None, None, "none", 0, 0)
        controller = VoiceController(Backend(), self.logger)
        controller.start()
        self.assertTrue(entered.wait(1))
        controller.cancel()
        controller._thread.join(2)
        self.assertEqual(self.history.list(), ())
        controller.close()
