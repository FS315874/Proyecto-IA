import io
import logging
import tempfile
import unittest
from pathlib import Path

from desktop_agent.cli import build_interpreter, process_command
from desktop_agent.executor import ActionExecutor
from desktop_agent.interpretation import (
    ProposalProviderError,
    ProposalProviderResult,
    ProposalUsage,
)
from desktop_agent.models import ToolResult
from desktop_agent.provider_config import (
    AI_ENABLED_ENV,
    AI_MONTHLY_BUDGET_ENV,
    OPENAI_API_KEY_ENV,
    ProviderConfig,
)
from desktop_agent.usage_budget import MonthlyUsageLedger


class MappingProposalProvider:
    def __init__(self, proposals: dict[str, dict[str, object]]) -> None:
        self._proposals = proposals
        self.commands: list[str] = []

    def propose(self, command: str) -> ProposalProviderResult:
        self.commands.append(command)
        return ProposalProviderResult(
            payload=self._proposals[command],
            usage=ProposalUsage(20, 4, 24),
            estimated_cost_usd=0.0000088,
        )


class FailingProposalProvider:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def propose(self, command: str) -> ProposalProviderResult:
        self.commands.append(command)
        raise ProposalProviderError(
            "simulated provider failure",
            ProposalProviderResult(
                payload=None,
                usage=ProposalUsage(10, 2, 12),
                estimated_cost_usd=0.0000044,
            ),
        )


class V03SimulatedAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.log_output = io.StringIO()
        self.logger = logging.Logger(self.id(), level=logging.INFO)
        self.logger.addHandler(logging.StreamHandler(self.log_output))
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.usage_path = Path(self.temporary_directory.name) / "ai_usage.json"
        self.executions: list[tuple[str, str]] = []

    def budget_factory(self, config: ProviderConfig) -> MonthlyUsageLedger:
        return MonthlyUsageLedger(
            config.monthly_budget_usd,
            path=self.usage_path,
            month_provider=lambda: "2026-08",
        )

    def executor(self) -> ActionExecutor:
        def open_url(url: str) -> ToolResult:
            self.executions.append(("open_url", url))
            return ToolResult(True, f"URL simulada: {url}")

        def open_application(name: str) -> ToolResult:
            self.executions.append(("open_application", name))
            return ToolResult(True, f"Aplicación simulada: {name}")

        return ActionExecutor(
            {
                "open_url": open_url,
                "open_application": open_application,
            },
            self.logger,
        )

    def enabled_interpreter(self, provider: object):
        return build_interpreter(
            {
                AI_ENABLED_ENV: "true",
                OPENAI_API_KEY_ENV: "test-api-key",
            },
            provider_factory=lambda config: provider,
            budget_factory=self.budget_factory,
        )

    def test_exact_v02_command_bypasses_provider_and_usage_ledger(self) -> None:
        provider = MappingProposalProvider({})
        output: list[str] = []

        success = process_command(
            "abrir youtube",
            self.executor(),
            self.logger,
            output.append,
            self.enabled_interpreter(provider),
        )

        self.assertTrue(success)
        self.assertEqual(provider.commands, [])
        self.assertEqual(
            self.executions,
            [("open_url", "https://www.youtube.com/")],
        )
        self.assertFalse(self.usage_path.exists())

    def test_three_agreed_natural_phrasings_execute_only_catalog_actions(self) -> None:
        proposals = {
            "poneme YouTube": {
                "schema_version": 1,
                "intent": "OPEN_URL",
                "target": "youtube",
            },
            "quiero hacer una cuenta en la calculadora": {
                "schema_version": 1,
                "intent": "OPEN_APPLICATION",
                "target": "calculator",
            },
            "abrime el editor Visual Studio Code": {
                "schema_version": 1,
                "intent": "OPEN_APPLICATION",
                "target": "vscode",
            },
        }
        provider = MappingProposalProvider(proposals)
        interpreter = self.enabled_interpreter(provider)
        executor = self.executor()
        output: list[str] = []

        for command in proposals:
            with self.subTest(command=command):
                self.assertTrue(
                    process_command(
                        command,
                        executor,
                        self.logger,
                        output.append,
                        interpreter,
                    )
                )

        self.assertEqual(provider.commands, list(proposals))
        self.assertEqual(
            self.executions,
            [
                ("open_url", "https://www.youtube.com/"),
                ("open_application", "calculator"),
                ("open_application", "vscode"),
            ],
        )
        snapshot = self.budget_factory(
            ProviderConfig(enabled=False)
        ).current_snapshot()
        self.assertEqual(snapshot.request_count, 3)
        self.assertEqual(snapshot.total_tokens, 72)
        self.assertAlmostEqual(snapshot.estimated_cost_usd, 0.0000264)
        usage_messages = [line for line in output if line.startswith("Uso IA ")]
        self.assertEqual(len(usage_messages), 3)
        self.assertIn("72 tokens", usage_messages[-1])
        self.assertIn("de USD 1.00", usage_messages[-1])

    def test_out_of_catalog_target_is_rejected_without_execution(self) -> None:
        provider = MappingProposalProvider(
            {
                "abrime Spotify": {
                    "schema_version": 1,
                    "intent": "OPEN_APPLICATION",
                    "target": "spotify",
                }
            }
        )
        output: list[str] = []

        success = process_command(
            "abrime Spotify",
            self.executor(),
            self.logger,
            output.append,
            self.enabled_interpreter(provider),
        )

        self.assertFalse(success)
        self.assertEqual(provider.commands, ["abrime Spotify"])
        self.assertEqual(self.executions, [])
        self.assertEqual(output[-1], "Comando no soportado todavía.")
        self.assertIn("status=invalid_proposal", self.log_output.getvalue())

    def test_disabled_provider_rejects_natural_command_without_side_effects(self) -> None:
        factory_calls: list[ProviderConfig] = []

        def provider_factory(config: ProviderConfig) -> MappingProposalProvider:
            factory_calls.append(config)
            return MappingProposalProvider({})

        interpreter = build_interpreter(
            {},
            provider_factory=provider_factory,
            budget_factory=self.budget_factory,
        )
        output: list[str] = []

        success = process_command(
            "poneme YouTube",
            self.executor(),
            self.logger,
            output.append,
            interpreter,
        )

        self.assertFalse(success)
        self.assertEqual(factory_calls, [])
        self.assertEqual(self.executions, [])
        self.assertFalse(self.usage_path.exists())
        self.assertEqual(output[-1], "Comando no soportado todavía.")

    def test_simulated_provider_failure_is_metered_and_has_no_side_effects(self) -> None:
        provider = FailingProposalProvider()
        output: list[str] = []

        success = process_command(
            "una formulación libre",
            self.executor(),
            self.logger,
            output.append,
            self.enabled_interpreter(provider),
        )

        self.assertFalse(success)
        self.assertEqual(provider.commands, ["una formulación libre"])
        self.assertEqual(self.executions, [])
        self.assertIn(
            "Uso IA 2026-08: 12 tokens, USD 0.000004 de USD 1.00.",
            output,
        )
        self.assertEqual(output[-1], "Comando no soportado todavía.")
        self.assertIn("status=provider_error", self.log_output.getvalue())

    def test_monthly_budget_blocks_before_simulated_provider_call(self) -> None:
        ledger = MonthlyUsageLedger(
            0.01,
            path=self.usage_path,
            month_provider=lambda: "2026-08",
        )
        reservation_id, _ = ledger.reserve()
        ledger.settle(reservation_id, None, None)
        provider = MappingProposalProvider({})
        interpreter = build_interpreter(
            {
                AI_ENABLED_ENV: "true",
                AI_MONTHLY_BUDGET_ENV: "0.01",
                OPENAI_API_KEY_ENV: "test-api-key",
            },
            provider_factory=lambda config: provider,
            budget_factory=self.budget_factory,
        )
        output: list[str] = []

        success = process_command(
            "poneme YouTube",
            self.executor(),
            self.logger,
            output.append,
            interpreter,
        )

        self.assertFalse(success)
        self.assertEqual(provider.commands, [])
        self.assertEqual(self.executions, [])
        self.assertEqual(
            output,
            [
                "Entendiendo comando...",
                "Uso IA 2026-08: 0 tokens, USD 0.010000 de USD 0.01.",
                "Límite mensual de IA alcanzado; no se realizó la llamada.",
            ],
        )


if __name__ == "__main__":
    unittest.main()
