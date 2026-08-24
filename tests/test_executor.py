import logging
import unittest

from desktop_agent.executor import ActionExecutionError, ActionExecutor
from desktop_agent.models import Action, Intent, RiskLevel, ToolResult


class ActionExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger("desktop_agent.tests")
        self.logger.addHandler(logging.NullHandler())

    def test_executes_registered_tool(self) -> None:
        received_urls: list[str] = []

        def fake_tool(url: str) -> ToolResult:
            received_urls.append(url)
            return ToolResult(success=True, message="ok")

        executor = ActionExecutor({"open_url": fake_tool}, self.logger)
        action = self._action()

        result = executor.execute(action)

        self.assertTrue(result.success)
        self.assertEqual(received_urls, ["https://www.google.com/"])

    def test_rejects_unregistered_tool(self) -> None:
        executor = ActionExecutor({}, self.logger)

        with self.assertRaisesRegex(ActionExecutionError, "no está registrada"):
            executor.execute(self._action())

    def test_rejects_action_that_requires_confirmation(self) -> None:
        executor = ActionExecutor({"open_url": lambda **_: None}, self.logger)
        action = Action(
            intent=Intent.OPEN_URL,
            tool_name="open_url",
            arguments={"url": "https://www.google.com/"},
            risk_level=RiskLevel.CAUTION,
            requires_confirmation=True,
        )

        with self.assertRaisesRegex(ActionExecutionError, "no es segura"):
            executor.execute(action)

    def test_rejects_non_safe_action_even_with_inconsistent_metadata(self) -> None:
        executor = ActionExecutor({"open_url": lambda **_: None}, self.logger)
        action = Action(
            intent=Intent.OPEN_URL,
            tool_name="open_url",
            arguments={"url": "https://www.google.com/"},
            risk_level=RiskLevel.DANGEROUS,
            requires_confirmation=False,
        )

        with self.assertRaisesRegex(ActionExecutionError, "no es segura"):
            executor.execute(action)

    @staticmethod
    def _action() -> Action:
        return Action(
            intent=Intent.OPEN_URL,
            tool_name="open_url",
            arguments={"url": "https://www.google.com/"},
            risk_level=RiskLevel.SAFE,
            requires_confirmation=False,
        )


if __name__ == "__main__":
    unittest.main()
