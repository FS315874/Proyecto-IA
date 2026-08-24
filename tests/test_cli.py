import logging
import unittest

from desktop_agent.cli import process_command
from desktop_agent.executor import ActionExecutor
from desktop_agent.models import ToolResult


class ProcessCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger("desktop_agent.cli.tests")
        self.logger.addHandler(logging.NullHandler())

    def test_processes_supported_command(self) -> None:
        output: list[str] = []
        executor = ActionExecutor(
            {"open_url": lambda url: ToolResult(True, f"Abierta: {url}")},
            self.logger,
        )

        success = process_command("abrir youtube", executor, self.logger, output.append)

        self.assertTrue(success)
        self.assertEqual(
            output,
            [
                "Entendiendo comando...",
                "Ejecutando open_url...",
                "Abierta: https://www.youtube.com/",
            ],
        )

    def test_reports_unsupported_command_without_executing_tool(self) -> None:
        output: list[str] = []
        executor = ActionExecutor({}, self.logger)

        success = process_command(
            "preparame un café", executor, self.logger, output.append
        )

        self.assertFalse(success)
        self.assertEqual(
            output,
            ["Entendiendo comando...", "Comando no soportado todavía."],
        )


if __name__ == "__main__":
    unittest.main()

