import logging
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol, runtime_checkable

from desktop_agent import __version__
from desktop_agent.budgeted_provider import BudgetedProposalProvider
from desktop_agent.command_processor import CommandProcessor
from desktop_agent.executor import ActionExecutor
from desktop_agent.interpretation import (
    HybridInterpreter,
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


def process_command(
    command: str,
    executor: ActionExecutor,
    logger: logging.Logger,
    output: Output = print,
    interpreter: HybridInterpreter | None = None,
) -> bool:
    active_interpreter = (
        interpreter if interpreter is not None else HybridInterpreter()
    )
    processor = CommandProcessor(executor, active_interpreter, logger)
    return processor.execute(command, output).success


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

    if arguments == ["--gui"]:
        from desktop_agent.tk_app import run_gui

        return run_gui()

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
