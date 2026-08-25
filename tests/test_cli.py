import io
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_agent.cli import (
    _hold_one_shot_playback,
    _run_interactive,
    build_executor,
    build_interpreter,
    process_command,
)
from desktop_agent.executor import ActionExecutor
from desktop_agent.interpretation import (
    HybridInterpreter,
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


class FakePlaybackController:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.stop_calls = 0
        self.active = False

    @property
    def has_active_session(self) -> bool:
        return self.active

    def __call__(self, query: str) -> ToolResult:
        self.queries.append(query)
        self.active = True
        return ToolResult(True, "Reproducción activa.")

    def stop(self) -> ToolResult:
        self.stop_calls += 1
        self.active = False
        return ToolResult(True, "Reproducción detenida.")


class FakeProposalProvider:
    def __init__(
        self,
        response: object,
        usage: ProposalUsage | None = None,
        estimated_cost_usd: float | None = None,
    ) -> None:
        self.response = response
        self.usage = usage
        self.estimated_cost_usd = estimated_cost_usd
        self.commands: list[str] = []

    def propose(self, command: str) -> ProposalProviderResult:
        self.commands.append(command)
        return ProposalProviderResult(
            self.response,
            self.usage,
            self.estimated_cost_usd,
        )


class FailingProposalProvider:
    def __init__(
        self,
        provider_result: ProposalProviderResult | None = None,
    ) -> None:
        self.provider_result = provider_result
        self.commands: list[str] = []

    def propose(self, command: str) -> ProposalProviderResult:
        self.commands.append(command)
        raise ProposalProviderError(
            "simulated external failure",
            self.provider_result,
        )


class ProcessCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.log_output = io.StringIO()
        self.logger = logging.Logger(self.id(), level=logging.INFO)
        self.logger.addHandler(logging.StreamHandler(self.log_output))
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.usage_path = Path(self.temporary_directory.name) / "ai_usage.json"

    def budget_factory(self, config: ProviderConfig) -> MonthlyUsageLedger:
        return MonthlyUsageLedger(
            config.monthly_budget_usd,
            path=self.usage_path,
            month_provider=lambda: "2026-08",
        )

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

    def test_processes_application_command(self) -> None:
        output: list[str] = []
        executor = ActionExecutor(
            {
                "open_application": lambda name: ToolResult(
                    True, f"Aplicación abierta: {name}"
                )
            },
            self.logger,
        )

        success = process_command(
            "abrir calculadora", executor, self.logger, output.append
        )

        self.assertTrue(success)
        self.assertEqual(
            output,
            [
                "Entendiendo comando...",
                "Ejecutando open_application...",
                "Aplicación abierta: calculator",
            ],
        )

    def test_exact_command_does_not_invoke_configured_provider(self) -> None:
        provider = FakeProposalProvider(
            {"schema_version": 1, "intent": "UNSUPPORTED", "target": None}
        )
        interpreter = build_interpreter(
            {
                AI_ENABLED_ENV: "true",
                OPENAI_API_KEY_ENV: "test-api-key",
            },
            provider_factory=lambda config: provider,
            budget_factory=self.budget_factory,
        )
        output: list[str] = []
        executor = ActionExecutor(
            {"open_url": lambda url: ToolResult(True, f"Abierta: {url}")},
            self.logger,
        )

        success = process_command(
            "abrir youtube",
            executor,
            self.logger,
            output.append,
            interpreter,
        )

        self.assertTrue(success)
        self.assertEqual(provider.commands, [])
        self.assertEqual(output[-1], "Abierta: https://www.youtube.com/")
        log = self.log_output.getvalue()
        self.assertIn("path=deterministic", log)
        self.assertIn("status=success", log)
        self.assertIn("provider_configured=true", log)
        self.assertIn("provider=openai", log)
        self.assertIn("model=gpt-5.6-luna", log)
        self.assertIn("input_tokens=none", log)
        self.assertNotIn("abrir youtube", log)
        self.assertNotIn("test-api-key", log)

    def test_natural_command_uses_provider_and_local_executor(self) -> None:
        usage = ProposalUsage(
            input_tokens=25,
            output_tokens=5,
            total_tokens=30,
        )
        provider = FakeProposalProvider(
            {
                "schema_version": 1,
                "intent": "OPEN_APPLICATION",
                "target": "calculator",
            },
            usage=usage,
            estimated_cost_usd=0.000011,
        )
        interpreter = build_interpreter(
            {
                AI_ENABLED_ENV: "1",
                OPENAI_API_KEY_ENV: "test-api-key",
            },
            provider_factory=lambda config: provider,
            budget_factory=self.budget_factory,
        )
        opened: list[str] = []
        executor = ActionExecutor(
            {
                "open_application": lambda name: (
                    opened.append(name)
                    or ToolResult(True, f"Aplicación abierta: {name}")
                )
            },
            self.logger,
        )
        output: list[str] = []

        success = process_command(
            "quiero usar la calculadora",
            executor,
            self.logger,
            output=output.append,
            interpreter=interpreter,
        )

        self.assertTrue(success)
        self.assertEqual(provider.commands, ["quiero usar la calculadora"])
        self.assertEqual(opened, ["calculator"])
        self.assertIn(
            "Uso IA 2026-08: 30 tokens, USD 0.000011 de USD 1.00.",
            output,
        )
        log = self.log_output.getvalue()
        self.assertIn("path=external", log)
        self.assertIn("status=success", log)
        self.assertIn("input_tokens=25", log)
        self.assertIn("output_tokens=5", log)
        self.assertIn("total_tokens=30", log)
        self.assertIn("estimated_cost_usd=0.0000110000", log)
        self.assertIn("monthly=2026-08", log)
        self.assertIn("monthly_requests=1", log)
        self.assertIn("monthly_total_tokens=30", log)
        self.assertIn("monthly_estimated_cost_usd=0.0000110000", log)
        self.assertIn("monthly_budget_usd=1.00", log)
        self.assertNotIn("quiero usar la calculadora", log)
        self.assertNotIn("schema_version", log)
        self.assertNotIn("test-api-key", log)

    def test_external_failure_does_not_execute_any_tool(self) -> None:
        provider = FailingProposalProvider(
            ProposalProviderResult(
                payload=None,
                usage=ProposalUsage(10, 2, 12),
                estimated_cost_usd=0.0000044,
            )
        )
        interpreter = build_interpreter(
            {
                AI_ENABLED_ENV: "yes",
                OPENAI_API_KEY_ENV: "test-api-key",
            },
            provider_factory=lambda config: provider,
            budget_factory=self.budget_factory,
        )
        executions: list[str] = []
        executor = ActionExecutor(
            {
                "open_url": lambda url: (
                    executions.append(url) or ToolResult(True, "unexpected")
                )
            },
            self.logger,
        )
        output: list[str] = []

        success = process_command(
            "una formulación libre",
            executor,
            self.logger,
            output.append,
            interpreter,
        )

        self.assertFalse(success)
        self.assertEqual(provider.commands, ["una formulación libre"])
        self.assertEqual(executions, [])
        self.assertEqual(output[-1], "Comando no soportado todavía.")
        log = self.log_output.getvalue()
        self.assertIn("path=external", log)
        self.assertIn("status=provider_error", log)
        self.assertIn("input_tokens=10", log)
        self.assertIn("output_tokens=2", log)
        self.assertIn("total_tokens=12", log)
        self.assertIn("estimated_cost_usd=0.0000044000", log)
        self.assertNotIn("una formulación libre", log)
        self.assertNotIn("simulated external failure", log)
        self.assertNotIn("test-api-key", log)

    def test_monthly_budget_blocks_provider_before_external_call(self) -> None:
        preexisting_ledger = MonthlyUsageLedger(
            0.01,
            path=self.usage_path,
            month_provider=lambda: "2026-08",
        )
        reservation_id, _ = preexisting_ledger.reserve()
        preexisting_ledger.settle(reservation_id, None, None)
        provider = FakeProposalProvider(
            {"schema_version": 1, "intent": "UNSUPPORTED", "target": None}
        )
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
            "una formulación libre",
            ActionExecutor({}, self.logger),
            self.logger,
            output.append,
            interpreter,
        )

        self.assertFalse(success)
        self.assertEqual(provider.commands, [])
        self.assertEqual(
            output,
            [
                "Entendiendo comando...",
                "Uso IA 2026-08: 0 tokens, USD 0.010000 de USD 0.01.",
                "Límite mensual de IA alcanzado; no se realizó la llamada.",
            ],
        )
        self.assertIn("status=budget_exceeded", self.log_output.getvalue())

    def test_invalid_provider_config_falls_back_to_deterministic_mode(self) -> None:
        warnings: list[str] = []
        factory_calls: list[object] = []

        def provider_factory(config: object) -> FakeProposalProvider:
            factory_calls.append(config)
            return FakeProposalProvider({})

        interpreter = build_interpreter(
            {AI_ENABLED_ENV: "true"},
            provider_factory=provider_factory,
            budget_factory=self.budget_factory,
            warning_output=warnings.append,
        )
        executor = ActionExecutor(
            {"open_url": lambda url: ToolResult(True, f"Abierta: {url}")},
            self.logger,
        )

        success = process_command(
            "abrir youtube",
            executor,
            self.logger,
            output=lambda message: None,
            interpreter=interpreter,
        )

        self.assertTrue(success)
        self.assertEqual(factory_calls, [])
        self.assertEqual(
            warnings,
            ["Configuración de IA inválida; se usará el modo determinista."],
        )


class V04CliIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.log_output = io.StringIO()
        self.logger = logging.Logger(self.id(), level=logging.INFO)
        self.logger.addHandler(logging.StreamHandler(self.log_output))
        self.controller = FakePlaybackController()
        self.executor, returned_controller = build_executor(
            self.logger,
            self.controller,
        )
        self.assertIs(returned_controller, self.controller)

    def test_registered_tools_play_and_stop_without_exposing_query_in_logs(
        self,
    ) -> None:
        output: list[str] = []

        played = process_command(
            "poné en youtube Qué tan malo puedo ser",
            self.executor,
            self.logger,
            output.append,
        )
        stopped = process_command(
            "detener youtube",
            self.executor,
            self.logger,
            output.append,
        )

        self.assertTrue(played)
        self.assertTrue(stopped)
        self.assertEqual(self.controller.queries, ["Qué tan malo puedo ser"])
        self.assertEqual(self.controller.stop_calls, 1)
        self.assertFalse(self.controller.has_active_session)
        self.assertNotIn("Qué tan malo puedo ser", self.log_output.getvalue())

    def test_deterministic_playback_bypasses_configured_provider(self) -> None:
        provider = FakeProposalProvider(
            {"schema_version": 1, "intent": "UNSUPPORTED", "target": None}
        )
        interpreter = HybridInterpreter(provider)

        success = process_command(
            "pone lofi hip hop en youtube",
            self.executor,
            self.logger,
            output=lambda _: None,
            interpreter=interpreter,
        )

        self.assertTrue(success)
        self.assertEqual(provider.commands, [])
        self.assertEqual(self.controller.queries, ["lofi hip hop"])

    def test_one_shot_waits_for_enter_and_stops_active_playback(self) -> None:
        self.controller("lofi")
        output: list[str] = []

        with patch("builtins.input", return_value=""):
            stopped = _hold_one_shot_playback(
                self.controller,
                output.append,
            )

        self.assertTrue(stopped)
        self.assertEqual(self.controller.stop_calls, 1)
        self.assertEqual(
            output,
            [
                "Reproducción activa; presioná Enter para detenerla.",
                "Reproducción detenida.",
            ],
        )

    def test_interactive_exit_stops_session_and_reports_current_version(self) -> None:
        self.controller("lofi")
        output: list[str] = []

        with patch("builtins.input", return_value="salir"):
            exit_code = _run_interactive(
                self.executor,
                self.logger,
                HybridInterpreter(),
                self.controller,
                output.append,
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(self.controller.stop_calls, 1)
        self.assertEqual(
            output,
            [
                "Desktop Agent v0.9.0 — escribí 'salir' para terminar.",
                "Reproducción detenida.",
                "Hasta luego.",
            ],
        )


class GuiDispatchTests(unittest.TestCase):
    def test_gui_flag_dispatches_without_building_cli_runtime(self) -> None:
        with patch("desktop_agent.tk_app.run_gui", return_value=7) as run_gui:
            from desktop_agent.cli import main

            result = main(["--gui"])

        self.assertEqual(result, 7)
        run_gui.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
