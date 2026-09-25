import logging
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from desktop_agent.browser_contract import normalize_search_query
from desktop_agent.approved_targets import normalize_target_name
from desktop_agent.catalog import SUPPORTED_APPLICATIONS, SUPPORTED_SITES
from desktop_agent.executor import ActionExecutionError, ActionExecutor
from desktop_agent.interpretation import (
    InterpretationPath,
    MonthlyUsageSnapshot,
    ProposalBudgetExceededError,
    ProposalProvider,
    ProposalProviderError,
    ProposalProviderResult,
    ProposalUsage,
    ProposalUsageTrackingError,
)
from desktop_agent.models import Action, Intent, RiskLevel, ToolResult
from desktop_agent.output_audio import validate_output_volume_target
from desktop_agent.parser import parse_command

PLAN_SCHEMA_VERSION = 1
_PLAN_FIELDS = frozenset({"schema_version", "steps"})
_PLAN_STEP_FIELDS = frozenset({"intent", "target"})
_PLAN_SEPARATOR = re.compile(
    r"\s*(?:;|\by\s+despu[eé]s\b|\bluego\b)\s*",
    re.IGNORECASE,
)


class PlanProposalIntent(str, Enum):
    OPEN_URL = "OPEN_URL"
    OPEN_APPLICATION = "OPEN_APPLICATION"
    OPEN_APPROVED_TARGET = "OPEN_APPROVED_TARGET"
    PLAY_YOUTUBE = "PLAY_YOUTUBE"
    STOP_YOUTUBE = "STOP_YOUTUBE"
    RESUME_YOUTUBE = "RESUME_YOUTUBE"
    PLAY_SPOTIFY_TRACK = "PLAY_SPOTIFY_TRACK"
    PLAY_SPOTIFY_PLAYLIST = "PLAY_SPOTIFY_PLAYLIST"
    SEARCH_SPOTIFY_TRACK = "SEARCH_SPOTIFY_TRACK"
    PAUSE_SPOTIFY = "PAUSE_SPOTIFY"
    RESUME_SPOTIFY = "RESUME_SPOTIFY"
    NEXT_SPOTIFY = "NEXT_SPOTIFY"
    PREVIOUS_SPOTIFY = "PREVIOUS_SPOTIFY"
    SET_SPOTIFY_VOLUME = "SET_SPOTIFY_VOLUME"
    SET_OUTPUT_VOLUME = "SET_OUTPUT_VOLUME"


class PlanStepState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class PlanStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    REJECTED = "rejected"


class PlanInterpretationStatus(str, Enum):
    SUCCESS = "success"
    UNSUPPORTED = "unsupported"
    PROVIDER_ERROR = "provider_error"
    INVALID_PROPOSAL = "invalid_proposal"
    BUDGET_EXCEEDED = "budget_exceeded"
    USAGE_TRACKING_ERROR = "usage_tracking_error"


@dataclass(frozen=True)
class PlanLimits:
    max_steps: int = 5
    max_duration_seconds: float = 30.0

    def __post_init__(self) -> None:
        if type(self.max_steps) is not int or not 2 <= self.max_steps <= 20:
            raise ValueError("El máximo de pasos debe estar entre 2 y 20.")
        if (
            type(self.max_duration_seconds) not in (int, float)
            or not 1 <= float(self.max_duration_seconds) <= 300
        ):
            raise ValueError("La duración máxima debe estar entre 1 y 300 segundos.")
        object.__setattr__(
            self, "max_duration_seconds", float(self.max_duration_seconds)
        )


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    action: Action


@dataclass(frozen=True)
class TaskPlan:
    plan_id: str
    steps: tuple[PlanStep, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "steps", tuple(self.steps))


@dataclass(frozen=True)
class PlanStepResult:
    step_id: str
    state: PlanStepState
    tool_name: str
    duration_ms: float
    result: ToolResult | None = None
    error: str | None = None


@dataclass(frozen=True)
class PlanExecution:
    plan_id: str
    status: PlanStatus
    step_results: tuple[PlanStepResult, ...]
    duration_ms: float


@dataclass(frozen=True)
class PlanInterpretationResult:
    plan: TaskPlan | None
    path: InterpretationPath
    status: PlanInterpretationStatus
    duration_ms: float
    provider_configured: bool
    provider_name: str | None = None
    provider_model: str | None = None
    usage: ProposalUsage | None = None
    estimated_cost_usd: float | None = None
    monthly_usage: MonthlyUsageSnapshot | None = None


class PlanValidationError(ValueError):
    """El plan completo no puede autorizarse."""


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()


PlanIdFactory = Callable[[], str]
Clock = Callable[[], float]


def _new_plan_id() -> str:
    return f"plan-{uuid.uuid4().hex}"


def _build_action(intent: PlanProposalIntent, target: object) -> Action:
    if intent is PlanProposalIntent.OPEN_URL:
        if not isinstance(target, str) or target not in SUPPORTED_SITES:
            raise PlanValidationError("El destino web del plan no está permitido.")
        return Action(
            Intent.OPEN_URL,
            "open_url",
            {"url": SUPPORTED_SITES[target].url},
            RiskLevel.SAFE,
            False,
        )
    if intent is PlanProposalIntent.OPEN_APPLICATION:
        if not isinstance(target, str) or target not in SUPPORTED_APPLICATIONS:
            raise PlanValidationError("La aplicación del plan no está permitida.")
        return Action(
            Intent.OPEN_APPLICATION,
            "open_application",
            {"name": target},
            RiskLevel.SAFE,
            False,
        )
    if intent is PlanProposalIntent.OPEN_APPROVED_TARGET:
        try:
            normalize_target_name(target)
        except ValueError:
            raise PlanValidationError("El nombre del destino aprobado no es válido.") from None
        return Action(
            Intent.OPEN_APPLICATION,
            "open_approved_target",
            {"name": target},
            RiskLevel.SAFE,
            False,
        )
    if intent is PlanProposalIntent.PLAY_YOUTUBE:
        if not isinstance(target, str):
            raise PlanValidationError("La consulta de YouTube no es válida.")
        try:
            query = normalize_search_query(target)
        except ValueError as error:
            raise PlanValidationError(
                "La consulta de YouTube no es válida."
            ) from error
        return Action(
            Intent.BROWSER_NAVIGATION,
            "play_youtube",
            {"query": query},
            RiskLevel.SAFE,
            False,
        )
    if intent is PlanProposalIntent.STOP_YOUTUBE:
        if target is not None:
            raise PlanValidationError("Detener YouTube no admite un destino.")
        return Action(
            Intent.BROWSER_NAVIGATION,
            "stop_youtube",
            {},
            RiskLevel.SAFE,
            False,
        )
    if intent is PlanProposalIntent.RESUME_YOUTUBE:
        if target is not None:
            raise PlanValidationError("Reanudar YouTube no admite un destino.")
        return Action(
            Intent.BROWSER_NAVIGATION,
            "resume_youtube",
            {},
            RiskLevel.SAFE,
            False,
        )
    spotify_targets = {
        PlanProposalIntent.PLAY_SPOTIFY_TRACK: ("play_spotify_track", "query"),
        PlanProposalIntent.PLAY_SPOTIFY_PLAYLIST: ("play_spotify_playlist", "name"),
        PlanProposalIntent.SEARCH_SPOTIFY_TRACK: ("search_spotify_track", "query"),
    }
    if intent in spotify_targets:
        if not isinstance(target, str):
            raise PlanValidationError("La búsqueda de Spotify no es válida.")
        try:
            normalized = normalize_search_query(target)
        except ValueError as error:
            raise PlanValidationError("La búsqueda de Spotify no es válida.") from error
        tool_name, argument_name = spotify_targets[intent]
        return Action(
            Intent.MEDIA_PLAYBACK,
            tool_name,
            {argument_name: normalized},
            RiskLevel.SAFE,
            False,
        )
    spotify_controls = {
        PlanProposalIntent.PAUSE_SPOTIFY: "pause_spotify",
        PlanProposalIntent.RESUME_SPOTIFY: "resume_spotify",
        PlanProposalIntent.NEXT_SPOTIFY: "next_spotify",
        PlanProposalIntent.PREVIOUS_SPOTIFY: "previous_spotify",
    }
    if intent in spotify_controls:
        if target is not None:
            raise PlanValidationError("El control de Spotify no admite un destino.")
        return Action(
            Intent.MEDIA_PLAYBACK,
            spotify_controls[intent],
            {},
            RiskLevel.SAFE,
            False,
        )
    if intent is PlanProposalIntent.SET_SPOTIFY_VOLUME:
        if (
            not isinstance(target, str)
            or not target.isascii()
            or not target.isdigit()
            or target != str(int(target))
            or not 0 <= int(target) <= 100
        ):
            raise PlanValidationError("El volumen de Spotify no es válido.")
        return Action(
            Intent.MEDIA_PLAYBACK,
            "set_spotify_volume",
            {"percent": target},
            RiskLevel.SAFE,
            False,
        )
    if intent is PlanProposalIntent.SET_OUTPUT_VOLUME:
        try:
            request = validate_output_volume_target(target)
        except ValueError:
            raise PlanValidationError(
                "El volumen de salida del plan no es válido."
            ) from None
        return Action(
            Intent.SYSTEM_CHANGE,
            "set_output_volume",
            {"device": request.device, "percent": request.percent},
            RiskLevel.SAFE,
            False,
        )
    raise PlanValidationError("El intent del plan no está permitido.")


def validate_plan_proposal(
    raw: object,
    plan_id_factory: PlanIdFactory = _new_plan_id,
) -> TaskPlan:
    if not isinstance(raw, dict) or set(raw) != _PLAN_FIELDS:
        raise PlanValidationError("La propuesta de plan tiene una forma inválida.")
    if raw["schema_version"] != PLAN_SCHEMA_VERSION:
        raise PlanValidationError("La versión de la propuesta no es compatible.")
    raw_steps = raw["steps"]
    if not isinstance(raw_steps, list) or not 2 <= len(raw_steps) <= 5:
        raise PlanValidationError("El plan debe contener entre 2 y 5 pasos.")

    steps: list[PlanStep] = []
    for index, raw_step in enumerate(raw_steps, start=1):
        if not isinstance(raw_step, dict) or set(raw_step) != _PLAN_STEP_FIELDS:
            raise PlanValidationError("Un paso propuesto tiene una forma inválida.")
        try:
            intent = PlanProposalIntent(raw_step["intent"])
        except (TypeError, ValueError):
            raise PlanValidationError(
                "Un paso propuesto usa un intent desconocido."
            ) from None
        action = _build_action(intent, raw_step["target"])
        steps.append(PlanStep(f"step-{index}", action))

    plan_id = plan_id_factory()
    if not isinstance(plan_id, str) or not plan_id:
        raise PlanValidationError("No se pudo crear un identificador local.")
    return TaskPlan(plan_id, tuple(steps))


def parse_plan_command(
    command: str,
    plan_id_factory: PlanIdFactory = _new_plan_id,
) -> TaskPlan | None:
    if not isinstance(command, str):
        return None
    parts = [part.strip() for part in _PLAN_SEPARATOR.split(command.strip())]
    if len(parts) < 2 or any(not part for part in parts):
        return None
    actions = [parse_command(part) for part in parts]
    if any(action is None for action in actions):
        return None
    plan_id = plan_id_factory()
    if not isinstance(plan_id, str) or not plan_id:
        return None
    return TaskPlan(
        plan_id,
        tuple(
            PlanStep(f"step-{index}", action)
            for index, action in enumerate(actions, start=1)
            if action is not None
        ),
    )


class TaskPlanner:
    """Prioriza planes deterministas y trata al proveedor como no confiable."""

    def __init__(
        self,
        provider: ProposalProvider | None = None,
        provider_name: str | None = None,
        provider_model: str | None = None,
        plan_id_factory: PlanIdFactory = _new_plan_id,
        clock: Clock = time.monotonic,
    ) -> None:
        self._provider = provider
        self._provider_name = provider_name
        self._provider_model = provider_model
        self._plan_id_factory = plan_id_factory
        self._clock = clock

    def interpret(self, command: str) -> PlanInterpretationResult:
        started_at = self._clock()
        deterministic = parse_plan_command(command, self._plan_id_factory)
        if deterministic is not None:
            return self._result(
                deterministic,
                InterpretationPath.DETERMINISTIC,
                PlanInterpretationStatus.SUCCESS,
                started_at,
            )
        if self._provider is None:
            return self._result(
                None,
                InterpretationPath.DETERMINISTIC,
                PlanInterpretationStatus.UNSUPPORTED,
                started_at,
            )
        try:
            provider_result = self._provider.propose(command)
        except ProposalBudgetExceededError as error:
            return self._result(
                None,
                InterpretationPath.EXTERNAL,
                PlanInterpretationStatus.BUDGET_EXCEEDED,
                started_at,
                error.provider_result,
            )
        except ProposalUsageTrackingError as error:
            return self._result(
                None,
                InterpretationPath.EXTERNAL,
                PlanInterpretationStatus.USAGE_TRACKING_ERROR,
                started_at,
                error.provider_result,
            )
        except ProposalProviderError as error:
            return self._result(
                None,
                InterpretationPath.EXTERNAL,
                PlanInterpretationStatus.PROVIDER_ERROR,
                started_at,
                error.provider_result,
            )
        if not isinstance(provider_result, ProposalProviderResult):
            return self._result(
                None,
                InterpretationPath.EXTERNAL,
                PlanInterpretationStatus.INVALID_PROPOSAL,
                started_at,
            )
        try:
            plan = validate_plan_proposal(
                provider_result.payload, self._plan_id_factory
            )
        except PlanValidationError:
            return self._result(
                None,
                InterpretationPath.EXTERNAL,
                PlanInterpretationStatus.INVALID_PROPOSAL,
                started_at,
                provider_result,
            )
        return self._result(
            plan,
            InterpretationPath.EXTERNAL,
            PlanInterpretationStatus.SUCCESS,
            started_at,
            provider_result,
        )

    def _result(
        self,
        plan: TaskPlan | None,
        path: InterpretationPath,
        status: PlanInterpretationStatus,
        started_at: float,
        provider_result: ProposalProviderResult | None = None,
    ) -> PlanInterpretationResult:
        configured = self._provider is not None
        return PlanInterpretationResult(
            plan=plan,
            path=path,
            status=status,
            duration_ms=max(0.0, (self._clock() - started_at) * 1_000),
            provider_configured=configured,
            provider_name=self._provider_name if configured else None,
            provider_model=self._provider_model if configured else None,
            usage=provider_result.usage if provider_result else None,
            estimated_cost_usd=(
                provider_result.estimated_cost_usd if provider_result else None
            ),
            monthly_usage=(
                provider_result.monthly_usage if provider_result else None
            ),
        )


class TaskPlanValidator:
    def __init__(
        self,
        executor: ActionExecutor,
        limits: PlanLimits = PlanLimits(),
    ) -> None:
        self._executor = executor
        self._limits = limits

    @property
    def limits(self) -> PlanLimits:
        return self._limits

    def validate(self, plan: TaskPlan) -> None:
        if not isinstance(plan, TaskPlan):
            raise PlanValidationError("El plan no cumple el contrato local.")
        if not isinstance(plan.plan_id, str) or not plan.plan_id:
            raise PlanValidationError("El identificador del plan no es válido.")
        if not 2 <= len(plan.steps) <= self._limits.max_steps:
            raise PlanValidationError(
                f"El plan debe tener entre 2 y {self._limits.max_steps} pasos."
            )
        step_ids: set[str] = set()
        for step in plan.steps:
            if not isinstance(step, PlanStep):
                raise PlanValidationError("Un paso no cumple el contrato local.")
            if not step.step_id or step.step_id in step_ids:
                raise PlanValidationError(
                    "Los identificadores de paso deben ser únicos."
                )
            step_ids.add(step.step_id)
            self._validate_action(step.action)
            try:
                self._executor.validate(step.action)
            except ActionExecutionError as error:
                raise PlanValidationError(str(error)) from error

    @staticmethod
    def _validate_action(action: Action) -> None:
        if not isinstance(action, Action):
            raise PlanValidationError("La acción del paso no es válida.")
        arguments = dict(action.arguments)
        if not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in arguments.items()
        ):
            raise PlanValidationError("Los argumentos del paso no son válidos.")
        if action.tool_name == "open_url":
            allowed_urls = {site.url for site in SUPPORTED_SITES.values()}
            valid = (
                action.intent is Intent.OPEN_URL
                and set(arguments) == {"url"}
                and arguments["url"] in allowed_urls
            )
        elif action.tool_name == "open_application":
            valid = (
                action.intent is Intent.OPEN_APPLICATION
                and set(arguments) == {"name"}
                and arguments["name"] in SUPPORTED_APPLICATIONS
            )
        elif action.tool_name == "open_approved_target":
            valid = (
                action.intent is Intent.OPEN_APPLICATION
                and set(arguments) in ({"name"}, {"name", "kind"})
                and (
                    "kind" not in arguments
                    or arguments["kind"] in {"project", "document"}
                )
            )
            if valid:
                try:
                    normalize_target_name(arguments["name"])
                except ValueError:
                    valid = False
        elif action.tool_name == "set_output_volume":
            valid = action.intent is Intent.SYSTEM_CHANGE and set(arguments) == {
                "device",
                "percent",
            }
            if valid:
                try:
                    validate_output_volume_target(arguments)
                except ValueError:
                    valid = False
        elif action.tool_name == "play_youtube":
            if (
                action.intent is not Intent.BROWSER_NAVIGATION
                or set(arguments) != {"query"}
            ):
                valid = False
            else:
                try:
                    valid = (
                        normalize_search_query(arguments["query"])
                        == arguments["query"]
                    )
                except ValueError:
                    valid = False
        elif action.tool_name in {"stop_youtube", "resume_youtube"}:
            valid = (
                action.intent is Intent.BROWSER_NAVIGATION and not arguments
            )
        elif action.tool_name in {
            "play_spotify_track",
            "play_spotify_playlist",
            "search_spotify_track",
        }:
            argument_name = (
                "name" if action.tool_name == "play_spotify_playlist" else "query"
            )
            if (
                action.intent is not Intent.MEDIA_PLAYBACK
                or set(arguments) != {argument_name}
            ):
                valid = False
            else:
                try:
                    valid = (
                        normalize_search_query(arguments[argument_name])
                        == arguments[argument_name]
                    )
                except ValueError:
                    valid = False
        elif action.tool_name in {
            "pause_spotify",
            "resume_spotify",
            "next_spotify",
            "previous_spotify",
        }:
            valid = action.intent is Intent.MEDIA_PLAYBACK and not arguments
        elif action.tool_name == "set_spotify_volume":
            value = arguments.get("percent")
            valid = (
                action.intent is Intent.MEDIA_PLAYBACK
                and set(arguments) == {"percent"}
                and isinstance(value, str)
                and value.isascii()
                and value.isdigit()
                and value == str(int(value))
                and 0 <= int(value) <= 100
            )
        else:
            valid = False
        if not valid:
            raise PlanValidationError(
                "La acción contiene una herramienta o argumentos no permitidos."
            )


class TaskPlanExecutor:
    """Valida todo antes del primer efecto y nunca reintenta por su cuenta."""

    def __init__(
        self,
        executor: ActionExecutor,
        logger: logging.Logger,
        limits: PlanLimits = PlanLimits(),
        clock: Clock = time.monotonic,
    ) -> None:
        self._executor = executor
        self._logger = logger
        self._validator = TaskPlanValidator(executor, limits)
        self._clock = clock

    def execute(
        self,
        plan: TaskPlan,
        cancellation: CancellationToken | None = None,
    ) -> PlanExecution:
        try:
            self._validator.validate(plan)
        except PlanValidationError:
            self._logger.info("Plan: status=REJECTED")
            raise
        token = cancellation or CancellationToken()
        started_at = self._clock()
        results: list[PlanStepResult] = []
        self._logger.info("Plan: id=%s status=RUNNING", plan.plan_id)

        for index, step in enumerate(plan.steps):
            elapsed = self._clock() - started_at
            if token.is_cancelled:
                results.extend(
                    self._remaining(
                        plan.steps[index:], PlanStepState.CANCELLED
                    )
                )
                return self._finish(plan, PlanStatus.CANCELLED, results, started_at)
            if elapsed >= self._validator.limits.max_duration_seconds:
                results.extend(
                    self._remaining(plan.steps[index:], PlanStepState.SKIPPED)
                )
                return self._finish(plan, PlanStatus.TIMED_OUT, results, started_at)

            step_started_at = self._clock()
            self._logger.info(
                "Plan step: id=%s tool=%s status=RUNNING",
                step.step_id,
                step.action.tool_name,
            )
            try:
                tool_result = self._executor.execute(step.action)
            except ActionExecutionError as error:
                duration_ms = max(
                    0.0, (self._clock() - step_started_at) * 1_000
                )
                results.append(
                    PlanStepResult(
                        step.step_id,
                        PlanStepState.FAILED,
                        step.action.tool_name,
                        duration_ms,
                        error=str(error),
                    )
                )
                results.extend(
                    self._remaining(plan.steps[index + 1 :], PlanStepState.SKIPPED)
                )
                self._logger.info(
                    "Plan step: id=%s tool=%s status=FAILED duration_ms=%.3f",
                    step.step_id,
                    step.action.tool_name,
                    duration_ms,
                )
                return self._finish(plan, PlanStatus.FAILED, results, started_at)

            duration_ms = max(
                0.0, (self._clock() - step_started_at) * 1_000
            )
            results.append(
                PlanStepResult(
                    step.step_id,
                    PlanStepState.SUCCEEDED,
                    step.action.tool_name,
                    duration_ms,
                    result=tool_result,
                )
            )
            self._logger.info(
                "Plan step: id=%s tool=%s status=SUCCEEDED duration_ms=%.3f",
                step.step_id,
                step.action.tool_name,
                duration_ms,
            )

        return self._finish(plan, PlanStatus.SUCCEEDED, results, started_at)

    def _remaining(
        self,
        steps: tuple[PlanStep, ...], state: PlanStepState
    ) -> list[PlanStepResult]:
        results = [
            PlanStepResult(step.step_id, state, step.action.tool_name, 0.0)
            for step in steps
        ]
        for result in results:
            self._logger.info(
                "Plan step: id=%s tool=%s status=%s duration_ms=0.000",
                result.step_id,
                result.tool_name,
                result.state.value.upper(),
            )
        return results

    def _finish(
        self,
        plan: TaskPlan,
        status: PlanStatus,
        results: list[PlanStepResult],
        started_at: float,
    ) -> PlanExecution:
        duration_ms = max(0.0, (self._clock() - started_at) * 1_000)
        self._logger.info(
            "Plan: id=%s status=%s duration_ms=%.3f",
            plan.plan_id,
            status.value.upper(),
            duration_ms,
        )
        return PlanExecution(plan.plan_id, status, tuple(results), duration_ms)
