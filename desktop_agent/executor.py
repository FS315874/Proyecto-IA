import logging
import time
from collections.abc import Callable, Mapping
from typing import Protocol, runtime_checkable

from desktop_agent.models import Action, RiskLevel, ToolResult
from desktop_agent.diagnostics import emit_failure

Tool = Callable[..., ToolResult]


@runtime_checkable
class AuthorizationValidator(Protocol):
    def validate(self, action: Action, authorization: object) -> None: ...

    def consume(self, action: Action, authorization: object) -> None: ...


class ActionExecutionError(RuntimeError):
    """Error controlado al validar o ejecutar una acción."""


class ActionExecutor:
    """Ejecuta únicamente herramientas registradas de forma explícita."""

    def __init__(
        self,
        tools: Mapping[str, Tool],
        logger: logging.Logger,
        authorization_validator: AuthorizationValidator | None = None,
    ) -> None:
        if authorization_validator is not None and not isinstance(
            authorization_validator,
            AuthorizationValidator,
        ):
            raise TypeError("El validador de autorizaciones no cumple su contrato.")
        self._tools = dict(tools)
        self._logger = logger
        self._authorization_validator = authorization_validator

    def validate(
        self,
        action: Action,
        authorization: object | None = None,
    ) -> None:
        """Comprueba una acción sin producir ningún efecto."""

        if not isinstance(action, Action):
            raise ActionExecutionError("La acción no cumple el contrato local.")
        if action.risk_level is RiskLevel.SAFE:
            if action.requires_confirmation or authorization is not None:
                raise ActionExecutionError(
                    "La metadata de seguridad de la acción es inconsistente."
                )
        elif (
            action.risk_level not in {RiskLevel.CAUTION, RiskLevel.DANGEROUS}
            or not action.requires_confirmation
        ):
            raise ActionExecutionError(
                "La metadata de seguridad de la acción es inconsistente."
            )
        elif authorization is None or self._authorization_validator is None:
            raise ActionExecutionError("La acción no tiene una autorización válida.")
        else:
            try:
                self._authorization_validator.validate(action, authorization)
            except Exception as error:
                raise ActionExecutionError(
                    "La acción no tiene una autorización válida."
                ) from error

        tool = self._tools.get(action.tool_name)
        if tool is None:
            raise ActionExecutionError(
                f"La herramienta '{action.tool_name}' no está registrada."
            )

    def execute(
        self,
        action: Action,
        authorization: object | None = None,
    ) -> ToolResult:
        started = time.monotonic()
        try:
            self.validate(action, authorization)
        except ActionExecutionError:
            emit_failure(self._logger, "action_rejected", "executor", stage="validating")
            raise
        tool = self._tools[action.tool_name]

        if action.risk_level is not RiskLevel.SAFE:
            assert authorization is not None
            assert self._authorization_validator is not None
            try:
                self._authorization_validator.consume(action, authorization)
            except Exception as error:
                emit_failure(self._logger, "action_rejected", "executor", stage="validating", tool=action.tool_name)
                raise ActionExecutionError(
                    "La autorización ya no es válida."
                ) from error

        self._logger.info("Tool: %s", action.tool_name)
        try:
            result = tool(**action.arguments)
        except Exception as error:
            emit_failure(self._logger, "tool_exception", "executor", stage="executing",
                         tool=action.tool_name, duration_ms=(time.monotonic() - started) * 1000)
            self._logger.error(
                "Status: ERROR tool=%s error=tool_failure",
                action.tool_name,
            )
            raise ActionExecutionError(
                f"No se pudo ejecutar '{action.tool_name}'."
            ) from error

        if not isinstance(result, ToolResult):
            emit_failure(self._logger, "invalid_tool_result", "executor", stage="executing",
                         tool=action.tool_name, duration_ms=(time.monotonic() - started) * 1000)
            self._logger.error(
                "Status: ERROR tool=%s error=invalid_tool_result",
                action.tool_name,
            )
            raise ActionExecutionError(
                f"La herramienta '{action.tool_name}' devolvió un resultado inválido."
            )

        status = "SUCCESS" if result.success else "ERROR"
        self._logger.info("Status: %s", status)
        if not result.success:
            emit_failure(self._logger, result.error_code or "tool_failed", "executor",
                         stage=result.error_stage or "executing", tool=action.tool_name,
                         duration_ms=(time.monotonic() - started) * 1000)
            raise ActionExecutionError(result.message)

        return result
