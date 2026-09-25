import contextlib
import io
import json
import logging
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from desktop_agent.diagnostics import DEFINITIONS, emit_failure, safe_duration
from desktop_agent.error_history import ErrorHistory, ErrorHistoryHandler, HistoryUnavailable


class ErrorHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "errors.sqlite3"
        self.history = ErrorHistory(self.path)
        self.handler = ErrorHistoryHandler(self.history)
        self.logger = logging.Logger(self.id())
        self.logger.addHandler(self.handler)

    def emit(self, code="tool_failed", **kwargs):
        emit_failure(self.logger, code, "executor", **kwargs)

    def test_read_missing_history_does_not_create_any_file(self):
        self.assertEqual(self.history.list(), ())
        self.assertFalse(self.path.exists())
        self.assertFalse(self.history.mark_reviewed(1))
        self.assertFalse(self.path.exists())

    def test_survives_restart_and_review_does_not_delete(self):
        self.emit("app_missing", tool="open_application", duration_ms=12.25)
        restarted = ErrorHistory(self.path)
        incident, = restarted.list()
        self.assertEqual(incident.code, "app_missing")
        self.assertEqual(incident.duration_ms, 12.25)
        self.assertTrue(incident.occurred_at.endswith("+00:00"))
        self.assertEqual(incident.report()["category"], "entorno")
        self.assertTrue(restarted.mark_reviewed(incident.id))
        self.assertEqual(restarted.list(), ())
        self.assertTrue(restarted.list(include_reviewed=True)[0].reviewed)
        self.emit("app_missing")
        self.assertEqual(len(restarted.list()), 1)
        self.assertEqual(len(restarted.list(include_reviewed=True)), 2)

    def test_only_explicit_diagnostics_are_stored_no_raw_message_or_traceback(self):
        private = "synthetic-private-command-and-secret"
        self.logger.error(private)
        self.assertEqual(self.history.list(), ())
        try:
            raise RuntimeError(private)
        except RuntimeError:
            self.logger.error(private, exc_info=True, extra={"diagnostic": {
                "code": "tool_failed", "source": "executor", "stage": "executing",
                "tool": "open_application", "duration_ms": None}})
        self.assertNotIn(private.encode(), self.path.read_bytes())
        self.assertEqual(len(self.history.list()), 1)

    def test_untrusted_extra_fields_are_rejected_without_logging_values(self):
        self.logger.warning("ignored", extra={"diagnostic": {"code": "tool_failed", "transcript": "private"}})
        self.assertTrue(self.handler.write_failed)
        self.assertFalse(self.path.exists())

    def test_unknown_code_tool_and_stage_are_not_persisted(self):
        self.emit("secret-value", stage="secret-value", tool="secret-value", duration_ms=float("nan"))
        incident, = self.history.list()
        self.assertEqual(incident.code, "tool_failed")
        self.assertIsNone(incident.stage)
        self.assertIsNone(incident.tool)
        self.assertIsNone(incident.duration_ms)
        self.assertNotIn(b"secret-value", self.path.read_bytes())

    def test_retention_keeps_newest_including_unreviewed(self):
        self.handler.history = ErrorHistory(self.path, limit=3)
        for _ in range(5):
            self.emit()
        self.assertEqual([item.id for item in self.history.list()], [5, 4, 3])

    def test_concurrent_writers_do_not_lose_records(self):
        fields = {"code": "tool_failed", "source": "executor", "stage": None, "tool": None, "duration_ms": None}
        def write(_):
            ErrorHistory(self.path).record(fields, "a" * 32)
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(write, range(12)))
        self.assertEqual(len(self.history.list()), 12)

    def test_corrupt_file_is_preserved_and_does_not_hide_tool_failure(self):
        self.path.write_bytes(b"corrupt synthetic history")
        with contextlib.redirect_stderr(io.StringIO()) as output:
            self.emit()
        self.assertTrue(self.handler.write_failed)
        self.assertIn("sin guardar", output.getvalue())
        self.assertEqual(self.path.read_bytes(), b"corrupt synthetic history")
        with self.assertRaises(HistoryUnavailable):
            self.history.list()

    def test_write_failure_recovery_and_pythonw_without_stderr(self):
        with patch.object(self.history, "record", side_effect=HistoryUnavailable()), patch("sys.stderr", None):
            self.emit()
        self.assertTrue(self.handler.write_failed)
        self.assertEqual(self.handler.dropped_count, 1)
        self.emit()
        self.assertFalse(self.handler.write_failed)
        self.assertEqual(self.handler.dropped_count, 1)
        self.assertEqual(len(self.history.list()), 1)

    def test_closed_stderr_never_masks_the_original_error(self):
        closed_stream = io.StringIO()
        closed_stream.close()
        with patch.object(self.history, "record", side_effect=HistoryUnavailable()), patch("sys.stderr", closed_stream):
            self.emit()
        self.assertEqual(self.handler.dropped_count, 1)

    def test_durations_reject_nonfinite_values_and_huge_integers(self):
        for value in (-1, float("nan"), float("inf"), 10**400, True, "12"):
            with self.subTest(value_type=type(value)):
                self.assertIsNone(safe_duration(value))
                self.emit(duration_ms=value)
                self.assertIsNone(self.history.list()[0].duration_ms)
        self.assertEqual(safe_duration(12.123456), 12.123)

    def test_invalid_read_filter_does_not_create_a_file(self):
        with self.assertRaises(ValueError):
            self.history.list(include_reviewed="private")
        self.assertFalse(self.path.exists())

    def test_cli_all_includes_reviewed_and_unavailable_has_safe_error(self):
        from desktop_agent.cli import main
        self.emit()
        self.history.mark_reviewed(self.history.list()[0].id)
        with patch("desktop_agent.error_history.ErrorHistory", return_value=self.history), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--errors", "--all"]), 0)
        self.assertTrue(json.loads(output.getvalue())["incidents"][0]["reviewed"])
        self.path.write_bytes(b"private corrupt file")
        with patch("desktop_agent.error_history.ErrorHistory", return_value=self.history), contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(main(["--errors"]), 1)
        self.assertNotIn("private", error.getvalue())

    def test_tampered_private_row_is_not_returned_or_exported(self):
        self.emit()
        with contextlib.closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE incidents SET code=?", ("private-injected-value",))
        with self.assertRaises(HistoryUnavailable) as caught:
            self.history.list()
        self.assertNotIn("private", str(caught.exception))

    def test_future_schema_is_not_overwritten(self):
        self.emit()
        with contextlib.closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("PRAGMA user_version=2")
        with self.assertRaises(HistoryUnavailable):
            self.history.list()
        self.emit()
        with contextlib.closing(sqlite3.connect(self.path)) as connection, connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0], 1)

    def test_cli_is_read_only_without_runtime_or_api_setup(self):
        from desktop_agent.cli import main
        self.emit("voice_authentication")
        with patch("desktop_agent.error_history.ErrorHistory", return_value=self.history), \
             patch("desktop_agent.cli.BrowserRuntime") as runtime, \
             patch("desktop_agent.cli.configure_logging") as logging_setup, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--errors"]), 0)
        runtime.assert_not_called()
        logging_setup.assert_not_called()
        report = json.loads(output.getvalue())
        self.assertEqual(report["classification"], "preliminary")
        self.assertEqual(report["incidents"][0]["code"], "voice_authentication")

    def test_cli_bad_options_never_become_commands(self):
        from desktop_agent.cli import main
        with patch("desktop_agent.cli.BrowserRuntime") as runtime, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--errors", "abrir chrome"]), 2)
        runtime.assert_not_called()

    def test_catalog_is_static_and_has_actionable_guidance(self):
        for definition in DEFINITIONS.values():
            self.assertTrue(definition.category)
            self.assertTrue(definition.summary)
            self.assertTrue(definition.next_check)


if __name__ == "__main__":
    unittest.main()
