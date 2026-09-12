import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from desktop_agent.browser_contract import normalize_search_query
from desktop_agent.catalog import SUPPORTED_APPLICATIONS, SUPPORTED_SITES
from desktop_agent.models import Action, Intent, RiskLevel
from desktop_agent.parser import parse_command

PROPOSAL_SCHEMA_VERSION = 1
_PROPOSAL_FIELDS = frozenset({"schema_version", "intent", "target"})


class ProposalIntent(str, Enum):
    """Intents que una fuente no confiable puede proponer."""

    OPEN_URL = "OPEN_URL"
    OPEN_APPLICATION = "OPEN_APPLICATION"
    PLAY_YOUTUBE = "PLAY_YOUTUBE"
    STOP_YOUTUBE = "STOP_YOUTUBE"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class ActionProposal:
    """Propuesta validada que todavía no tiene permiso de ejecución."""

    schema_version: int
    intent: ProposalIntent
    target: str | None


class ProposalValidationError(ValueError):
    """La propuesta no cumple el contrato o la política local."""


class ProposalProviderError(RuntimeError):
    """Un proveedor no pudo producir una propuesta interpretable."""

    def __init__(
        self,
        message: str,
        provider_result: "ProposalProviderResult | None" = None,
    ) -> None:
        super().__init__(message)
        self.provider_result = provider_result


class ProposalBudgetExceededError(ProposalProviderError):
    """El límite local impide realizar una nueva solicitud externa."""


class ProposalUsageTrackingError(ProposalProviderError):
    """No se puede garantizar el registro local del consumo externo."""


@dataclass(frozen=True)
class ProposalUsage:
    """Contadores numéricos seguros informados por un proveedor."""

    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0

    def __post_init__(self) -> None:
        values = (
            self.input_tokens,
            self.output_tokens,
            self.total_tokens,
            self.cached_input_tokens,
            self.cache_write_tokens,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("Los contadores de uso deben ser enteros no negativos.")
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("El total de tokens no coincide con entrada y salida.")
        if self.cached_input_tokens + self.cache_write_tokens > self.input_tokens:
            raise ValueError("El detalle de entrada supera los tokens de entrada.")


@dataclass(frozen=True)
class MonthlyUsageSnapshot:
    """Acumulado local del mes, sin contenido de órdenes ni respuestas."""

    month: str
    request_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_input_tokens: int
    cache_write_tokens: int
    estimated_cost_usd: float
    budget_usd: float
    remaining_usd: float
    unmetered_request_count: int = 0
    pending_reservation_count: int = 0

    def __post_init__(self) -> None:
        counters = (
            self.request_count,
            self.input_tokens,
            self.output_tokens,
            self.total_tokens,
            self.cached_input_tokens,
            self.cache_write_tokens,
            self.unmetered_request_count,
            self.pending_reservation_count,
        )
        if any(type(value) is not int or value < 0 for value in counters):
            raise ValueError("El acumulado mensual contiene contadores inválidos.")
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("El total mensual de tokens no coincide.")
        if self.cached_input_tokens + self.cache_write_tokens > self.input_tokens:
            raise ValueError("El detalle mensual supera los tokens de entrada.")
        if self.unmetered_request_count > self.request_count:
            raise ValueError("Las solicitudes sin medición superan el total mensual.")
        if self.pending_reservation_count > self.request_count:
            raise ValueError("Las reservas pendientes superan el total mensual.")

        for value in (
            self.estimated_cost_usd,
            self.budget_usd,
            self.remaining_usd,
        ):
            if (
                type(value) not in (int, float)
                or not math.isfinite(float(value))
                or float(value) < 0
            ):
                raise ValueError("El acumulado mensual contiene importes inválidos.")


@dataclass(frozen=True)
class ProposalProviderResult:
    """Datos no confiables y telemetría numérica devueltos por el adaptador."""

    payload: object
    usage: ProposalUsage | None = None
    estimated_cost_usd: float | None = None
    monthly_usage: MonthlyUsageSnapshot | None = None

    def __post_init__(self) -> None:
        cost = self.estimated_cost_usd
        if cost is None:
            return
        if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
            raise ValueError("El costo estimado debe ser un número no negativo.")
        object.__setattr__(self, "estimated_cost_usd", float(cost))


class ProposalProvider(Protocol):
    """Límite mínimo que implementa un adaptador externo."""

    def propose(self, command: str) -> ProposalProviderResult:
        """Devuelve datos no confiables y telemetría numérica opcional."""


class InterpretationPath(str, Enum):
    DETERMINISTIC = "deterministic"
    EXTERNAL = "external"


class InterpretationStatus(str, Enum):
    SUCCESS = "success"
    UNSUPPORTED = "unsupported"
    PROVIDER_ERROR = "provider_error"
    INVALID_PROPOSAL = "invalid_proposal"
    BUDGET_EXCEEDED = "budget_exceeded"
    USAGE_TRACKING_ERROR = "usage_tracking_error"


@dataclass(frozen=True)
class InterpretationResult:
    """Resultado observable sin conservar la orden ni la respuesta completa."""

    action: Action | None
    path: InterpretationPath
    status: InterpretationStatus
    duration_ms: float
    provider_configured: bool
    provider_name: str | None = None
    provider_model: str | None = None
    usage: ProposalUsage | None = None
    estimated_cost_usd: float | None = None
    monthly_usage: MonthlyUsageSnapshot | None = None


DeterministicParser = Callable[[str], Action | None]
Clock = Callable[[], float]


def _validate_proposal_values(
    schema_version: object,
    raw_intent: object,
    target: object,
) -> ActionProposal:
    if (
        type(schema_version) is not int
        or schema_version != PROPOSAL_SCHEMA_VERSION
    ):
        raise ProposalValidationError("La versión del contrato no está soportada.")
    if not isinstance(raw_intent, str):
        raise ProposalValidationError("El intent debe ser texto.")

    try:
        intent = ProposalIntent(raw_intent)
    except ValueError as error:
        raise ProposalValidationError("El intent no está permitido.") from error

    if target is not None and not isinstance(target, str):
        raise ProposalValidationError("El destino debe ser texto o null.")
    if isinstance(target, str) and (not target or target != target.strip()):
        raise ProposalValidationError(
            "El destino debe ser una clave canónica no vacía."
        )
    without_target = {ProposalIntent.UNSUPPORTED, ProposalIntent.STOP_YOUTUBE}
    if intent in without_target and target is not None:
        raise ProposalValidationError("Este intent requiere un destino null.")
    if intent not in without_target and target is None:
        raise ProposalValidationError("Un intent ejecutable requiere un destino.")
    if intent is ProposalIntent.PLAY_YOUTUBE:
        try:
            target = normalize_search_query(target)
        except ValueError:
            raise ProposalValidationError("La consulta de YouTube no es válida.") from None

    return ActionProposal(
        schema_version=schema_version,
        intent=intent,
        target=target,
    )


def validate_proposal(payload: object) -> ActionProposal:
    """Valida estrictamente datos no confiables contra el contrato v1."""

    if not isinstance(payload, Mapping):
        raise ProposalValidationError("La propuesta debe ser un único objeto.")

    payload_fields = set(payload.keys())
    if payload_fields != _PROPOSAL_FIELDS:
        raise ProposalValidationError(
            "La propuesta debe contener exactamente schema_version, intent y target."
        )

    return _validate_proposal_values(
        schema_version=payload["schema_version"],
        raw_intent=payload["intent"],
        target=payload["target"],
    )


def build_action_from_proposal(proposal: ActionProposal) -> Action | None:
    """Construye una acción solo con catálogo, permisos y argumentos locales."""

    proposal = _validate_proposal_values(
        schema_version=proposal.schema_version,
        raw_intent=proposal.intent,
        target=proposal.target,
    )

    if proposal.intent is ProposalIntent.UNSUPPORTED:
        return None

    if proposal.intent in {ProposalIntent.PLAY_YOUTUBE, ProposalIntent.STOP_YOUTUBE}:
        playing = proposal.intent is ProposalIntent.PLAY_YOUTUBE
        return Action(
            intent=Intent.BROWSER_NAVIGATION,
            tool_name="play_youtube" if playing else "stop_youtube",
            arguments={"query": proposal.target} if playing else {},
            risk_level=RiskLevel.SAFE,
            requires_confirmation=False,
        )

    target = proposal.target
    assert target is not None

    if proposal.intent is ProposalIntent.OPEN_URL:
        site = SUPPORTED_SITES.get(target)
        if site is None:
            raise ProposalValidationError("El sitio no pertenece al catálogo local.")
        return Action(
            intent=Intent.OPEN_URL,
            tool_name="open_url",
            arguments={"url": site.url},
            risk_level=RiskLevel.SAFE,
            requires_confirmation=False,
        )

    if proposal.intent is ProposalIntent.OPEN_APPLICATION:
        if target not in SUPPORTED_APPLICATIONS:
            raise ProposalValidationError(
                "La aplicación no pertenece al catálogo local."
            )
        return Action(
            intent=Intent.OPEN_APPLICATION,
            tool_name="open_application",
            arguments={"name": target},
            risk_level=RiskLevel.SAFE,
            requires_confirmation=False,
        )

    raise ProposalValidationError("El intent no se puede convertir en una acción.")


class HybridInterpreter:
    """Usa primero el parser determinista y luego un proveedor opcional."""

    def __init__(
        self,
        provider: ProposalProvider | None = None,
        deterministic_parser: DeterministicParser = parse_command,
        clock: Clock = time.perf_counter,
        provider_name: str | None = None,
        provider_model: str | None = None,
    ) -> None:
        self._provider = provider
        self._deterministic_parser = deterministic_parser
        self._clock = clock
        self._provider_name = provider_name
        self._provider_model = provider_model

    def interpret(self, command: str) -> Action | None:
        return self.interpret_detailed(command).action

    def interpret_detailed(self, command: str) -> InterpretationResult:
        started_at = self._clock()
        action = self._deterministic_parser(command)
        if action is not None:
            return self._result(
                action,
                InterpretationPath.DETERMINISTIC,
                InterpretationStatus.SUCCESS,
                started_at,
            )

        if self._provider is None:
            return self._result(
                None,
                InterpretationPath.DETERMINISTIC,
                InterpretationStatus.UNSUPPORTED,
                started_at,
            )

        try:
            provider_result = self._provider.propose(command)
        except ProposalBudgetExceededError as error:
            return self._result(
                None,
                InterpretationPath.EXTERNAL,
                InterpretationStatus.BUDGET_EXCEEDED,
                started_at,
                error.provider_result,
            )
        except ProposalUsageTrackingError as error:
            return self._result(
                None,
                InterpretationPath.EXTERNAL,
                InterpretationStatus.USAGE_TRACKING_ERROR,
                started_at,
                error.provider_result,
            )
        except ProposalProviderError as error:
            return self._result(
                None,
                InterpretationPath.EXTERNAL,
                InterpretationStatus.PROVIDER_ERROR,
                started_at,
                error.provider_result,
            )

        if not isinstance(provider_result, ProposalProviderResult):
            return self._result(
                None,
                InterpretationPath.EXTERNAL,
                InterpretationStatus.INVALID_PROPOSAL,
                started_at,
            )

        try:
            proposal = validate_proposal(provider_result.payload)
            action = build_action_from_proposal(proposal)
        except ProposalValidationError:
            return self._result(
                None,
                InterpretationPath.EXTERNAL,
                InterpretationStatus.INVALID_PROPOSAL,
                started_at,
                provider_result,
            )

        status = (
            InterpretationStatus.SUCCESS
            if action is not None
            else InterpretationStatus.UNSUPPORTED
        )
        return self._result(
            action,
            InterpretationPath.EXTERNAL,
            status,
            started_at,
            provider_result,
        )

    def _result(
        self,
        action: Action | None,
        path: InterpretationPath,
        status: InterpretationStatus,
        started_at: float,
        provider_result: ProposalProviderResult | None = None,
    ) -> InterpretationResult:
        duration_ms = max(0.0, (self._clock() - started_at) * 1_000)
        provider_configured = self._provider is not None
        return InterpretationResult(
            action=action,
            path=path,
            status=status,
            duration_ms=duration_ms,
            provider_configured=provider_configured,
            provider_name=(
                self._provider_name if provider_configured else None
            ),
            provider_model=(
                self._provider_model if provider_configured else None
            ),
            usage=(provider_result.usage if provider_result is not None else None),
            estimated_cost_usd=(
                provider_result.estimated_cost_usd
                if provider_result is not None
                else None
            ),
            monthly_usage=(
                provider_result.monthly_usage
                if provider_result is not None
                else None
            ),
        )
