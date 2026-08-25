import logging
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol, runtime_checkable

from desktop_agent import __version__
from desktop_agent.budgeted_provider import BudgetedProposalProvider
from desktop_agent.executor import ActionExecutionError, ActionExecutor
from desktop_agent.interpretation import (
    HybridInterpreter,
    InterpretationResult,
    InterpretationStatus,
    ProposalProvider,
    ProposalProviderError,
)
from desktop_agent.logging_config import configure_logging
from desktop_agent.models import ToolResult
from desktop_agent.openai_provider import OpenAIProposalProvider
from desktop_agent.playwright_backend import create_youtube_playwright_adapter
from desktop_agent.provider_config import (
    ProviderConfig,
    ProviderConfigurationError,
    load_provider_config,
)
from desktop_agent.tools.applications import open_application
from desktop_agent.tools.browser import open_url
from desktop_agent.tools.browser_automation import YouTubePlaybackTool
from desktop_agent.usage_budget import MonthlyUsageLedger

Output = Callable[[str], None]
ProviderFactory = Callable[[ProviderConfig], ProposalProvider]
BudgetFactory = Callable[[ProviderConfig], MonthlyUsageLedger]


@runtime_checkable
class PlaybackController(Protocol):
    @property
    def has_active_session(self) -> bool: ...

    def __call__(self, query: str) -> ToolResult: ...

    def stop(self) -> ToolResult: ...


def _default_playback_controller(logger: logging.Logger) -> PlaybackController:
    return YouTubePlaybackTool(
        lambda: create_youtube_playwright_adapter(logger),
        logger,
    )


def build_executor(
    logger: logging.Logger,
    playback_controller: PlaybackController | None = None,
) -> tuple[ActionExecutor, PlaybackController]:
    """Registra herramientas sin iniciar Chromium hasta recibir una orden web."""

    if not isinstance(logger, logging.Logger):
        raise TypeError("El logger del ejecutor no es válido.")
    controller = (
        playback_controller
        if playback_controller is not None
        else _default_playback_controller(logger)
    )
    if not isinstance(controller, PlaybackController):
        raise TypeError("El controlador de reproducción no es válido.")
    executor = ActionExecutor(
        tools={
            "open_url": open_url,
            "open_application": open_application,
            "play_youtube": controller,
            "stop_youtube": controller.stop,
        },
        logger=logger,
    )
    return executor, controller


def _default_budget_factory(config: ProviderConfig) -> MonthlyUsageLedger:
    return MonthlyUsageLedger(config.monthly_budget_usd)


def build_interpreter(
    environ: Mapping[str, str] | None = None,
    provider_factory: ProviderFactory = OpenAIProposalProvider,
    budget_factory: BudgetFactory = _default_budget_factory,
    warning_output: Output | None = None,
) -> HybridInterpreter:
    """Construye el intérprete sin impedir el modo local ante fallos de setup."""

    provider_name: str | None = None
    provider_model: str | None = None
    try:
        config = load_provider_config(environ)
        provider = provider_factory(config) if config.enabled else None
        if provider is not None:
            provider = BudgetedProposalProvider(
                provider,
                budget_factory(config),
            )
            provider_name = config.provider_name
            provider_model = config.model
    except ProviderConfigurationError:
        if warning_output is not None:
            warning_output(
                "Configuración de IA inválida; se usará el modo determinista."
            )
        provider = None
    except ProposalProviderError:
        if warning_output is not None:
            warning_output(
                "Proveedor de IA no disponible; se usará el modo determinista."
            )
        provider = None

    return HybridInterpreter(
        provider,
        provider_name=provider_name,
        provider_model=provider_model,
    )


def _optional_log_value(value: object | None) -> object:
    return "none" if value is None else value


def _log_interpretation(
    logger: logging.Logger,
    result: InterpretationResult,
) -> None:
    usage = result.usage
    estimated_cost = (
        "none"
        if result.estimated_cost_usd is None
        else f"{result.estimated_cost_usd:.10f}"
    )
    monthly = result.monthly_usage
    logger.info(
        "Interpretation: path=%s status=%s duration_ms=%.3f "
        "provider_configured=%s provider=%s model=%s "
        "input_tokens=%s output_tokens=%s total_tokens=%s "
        "estimated_cost_usd=%s monthly=%s monthly_requests=%s "
        "monthly_input_tokens=%s monthly_output_tokens=%s "
        "monthly_total_tokens=%s monthly_estimated_cost_usd=%s "
        "monthly_budget_usd=%s monthly_remaining_usd=%s "
        "monthly_unmetered_requests=%s monthly_pending_reservations=%s",
        result.path.value,
        result.status.value,
        result.duration_ms,
        str(result.provider_configured).lower(),
        _optional_log_value(result.provider_name),
        _optional_log_value(result.provider_model),
        _optional_log_value(usage.input_tokens if usage else None),
        _optional_log_value(usage.output_tokens if usage else None),
        _optional_log_value(usage.total_tokens if usage else None),
        estimated_cost,
        _optional_log_value(monthly.month if monthly else None),
        _optional_log_value(monthly.request_count if monthly else None),
        _optional_log_value(monthly.input_tokens if monthly else None),
        _optional_log_value(monthly.output_tokens if monthly else None),
        _optional_log_value(monthly.total_tokens if monthly else None),
        (
            "none"
            if monthly is None
            else f"{monthly.estimated_cost_usd:.10f}"
        ),
        "none" if monthly is None else f"{monthly.budget_usd:.2f}",
        "none" if monthly is None else f"{monthly.remaining_usd:.10f}",
        _optional_log_value(
            monthly.unmetered_request_count if monthly else None
        ),
        _optional_log_value(
            monthly.pending_reservation_count if monthly else None
        ),
    )


def _format_monthly_usage(result: InterpretationResult) -> str | None:
    monthly = result.monthly_usage
    if monthly is None:
        return None

    budget_text = f"{monthly.budget_usd:.6f}".rstrip("0").rstrip(".")
    if "." not in budget_text:
        budget_text += ".00"
    elif len(budget_text.rsplit(".", 1)[1]) == 1:
        budget_text += "0"
    return (
        f"Uso IA {monthly.month}: {monthly.total_tokens} tokens, "
        f"USD {monthly.estimated_cost_usd:.6f} de "
        f"USD {budget_text}."
    )


def process_command(
    command: str,
    executor: ActionExecutor,
    logger: logging.Logger,
    output: Output = print,
    interpreter: HybridInterpreter | None = None,
) -> bool:
    logger.info("Command received")
    output("Entendiendo comando...")

    active_interpreter = (
        interpreter if interpreter is not None else HybridInterpreter()
    )
    interpretation = active_interpreter.interpret_detailed(command)
    _log_interpretation(logger, interpretation)
    monthly_usage_message = _format_monthly_usage(interpretation)
    if monthly_usage_message is not None:
        output(monthly_usage_message)

    if interpretation.status is InterpretationStatus.BUDGET_EXCEEDED:
        logger.info("Status: AI_BUDGET_EXCEEDED")
        output("Límite mensual de IA alcanzado; no se realizó la llamada.")
        return False
    if interpretation.status is InterpretationStatus.USAGE_TRACKING_ERROR:
        logger.info("Status: AI_USAGE_TRACKING_ERROR")
        output("No se pudo verificar el consumo de IA; no se ejecutó ninguna acción.")
        return False

    action = interpretation.action
    if action is None:
        logger.info("Status: UNSUPPORTED_COMMAND")
        output("Comando no soportado todavía.")
        return False

    logger.info("Intent: %s", action.intent.value)
    if "url" in action.arguments:
        logger.info("URL: %s", action.arguments["url"])
    if "name" in action.arguments:
        logger.info("Application: %s", action.arguments["name"])

    output(f"Ejecutando {action.tool_name}...")
    try:
        result = executor.execute(action)
    except ActionExecutionError as error:
        output(f"Error: {error}")
        return False

    output(result.message)
    return True


def _run_interactive(
    executor: ActionExecutor,
    logger: logging.Logger,
    interpreter: HybridInterpreter,
    playback_controller: PlaybackController,
    output: Output = print,
) -> int:
    output(f"Desktop Agent v{__version__} — escribí 'salir' para terminar.")
    while True:
        try:
            command = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            output("")
            return _finish_interactive(playback_controller, output)

        if command.casefold() in {"salir", "exit"}:
            return _finish_interactive(playback_controller, output)
        if not command:
            continue

        process_command(
            command,
            executor,
            logger,
            output,
            interpreter=interpreter,
        )


def _stop_active_playback(
    playback_controller: PlaybackController,
    output: Output,
) -> bool:
    if not playback_controller.has_active_session:
        return True
    result = playback_controller.stop()
    output(result.message)
    return result.success


def _finish_interactive(
    playback_controller: PlaybackController,
    output: Output,
) -> int:
    stopped = _stop_active_playback(playback_controller, output)
    output("Hasta luego.")
    return 0 if stopped else 1


def _hold_one_shot_playback(
    playback_controller: PlaybackController,
    output: Output = print,
) -> bool:
    if not playback_controller.has_active_session:
        return True
    output("Reproducción activa; presioná Enter para detenerla.")
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        output("")
    return _stop_active_playback(playback_controller, output)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)

    try:
        logger = configure_logging()
    except OSError as error:
        print(f"No se pudo crear el archivo de log: {error}", file=sys.stderr)
        return 1

    executor, playback_controller = build_executor(logger)
    interpreter = build_interpreter(
        warning_output=lambda message: print(message, file=sys.stderr)
    )

    if arguments:
        command = " ".join(arguments)
        success = process_command(
            command,
            executor,
            logger,
            interpreter=interpreter,
        )
        if success:
            success = _hold_one_shot_playback(playback_controller) and success
        return 0 if success else 1

    return _run_interactive(
        executor,
        logger,
        interpreter,
        playback_controller,
    )
