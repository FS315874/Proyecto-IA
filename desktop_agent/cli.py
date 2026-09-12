import logging
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol, runtime_checkable

from desktop_agent import __version__
from desktop_agent.budgeted_provider import BudgetedProposalProvider
from desktop_agent.browser_bridge import BrowserBridgeClient
from desktop_agent.browser_preferences import (
    BrowserPreferenceSource,
    StaticBrowserPreferenceSource,
)
from desktop_agent.browser_runtime import (
    BrowserRuntime,
    PreferredYouTubeAdapterFactory,
)
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
from desktop_agent.openai_plan_provider import OpenAIPlanProvider
from desktop_agent.plans import (
    CancellationToken,
    PlanInterpretationStatus,
    PlanStatus,
    PlanStepState,
    PlanValidationError,
    TaskPlanExecutor,
    TaskPlanner,
)
from desktop_agent.playwright_backend import create_youtube_playwright_adapter
from desktop_agent.preferred_browser import PreferredBrowserOpener
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
PlanProviderFactory = Callable[[ProviderConfig], ProposalProvider]


@runtime_checkable
class PlaybackController(Protocol):
    @property
    def has_active_session(self) -> bool: ...

    def __call__(self, query: str) -> ToolResult: ...

    def stop(self) -> ToolResult: ...


def _default_playback_controller(
    logger: logging.Logger,
    browser_preferences: BrowserPreferenceSource,
    browser_bridge: BrowserBridgeClient | None,
) -> PlaybackController:
    return YouTubePlaybackTool(
        PreferredYouTubeAdapterFactory(
            browser_preferences,
            browser_bridge,
            logger,
            isolated_factory=lambda: create_youtube_playwright_adapter(logger),
            session_connector=PreferredBrowserOpener(
                browser_preferences, bridge=browser_bridge,
            ).ensure_current_session,
        ),
        logger,
        session_key=browser_preferences.load,
    )


def build_executor(
    logger: logging.Logger,
    playback_controller: PlaybackController | None = None,
    *,
    browser_preferences: BrowserPreferenceSource | None = None,
    browser_bridge: BrowserBridgeClient | None = None,
) -> tuple[ActionExecutor, PlaybackController]:
    """Registra herramientas sin iniciar Chromium hasta recibir una orden web."""

    if not isinstance(logger, logging.Logger):
        raise TypeError("El logger del ejecutor no es válido.")
    preferences = browser_preferences or StaticBrowserPreferenceSource()
    if not isinstance(preferences, BrowserPreferenceSource):
        raise TypeError("Las preferencias del navegador no son válidas.")
    if browser_bridge is not None and not isinstance(
        browser_bridge,
        BrowserBridgeClient,
    ):
        raise TypeError("El puente del navegador no cumple el contrato.")
    controller = (
        playback_controller
        if playback_controller is not None
        else _default_playback_controller(logger, preferences, browser_bridge)
    )
    if not isinstance(controller, PlaybackController):
        raise TypeError("El controlador de reproducción no es válido.")
    browser_opener = PreferredBrowserOpener(
        preferences,
        bridge=browser_bridge,
    )
    executor = ActionExecutor(
        tools={
            "open_url": lambda url: open_url(url, opener=browser_opener),
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


def build_task_planner(
    environ: Mapping[str, str] | None = None,
    provider_factory: PlanProviderFactory = OpenAIPlanProvider,
    budget_factory: BudgetFactory = _default_budget_factory,
    warning_output: Output | None = None,
) -> TaskPlanner:
    """Construye el planificador opt-in con el mismo presupuesto local."""

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
                "Configuración de IA inválida; se usarán planes deterministas."
            )
        provider = None
    except ProposalProviderError:
        if warning_output is not None:
            warning_output(
                "Proveedor de IA no disponible; se usarán planes deterministas."
            )
        provider = None
    return TaskPlanner(
        provider,
        provider_name=provider_name,
        provider_model=provider_model,
    )


def process_plan_command(
    command: str,
    plan_executor: TaskPlanExecutor,
    planner: TaskPlanner,
    logger: logging.Logger,
    output: Output = print,
    cancellation: CancellationToken | None = None,
) -> bool:
    logger.info("Plan command received")
    output("Planificando tarea...")
    interpretation = planner.interpret(command)
    logger.info(
        "Plan interpretation: path=%s status=%s duration_ms=%.3f "
        "provider_configured=%s provider=%s model=%s",
        interpretation.path.value,
        interpretation.status.value,
        interpretation.duration_ms,
        str(interpretation.provider_configured).lower(),
        interpretation.provider_name or "none",
        interpretation.provider_model or "none",
    )
    monthly = interpretation.monthly_usage
    if monthly is not None:
        output(
            f"Uso IA {monthly.month}: {monthly.total_tokens} tokens, "
            f"USD {monthly.estimated_cost_usd:.6f} de "
            f"USD {monthly.budget_usd:.2f}."
        )

    if interpretation.status is PlanInterpretationStatus.BUDGET_EXCEEDED:
        output("Límite mensual de IA alcanzado; no se realizó la llamada.")
        return False
    if interpretation.status is PlanInterpretationStatus.USAGE_TRACKING_ERROR:
        output("No se pudo verificar el consumo; no se ejecutó el plan.")
        return False
    if interpretation.status in {
        PlanInterpretationStatus.PROVIDER_ERROR,
        PlanInterpretationStatus.INVALID_PROPOSAL,
    }:
        output("El proveedor no produjo un plan local válido.")
        return False
    if interpretation.plan is None:
        output("No se pudo construir un plan soportado.")
        return False

    output(f"Plan validado: {len(interpretation.plan.steps)} pasos.")
    try:
        execution = plan_executor.execute(
            interpretation.plan,
            cancellation=cancellation,
        )
    except PlanValidationError as error:
        output(f"Plan rechazado: {error}")
        return False

    for step in execution.step_results:
        if step.state is PlanStepState.SUCCEEDED and step.result is not None:
            output(f"{step.step_id}: {step.result.message}")
        elif step.state is PlanStepState.FAILED:
            output(f"{step.step_id}: error: {step.error}")
        else:
            output(f"{step.step_id}: {step.state.value}")
    output(f"Plan finalizado: {execution.status.value}.")
    return execution.status is PlanStatus.SUCCEEDED


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

    browser_runtime = BrowserRuntime()
    browser_runtime.start()
    try:
        executor, playback_controller = build_executor(
            logger,
            browser_preferences=browser_runtime.preferences,
            browser_bridge=browser_runtime.bridge,
        )

        if arguments and arguments[0] == "--plan":
            if len(arguments) == 1:
                print("Falta la tarea que se debe planificar.", file=sys.stderr)
                return 1
            planner = build_task_planner(
                warning_output=lambda message: print(message, file=sys.stderr)
            )
            success = process_plan_command(
                " ".join(arguments[1:]),
                TaskPlanExecutor(executor, logger),
                planner,
                logger,
            )
            if playback_controller.has_active_session:
                if success:
                    success = (
                        _hold_one_shot_playback(playback_controller) and success
                    )
                else:
                    success = (
                        _stop_active_playback(playback_controller, print) and success
                    )
            return 0 if success else 1

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
                success = (
                    _hold_one_shot_playback(playback_controller) and success
                )
            return 0 if success else 1

        return _run_interactive(
            executor,
            logger,
            interpreter,
            playback_controller,
        )
    finally:
        browser_runtime.close()
