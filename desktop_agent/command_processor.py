import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum

from desktop_agent.executor import ActionExecutionError, ActionExecutor
from desktop_agent.interpretation import (
    HybridInterpreter,
    InterpretationResult,
    InterpretationStatus,
)
from desktop_agent.models import ToolResult
from desktop_agent.diagnostics import emit_failure


class CommandStage(str, Enum):
    """Etapa observable de una ejecución local."""

    INTERPRETING = "interpreting"
    EXECUTING = "executing"
    FINISHED = "finished"


@dataclass(frozen=True)
class CommandProgress:
    stage: CommandStage
    tool_name: str | None = None


@dataclass(frozen=True)
class CommandExecution:
    """Resultado estructurado compartido por CLI y futuras interfaces."""

    success: bool
    messages: tuple[str, ...]
    interpretation: InterpretationResult
    duration_ms: float
    tool_result: ToolResult | None = None
    tool_name: str | None = None
    cancelled: bool = False


Output = Callable[[str], None]
ProgressOutput = Callable[[CommandProgress], None]
Clock = Callable[[], float]


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


def format_monthly_usage(result: InterpretationResult) -> str | None:
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


class CommandProcessor:
    """Interpreta y ejecuta una orden sin depender de una interfaz concreta."""

    def __init__(
        self,
        executor: ActionExecutor,
        interpreter: HybridInterpreter,
        logger: logging.Logger,
        clock: Clock = time.monotonic,
    ) -> None:
        self._executor = executor
        self._interpreter = interpreter
        self._logger = logger
        self._clock = clock

    def execute(
        self,
        command: str,
        output: Output = print,
        progress: ProgressOutput | None = None,
        *,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> CommandExecution:
        started_at = self._clock()
        messages: list[str] = []

        def emit(message: str) -> None:
            messages.append(message)
            output(message)

        def notify(stage: CommandStage, tool_name: str | None = None) -> None:
            if progress is not None:
                progress(CommandProgress(stage, tool_name))

        self._logger.info("Command received")
        notify(CommandStage.INTERPRETING)
        emit("Entendiendo comando...")

        interpretation = self._interpreter.interpret_detailed(command)
        _log_interpretation(self._logger, interpretation)
        monthly_usage_message = format_monthly_usage(interpretation)
        if monthly_usage_message is not None:
            emit(monthly_usage_message)

        if cancelled():
            emit("Orden cancelada antes de ejecutar acciones.")
            result = self._finished(False, messages, interpretation, started_at, progress)
            return replace(result, cancelled=True)

        if interpretation.status is not InterpretationStatus.SUCCESS:
            emit_failure(self._logger, interpretation.status.value, "interpretation",
                         stage="interpreting", duration_ms=interpretation.duration_ms)

        if interpretation.status is InterpretationStatus.BUDGET_EXCEEDED:
            self._logger.info("Status: AI_BUDGET_EXCEEDED")
            emit("Límite mensual de IA alcanzado; no se realizó la llamada.")
            return self._finished(
                False, messages, interpretation, started_at, progress
            )
        if interpretation.status is InterpretationStatus.USAGE_TRACKING_ERROR:
            self._logger.info("Status: AI_USAGE_TRACKING_ERROR")
            emit(
                "No se pudo verificar el consumo de IA; "
                "no se ejecutó ninguna acción."
            )
            return self._finished(
                False, messages, interpretation, started_at, progress
            )

        action = interpretation.action
        if action is None:
            self._logger.info("Status: %s", interpretation.status.value.upper())
            if interpretation.status is InterpretationStatus.PROVIDER_ERROR:
                emit("La IA no pudo responder. Revisá conexión, clave y saldo de API en Configuración.")
            elif interpretation.status is InterpretationStatus.INVALID_PROPOSAL:
                emit("La IA devolvió una propuesta inválida; no se ejecutó ninguna acción.")
            elif not interpretation.provider_configured:
                emit("Comando no soportado todavía. Activá la interpretación con IA en Configuración para usar frases libres.")
            else:
                emit("Ese pedido todavía no tiene una herramienta disponible. Consultá los ejemplos de la aplicación.")
            return self._finished(
                False, messages, interpretation, started_at, progress
            )

        self._logger.info("Intent: %s", action.intent.value)
        if "url" in action.arguments:
            self._logger.info("URL: %s", action.arguments["url"])
        if "name" in action.arguments:
            self._logger.info("Application: %s", action.arguments["name"])

        notify(CommandStage.EXECUTING, action.tool_name)
        emit(f"Ejecutando {action.tool_name}...")
        try:
            tool_result = self._executor.execute(action)
        except ActionExecutionError as error:
            emit(f"Error: {error}")
            return self._finished(
                False,
                messages,
                interpretation,
                started_at,
                progress,
                tool_name=action.tool_name,
            )

        emit(tool_result.message)
        return self._finished(
            True,
            messages,
            interpretation,
            started_at,
            progress,
            tool_result=tool_result,
            tool_name=action.tool_name,
        )

    def _finished(
        self,
        success: bool,
        messages: list[str],
        interpretation: InterpretationResult,
        started_at: float,
        progress: ProgressOutput | None,
        tool_result: ToolResult | None = None,
        tool_name: str | None = None,
    ) -> CommandExecution:
        duration_ms = max(0.0, (self._clock() - started_at) * 1_000)
        if progress is not None:
            progress(CommandProgress(CommandStage.FINISHED, tool_name))
        return CommandExecution(
            success=success,
            messages=tuple(messages),
            interpretation=interpretation,
            duration_ms=duration_ms,
            tool_result=tool_result,
            tool_name=tool_name,
        )
