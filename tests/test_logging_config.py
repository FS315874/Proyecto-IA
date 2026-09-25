import logging
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest.mock import patch

from desktop_agent.diagnostics import emit_failure
from desktop_agent.error_history import ErrorHistory, history_handler
from desktop_agent.logging_config import configure_logging


class LoggingConfigurationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)
        self.logger = logging.Logger(self.id())
        # Cerrar handlers antes de limpiar el directorio en Windows.
        self.addCleanup(self.close_handlers)

    def close_handlers(self):
        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)
            handler.close()

    def test_default_location_and_repeated_configuration_keep_one_history(self):
        with patch("desktop_agent.logging_config.logging.getLogger", return_value=self.logger), \
             patch.dict("os.environ", {"LOCALAPPDATA": str(self.path)}):
            configured = configure_logging()
            self.assertIs(configure_logging(), configured)
        self.assertEqual(len(configured.handlers), 2)
        handler = history_handler(configured)
        self.assertEqual(handler.history.path, self.path / "DesktopAgent" / "error_history.sqlite3")
        emit_failure(configured, "tool_failed", "executor")
        self.assertEqual(len(handler.history.list()), 1)

    def test_rotation_is_bounded_and_does_not_rotate_the_incident_database(self):
        target = self.path / "custom" / "agent.log"
        with patch("desktop_agent.logging_config.logging.getLogger", return_value=self.logger):
            configure_logging(target)
        rotating, = [item for item in self.logger.handlers if isinstance(item, RotatingFileHandler)]
        self.assertEqual(rotating.maxBytes, 2_000_000)
        self.assertEqual(rotating.backupCount, 2)
        rotating.maxBytes = 250  # Fuerza rotación con datos ficticios pequeños.
        for _ in range(10):
            emit_failure(self.logger, "tool_failed", "executor")
        self.close_handlers()
        self.assertEqual(len(list(target.parent.glob("agent.log*"))), 3)
        self.assertEqual(len(ErrorHistory(target.with_name("error_history.sqlite3")).list()), 10)


if __name__ == "__main__":
    unittest.main()
