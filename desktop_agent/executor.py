import logging
from collections.abc import Callable, Mapping

from desktop_agent.models import Action, RiskLevel, ToolResult

Tool = Callable[..., ToolResult]


class ActionExecutionError(RuntimeError):
    """Error controlado al validar o ejecutar una acción."""


class ActionExecutor:
    """Ejecuta únicamente herramientas registradas de forma explícita."""

    def __init__(self, tools: Mapping[str, Tool], logger: logging.Logger) -> None:
        self._tools = dict(tools)
        self._logger = logger

    def execute(self, action: Action) -> ToolResult:
        if action.risk_level is not RiskLevel.SAFE or action.requires_confirmation:
            raise ActionExecutionError(
                "La acción no es segura sin confirmación, pero v0.1 aún no "
                "incluye ese flujo."
            )

        tool = self._tools.get(action.tool_name)
        if tool is None:
            raise ActionExecutionError(
                f"La herramienta '{action.tool_name}' no está registrada."
            )

        self._logger.info("Tool: %s", action.tool_name)
        try:
            result = tool(**action.arguments)
        except Exception as error:
            self._logger.exception("Status: ERROR")
            raise ActionExecutionError(
                f"No se pudo ejecutar '{action.tool_name}': {error}"
            ) from error

        status = "SUCCESS" if result.success else "ERROR"
        self._logger.info("Status: %s", status)
        if not result.success:
            raise ActionExecutionError(result.message)

        return result
