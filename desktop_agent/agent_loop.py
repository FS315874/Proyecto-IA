import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")


class LoopContractError(ValueError):
    pass


class LoopState(str, Enum):
    IDLE = "idle"
    OBSERVING = "observing"
    DECIDING = "deciding"
    ACTING = "acting"
    EVALUATING = "evaluating"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    AMBIGUOUS = "ambiguous"

    @property
    def terminal(self) -> bool:
        return self in {
            LoopState.SUCCEEDED,
            LoopState.FAILED,
            LoopState.CANCELLED,
            LoopState.TIMED_OUT,
            LoopState.AMBIGUOUS,
        }


class DecisionKind(str, Enum):
    ACTION = "action"
    AMBIGUOUS = "ambiguous"
    ABANDON = "abandon"


class ActionOutcomeStatus(str, Enum):
    DELIVERED = "delivered"
    TRANSIENT_FAILURE = "transient_failure"
    PERMANENT_FAILURE = "permanent_failure"


class EvaluationKind(str, Enum):
    SUCCESS = "success"
    PROGRESS = "progress"
    RETRY = "retry"
    AMBIGUOUS = "ambiguous"
    ABANDON = "abandon"


class LoopCause(str, Enum):
    TRANSIENT_UI = "transient_ui"
    TRANSIENT_TIMEOUT = "transient_timeout"
    AMBIGUOUS_STATE = "ambiguous_state"
    POLICY_BLOCK = "policy_block"
    VALIDATION_FAILURE = "validation_failure"
    OBSERVATION_FAILURE = "observation_failure"
    DECISION_FAILURE = "decision_failure"
    ACTION_FAILURE = "action_failure"
    EVALUATION_FAILURE = "evaluation_failure"

    @property
    def transient(self) -> bool:
        return self in {
            LoopCause.TRANSIENT_UI,
            LoopCause.TRANSIENT_TIMEOUT,
        }


@dataclass(frozen=True)
class LoopLimits:
    max_duration_seconds: float = 30.0
    max_observations: int = 5
    max_actions: int = 3
    max_retries_per_cause: int = 1
    max_ambiguities: int = 2

    def __post_init__(self) -> None:
        timeout = self.max_duration_seconds
        if type(timeout) not in (int, float) or not 1 <= float(timeout) <= 300:
            raise LoopContractError("La duración del bucle no es válida.")
        object.__setattr__(self, "max_duration_seconds", float(timeout))
        integers = (
            self.max_observations,
            self.max_actions,
            self.max_retries_per_cause,
            self.max_ambiguities,
        )
        if any(type(value) is not int or value < 1 for value in integers):
            raise LoopContractError("Los límites del bucle deben ser positivos.")
        if self.max_actions > self.max_observations:
            raise LoopContractError(
                "Las acciones no pueden superar las observaciones."
            )


def _validate_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise LoopContractError(f"{label} no es un identificador seguro.")
    return value


@dataclass(frozen=True)
class LoopTask:
    task_id: str
    goal_key: str
    allowed_tools: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_id(self.task_id, "task_id")
        _validate_id(self.goal_key, "goal_key")
        tools = tuple(self.allowed_tools)
        if not tools or any(
            not isinstance(tool, str) or not _SAFE_ID.fullmatch(tool)
            for tool in tools
        ):
            raise LoopContractError("La allowlist de herramientas no es válida.")
        if len(set(tools)) != len(tools):
            raise LoopContractError("La allowlist contiene duplicados.")
        object.__setattr__(self, "allowed_tools", tools)


@dataclass(frozen=True)
class LoopObservation:
    observation_id: str
    state_token: str

    def __post_init__(self) -> None:
        _validate_id(self.observation_id, "observation_id")
        _validate_id(self.state_token, "state_token")


@dataclass(frozen=True)
class LoopDecision:
    decision_id: str
    kind: DecisionKind
    observed_state_token: str
    tool_name: str | None = None
    action_fingerprint: str | None = None

    def __post_init__(self) -> None:
        _validate_id(self.decision_id, "decision_id")
        _validate_id(self.observed_state_token, "observed_state_token")
        if not isinstance(self.kind, DecisionKind):
            raise LoopContractError("La decisión no tiene un tipo válido.")
        if self.kind is DecisionKind.ACTION:
            _validate_id(self.tool_name, "tool_name")
            _validate_id(self.action_fingerprint, "action_fingerprint")
        elif self.tool_name is not None or self.action_fingerprint is not None:
            raise LoopContractError(
                "Una decisión sin acción no puede declarar herramienta."
            )


@dataclass(frozen=True)
class LoopActionOutcome:
    status: ActionOutcomeStatus
    cause: LoopCause | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ActionOutcomeStatus):
            raise LoopContractError("El resultado de acción no es válido.")
        if self.status is ActionOutcomeStatus.DELIVERED:
            if self.cause is not None:
                raise LoopContractError("Una acción entregada no admite causa.")
        elif not isinstance(self.cause, LoopCause):
            raise LoopContractError("Un fallo de acción requiere una causa.")
        elif (
            self.status is ActionOutcomeStatus.TRANSIENT_FAILURE
            and not self.cause.transient
        ):
            raise LoopContractError("El fallo no usa una causa transitoria.")
        elif (
            self.status is ActionOutcomeStatus.PERMANENT_FAILURE
            and self.cause.transient
        ):
            raise LoopContractError("El fallo permanente usa una causa transitoria.")


@dataclass(frozen=True)
class LoopEvaluation:
    kind: EvaluationKind
    state_changed: bool
    resulting_state_token: str
    cause: LoopCause | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EvaluationKind):
            raise LoopContractError("La evaluación no tiene un tipo válido.")
        if type(self.state_changed) is not bool:
            raise LoopContractError("La marca de cambio de estado no es válida.")
        _validate_id(self.resulting_state_token, "resulting_state_token")
        if self.kind is EvaluationKind.RETRY:
            if not isinstance(self.cause, LoopCause) or not self.cause.transient:
                raise LoopContractError("RETRY requiere una causa transitoria.")
        elif self.kind is EvaluationKind.AMBIGUOUS:
            if self.cause is not LoopCause.AMBIGUOUS_STATE:
                raise LoopContractError("AMBIGUOUS requiere su causa específica.")
        elif self.kind is EvaluationKind.ABANDON:
            if not isinstance(self.cause, LoopCause) or self.cause.transient:
                raise LoopContractError("ABANDON requiere una causa permanente.")
        elif self.cause is not None:
            raise LoopContractError("La evaluación no admite una causa.")
        if self.kind is EvaluationKind.PROGRESS and not self.state_changed:
            raise LoopContractError("PROGRESS requiere un estado nuevo.")


@dataclass(frozen=True)
class LoopIterationEvidence:
    iteration: int
    observation_id: str
    observed_state_token: str
    decision_id: str | None
    decision_kind: DecisionKind | None
    tool_name: str | None
    action_fingerprint: str | None
    action_status: ActionOutcomeStatus | None
    evaluation_kind: EvaluationKind | None
    cause: LoopCause | None
    state_changed: bool
    duration_ms: float


@dataclass(frozen=True)
class LoopExecution:
    task_id: str
    status: LoopState
    state_history: tuple[LoopState, ...]
    iterations: tuple[LoopIterationEvidence, ...]
    observation_count: int
    action_count: int
    duration_ms: float
    termination_reason: str


@runtime_checkable
class LoopObserver(Protocol):
    def observe(self, task: LoopTask) -> LoopObservation: ...


@runtime_checkable
class LoopDecisionMaker(Protocol):
    def decide(
        self,
        task: LoopTask,
        observation: LoopObservation,
    ) -> LoopDecision: ...


@runtime_checkable
class LoopActor(Protocol):
    def act(
        self,
        task: LoopTask,
        decision: LoopDecision,
    ) -> LoopActionOutcome: ...


@runtime_checkable
class LoopEvaluator(Protocol):
    def evaluate(
        self,
        task: LoopTask,
        observation: LoopObservation,
        decision: LoopDecision,
        outcome: LoopActionOutcome,
    ) -> LoopEvaluation: ...


class LoopCancellation:
    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()


Clock = Callable[[], float]


_ALLOWED_TRANSITIONS = {
    LoopState.IDLE: {LoopState.OBSERVING, LoopState.CANCELLED},
    LoopState.OBSERVING: {
        LoopState.DECIDING,
        LoopState.FAILED,
        LoopState.CANCELLED,
        LoopState.TIMED_OUT,
    },
    LoopState.DECIDING: {
        LoopState.ACTING,
        LoopState.OBSERVING,
        LoopState.FAILED,
        LoopState.CANCELLED,
        LoopState.TIMED_OUT,
        LoopState.AMBIGUOUS,
    },
    LoopState.ACTING: {
        LoopState.EVALUATING,
        LoopState.FAILED,
        LoopState.CANCELLED,
        LoopState.TIMED_OUT,
    },
    LoopState.EVALUATING: {
        LoopState.OBSERVING,
        LoopState.SUCCEEDED,
        LoopState.FAILED,
        LoopState.CANCELLED,
        LoopState.TIMED_OUT,
        LoopState.AMBIGUOUS,
    },
}


class BoundedAgentLoop:
    """Máquina finita: no ejecuta texto y no amplía la allowlist de herramientas."""

    def __init__(
        self,
        observer: LoopObserver,
        decision_maker: LoopDecisionMaker,
        actor: LoopActor,
        evaluator: LoopEvaluator,
        logger: logging.Logger,
        limits: LoopLimits = LoopLimits(),
        clock: Clock = time.monotonic,
    ) -> None:
        ports = (observer, decision_maker, actor, evaluator)
        protocols = (LoopObserver, LoopDecisionMaker, LoopActor, LoopEvaluator)
        if any(
            not isinstance(port, protocol)
            for port, protocol in zip(ports, protocols, strict=True)
        ):
            raise TypeError("Un puerto del bucle no cumple su contrato.")
        self._observer = observer
        self._decision_maker = decision_maker
        self._actor = actor
        self._evaluator = evaluator
        self._logger = logger
        self._limits = limits
        self._clock = clock

    def run(
        self,
        task: LoopTask,
        cancellation: LoopCancellation | None = None,
    ) -> LoopExecution:
        if not isinstance(task, LoopTask):
            raise LoopContractError("La tarea no cumple el contrato.")
        cancel = cancellation or LoopCancellation()
        started_at = self._clock()
        state = LoopState.IDLE
        history = [state]
        evidence: list[LoopIterationEvidence] = []
        observations = 0
        actions = 0
        ambiguities = 0
        retries: dict[LoopCause, int] = {}
        seen_actions: set[tuple[str, str]] = set()
        expected_state_token: str | None = None

        def transition(next_state: LoopState) -> None:
            nonlocal state
            allowed = _ALLOWED_TRANSITIONS.get(state, set())
            if next_state not in allowed:
                raise RuntimeError("Transición interna de bucle inválida.")
            state = next_state
            history.append(state)
            self._logger.info(
                "Loop: task=%s state=%s observations=%d actions=%d",
                task.task_id,
                state.value.upper(),
                observations,
                actions,
            )

        def stop_if_needed() -> LoopExecution | None:
            if cancel.is_cancelled:
                transition(LoopState.CANCELLED)
                return self._finish(
                    task,
                    state,
                    history,
                    evidence,
                    observations,
                    actions,
                    started_at,
                    "cancelled",
                )
            if self._clock() - started_at >= self._limits.max_duration_seconds:
                transition(LoopState.TIMED_OUT)
                return self._finish(
                    task,
                    state,
                    history,
                    evidence,
                    observations,
                    actions,
                    started_at,
                    "time_limit",
                )
            return None

        transition(LoopState.OBSERVING)
        while not state.terminal:
            stopped = stop_if_needed()
            if stopped is not None:
                return stopped
            if observations >= self._limits.max_observations:
                transition(LoopState.FAILED)
                return self._finish(
                    task,
                    state,
                    history,
                    evidence,
                    observations,
                    actions,
                    started_at,
                    "observation_limit",
                )

            iteration_started = self._clock()
            try:
                observation = self._observer.observe(task)
            except Exception:
                transition(LoopState.FAILED)
                return self._finish(
                    task,
                    state,
                    history,
                    evidence,
                    observations,
                    actions,
                    started_at,
                    "observation_failure",
                )
            if not isinstance(observation, LoopObservation):
                transition(LoopState.FAILED)
                return self._finish(
                    task,
                    state,
                    history,
                    evidence,
                    observations,
                    actions,
                    started_at,
                    "invalid_observation",
                )
            if (
                expected_state_token is not None
                and observation.state_token != expected_state_token
            ):
                evidence.append(
                    self._evidence(
                        len(evidence) + 1,
                        observation,
                        None,
                        None,
                        None,
                        LoopCause.VALIDATION_FAILURE,
                        False,
                        iteration_started,
                    )
                )
                transition(LoopState.FAILED)
                return self._finish(
                    task,
                    state,
                    history,
                    evidence,
                    observations,
                    actions,
                    started_at,
                    "state_evidence_mismatch",
                )
            observations += 1
            stopped = stop_if_needed()
            if stopped is not None:
                return stopped

            transition(LoopState.DECIDING)
            try:
                decision = self._decision_maker.decide(task, observation)
            except Exception:
                evidence.append(
                    self._evidence(
                        len(evidence) + 1,
                        observation,
                        None,
                        None,
                        None,
                        LoopCause.DECISION_FAILURE,
                        False,
                        iteration_started,
                    )
                )
                transition(LoopState.FAILED)
                return self._finish(
                    task,
                    state,
                    history,
                    evidence,
                    observations,
                    actions,
                    started_at,
                    "decision_failure",
                )
            if (
                not isinstance(decision, LoopDecision)
                or decision.observed_state_token != observation.state_token
            ):
                evidence.append(
                    self._evidence(
                        len(evidence) + 1,
                        observation,
                        decision if isinstance(decision, LoopDecision) else None,
                        None,
                        None,
                        LoopCause.VALIDATION_FAILURE,
                        False,
                        iteration_started,
                    )
                )
                transition(LoopState.FAILED)
                return self._finish(
                    task,
                    state,
                    history,
                    evidence,
                    observations,
                    actions,
                    started_at,
                    "invalid_decision",
                )
            stopped = stop_if_needed()
            if stopped is not None:
                return stopped

            if decision.kind is DecisionKind.ABANDON:
                evidence.append(
                    self._evidence(
                        len(evidence) + 1,
                        observation,
                        decision,
                        None,
                        None,
                        LoopCause.DECISION_FAILURE,
                        False,
                        iteration_started,
                    )
                )
                transition(LoopState.FAILED)
                return self._finish(
                    task,
                    state,
                    history,
                    evidence,
                    observations,
                    actions,
                    started_at,
                    "decision_abandoned",
                )
            if decision.kind is DecisionKind.AMBIGUOUS:
                ambiguities += 1
                evidence.append(
                    self._evidence(
                        len(evidence) + 1,
                        observation,
                        decision,
                        None,
                        None,
                        LoopCause.AMBIGUOUS_STATE,
                        False,
                        iteration_started,
                    )
                )
                if ambiguities >= self._limits.max_ambiguities:
                    transition(LoopState.AMBIGUOUS)
                    return self._finish(
                        task,
                        state,
                        history,
                        evidence,
                        observations,
                        actions,
                        started_at,
                        "persistent_ambiguity",
                    )
                expected_state_token = observation.state_token
                transition(LoopState.OBSERVING)
                continue

            assert decision.tool_name is not None
            assert decision.action_fingerprint is not None
            if decision.tool_name not in task.allowed_tools:
                evidence.append(
                    self._evidence(
                        len(evidence) + 1,
                        observation,
                        decision,
                        None,
                        None,
                        LoopCause.POLICY_BLOCK,
                        False,
                        iteration_started,
                    )
                )
                transition(LoopState.FAILED)
                return self._finish(
                    task,
                    state,
                    history,
                    evidence,
                    observations,
                    actions,
                    started_at,
                    "tool_not_allowed",
                )
            action_key = (
                observation.state_token,
                decision.action_fingerprint,
            )
            if action_key in seen_actions:
                evidence.append(
                    self._evidence(
                        len(evidence) + 1,
                        observation,
                        decision,
                        None,
                        None,
                        LoopCause.AMBIGUOUS_STATE,
                        False,
                        iteration_started,
                    )
                )
                transition(LoopState.AMBIGUOUS)
                return self._finish(
                    task,
                    state,
                    history,
                    evidence,
                    observations,
                    actions,
                    started_at,
                    "repeated_action_without_new_state",
                )
            if actions >= self._limits.max_actions:
                evidence.append(
                    self._evidence(
                        len(evidence) + 1,
                        observation,
                        decision,
                        None,
                        None,
                        LoopCause.ACTION_FAILURE,
                        False,
                        iteration_started,
                    )
                )
                transition(LoopState.FAILED)
                return self._finish(
                    task,
                    state,
                    history,
                    evidence,
                    observations,
                    actions,
                    started_at,
                    "action_limit",
                )
            seen_actions.add(action_key)
            transition(LoopState.ACTING)
            try:
                outcome = self._actor.act(task, decision)
            except Exception:
                evidence.append(
                    self._evidence(
                        len(evidence) + 1,
                        observation,
                        decision,
                        None,
                        None,
                        LoopCause.ACTION_FAILURE,
                        False,
                        iteration_started,
                    )
                )
                transition(LoopState.FAILED)
                return self._finish(
                    task,
                    state,
                    history,
                    evidence,
                    observations,
                    actions,
                    started_at,
                    "action_failure",
                )
            if not isinstance(outcome, LoopActionOutcome):
                evidence.append(
                    self._evidence(
                        len(evidence) + 1,
                        observation,
                        decision,
                        None,
                        None,
                        LoopCause.ACTION_FAILURE,
                        False,
                        iteration_started,
                    )
                )
                transition(LoopState.FAILED)
                return self._finish(
                    task,
                    state,
                    history,
                    evidence,
                    observations,
                    actions,
                    started_at,
                    "invalid_action_outcome",
                )
            actions += 1
            stopped = stop_if_needed()
            if stopped is not None:
                return stopped

            transition(LoopState.EVALUATING)
            try:
                evaluation = self._evaluator.evaluate(
                    task, observation, decision, outcome
                )
            except Exception:
                evidence.append(
                    self._evidence(
                        len(evidence) + 1,
                        observation,
                        decision,
                        outcome,
                        None,
                        LoopCause.EVALUATION_FAILURE,
                        False,
                        iteration_started,
                    )
                )
                transition(LoopState.FAILED)
                return self._finish(
                    task,
                    state,
                    history,
                    evidence,
                    observations,
                    actions,
                    started_at,
                    "evaluation_failure",
                )
            if not isinstance(evaluation, LoopEvaluation):
                evidence.append(
                    self._evidence(
                        len(evidence) + 1,
                        observation,
                        decision,
                        outcome,
                        None,
                        LoopCause.EVALUATION_FAILURE,
                        False,
                        iteration_started,
                    )
                )
                transition(LoopState.FAILED)
                return self._finish(
                    task,
                    state,
                    history,
                    evidence,
                    observations,
                    actions,
                    started_at,
                    "invalid_evaluation",
                )
            token_changed = (
                evaluation.resulting_state_token != observation.state_token
            )
            if token_changed is not evaluation.state_changed:
                evidence.append(
                    self._evidence(
                        len(evidence) + 1,
                        observation,
                        decision,
                        outcome,
                        evaluation,
                        LoopCause.VALIDATION_FAILURE,
                        evaluation.state_changed,
                        iteration_started,
                    )
                )
                transition(LoopState.FAILED)
                return self._finish(
                    task,
                    state,
                    history,
                    evidence,
                    observations,
                    actions,
                    started_at,
                    "incoherent_state_evidence",
                )
            evidence.append(
                self._evidence(
                    len(evidence) + 1,
                    observation,
                    decision,
                    outcome,
                    evaluation,
                    evaluation.cause or outcome.cause,
                    evaluation.state_changed,
                    iteration_started,
                )
            )
            stopped = stop_if_needed()
            if stopped is not None:
                return stopped

            if evaluation.kind is EvaluationKind.SUCCESS:
                transition(LoopState.SUCCEEDED)
                return self._finish(
                    task,
                    state,
                    history,
                    evidence,
                    observations,
                    actions,
                    started_at,
                    "success_condition",
                )
            if evaluation.kind is EvaluationKind.ABANDON:
                transition(LoopState.FAILED)
                return self._finish(
                    task,
                    state,
                    history,
                    evidence,
                    observations,
                    actions,
                    started_at,
                    "evaluation_abandoned",
                )
            if evaluation.kind is EvaluationKind.AMBIGUOUS:
                ambiguities += 1
                if ambiguities >= self._limits.max_ambiguities:
                    transition(LoopState.AMBIGUOUS)
                    return self._finish(
                        task,
                        state,
                        history,
                        evidence,
                        observations,
                        actions,
                        started_at,
                        "persistent_ambiguity",
                    )
                expected_state_token = evaluation.resulting_state_token
                transition(LoopState.OBSERVING)
                continue
            if evaluation.kind is EvaluationKind.RETRY:
                assert evaluation.cause is not None
                retries[evaluation.cause] = retries.get(evaluation.cause, 0) + 1
                if retries[evaluation.cause] > self._limits.max_retries_per_cause:
                    transition(LoopState.FAILED)
                    return self._finish(
                        task,
                        state,
                        history,
                        evidence,
                        observations,
                        actions,
                        started_at,
                        "retry_limit",
                    )
            elif evaluation.kind is not EvaluationKind.PROGRESS:
                transition(LoopState.FAILED)
                return self._finish(
                    task,
                    state,
                    history,
                    evidence,
                    observations,
                    actions,
                    started_at,
                    "invalid_continuation",
                )
            expected_state_token = evaluation.resulting_state_token
            transition(LoopState.OBSERVING)

        raise RuntimeError("El bucle terminó sin estado terminal.")

    def _evidence(
        self,
        iteration: int,
        observation: LoopObservation,
        decision: LoopDecision | None,
        outcome: LoopActionOutcome | None,
        evaluation: LoopEvaluation | None,
        cause: LoopCause | None,
        state_changed: bool,
        started_at: float,
    ) -> LoopIterationEvidence:
        return LoopIterationEvidence(
            iteration=iteration,
            observation_id=observation.observation_id,
            observed_state_token=observation.state_token,
            decision_id=decision.decision_id if decision else None,
            decision_kind=decision.kind if decision else None,
            tool_name=decision.tool_name if decision else None,
            action_fingerprint=(
                decision.action_fingerprint if decision else None
            ),
            action_status=outcome.status if outcome else None,
            evaluation_kind=evaluation.kind if evaluation else None,
            cause=cause,
            state_changed=state_changed,
            duration_ms=max(0.0, (self._clock() - started_at) * 1000),
        )

    def _finish(
        self,
        task: LoopTask,
        status: LoopState,
        history: list[LoopState],
        evidence: list[LoopIterationEvidence],
        observations: int,
        actions: int,
        started_at: float,
        reason: str,
    ) -> LoopExecution:
        duration_ms = max(0.0, (self._clock() - started_at) * 1000)
        self._logger.info(
            "Loop: task=%s status=%s reason=%s observations=%d "
            "actions=%d duration_ms=%.3f",
            task.task_id,
            status.value.upper(),
            reason,
            observations,
            actions,
            duration_ms,
        )
        return LoopExecution(
            task_id=task.task_id,
            status=status,
            state_history=tuple(history),
            iterations=tuple(evidence),
            observation_count=observations,
            action_count=actions,
            duration_ms=duration_ms,
            termination_reason=reason,
        )
