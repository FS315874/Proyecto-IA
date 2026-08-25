import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol, runtime_checkable

from desktop_agent.command_processor import CommandStage
from desktop_agent.executor import ActionExecutionError, ActionExecutor
from desktop_agent.local_controller import (
    ControllerCancelStatus,
    ControllerSnapshot,
    ControllerState,
    ControllerUpdate,
    ControllerUpdateKind,
    LocalAgentController,
    LocalControllerError,
)
from desktop_agent.models import ToolResult
from desktop_agent.permissions import (
    AuthorizationResult,
    AuthorizationStatus,
    ConfirmationChannel,
    ConfirmationDecision,
    ConfirmationRequest,
    PermissionBroker,
    PermissionPreparation,
    PolicySubject,
    PreparationStatus,
)
from desktop_agent.windows_session import SessionAvailability


MAX_HISTORY_ITEMS = 100
MAX_HISTORY_LABEL_LENGTH = 120


class ServiceError(RuntimeError):
    """La interfaz local solicitó una transición no permitida."""


class ServiceState(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSING = "closing"
    CLOSED = "closed"


class TaskKind(str, Enum):
    COMMAND = "command"
    VOICE_COMMAND = "voice_command"
    POLICY_ACTION = "policy_action"


class TaskState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {
            TaskState.SUCCEEDED,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }


@dataclass(frozen=True)
class ServiceEvidence:
    task_id: str
    status: TaskState
    tool_name: str | None
    observable_success: bool
    queue_ms: float | None
    total_ms: float | None
    reason: str


@dataclass(frozen=True)
class TaskHistoryEntry:
    task_id: str
    kind: TaskKind
    label: str
    state: TaskState
    stage: CommandStage | None = None
    active_tool: str | None = None
    result: str | None = None
    evidence: ServiceEvidence | None = None


@dataclass(frozen=True)
class PendingServiceConfirmation:
    task_id: str
    subject: PolicySubject
    request: ConfirmationRequest


@dataclass(frozen=True)
class ServiceSnapshot:
    state: ServiceState
    controller: ControllerSnapshot
    history: tuple[TaskHistoryEntry, ...]
    active_tool: str | None
    pending_confirmation: PendingServiceConfirmation | None
    suspension_reason: str | None


@dataclass(frozen=True)
class PolicyActionExecution:
    authorization: AuthorizationResult
    tool_result: ToolResult | None
    error: str | None


@runtime_checkable
class SessionMonitor(Protocol):
    def current(self) -> SessionAvailability: ...


class PolicyActionCoordinator:
    """Une broker y ejecutor sin entregar tokens a la capa de interfaz."""

    def __init__(
        self,
        broker: PermissionBroker,
        executor: ActionExecutor,
    ) -> None:
        if not isinstance(broker, PermissionBroker):
            raise TypeError("El coordinador requiere un broker de permisos.")
        if not isinstance(executor, ActionExecutor):
            raise TypeError("El coordinador requiere un ejecutor de acciones.")
        self._broker = broker
        self._executor = executor

    def prepare(
        self,
        subject: PolicySubject,
        channels: tuple[ConfirmationChannel, ...],
    ) -> PermissionPreparation:
        return self._broker.prepare(subject, channels)

    def execute_safe(self, subject: PolicySubject) -> PolicyActionExecution:
        try:
            result = self._executor.execute(subject.action)
        except ActionExecutionError:
            return PolicyActionExecution(
                AuthorizationResult(
                    AuthorizationStatus.INVALID,
                    None,
                    "safe_execution_failed",
                ),
                None,
                "La acción segura no pudo completarse.",
            )
        return PolicyActionExecution(
            AuthorizationResult(
                AuthorizationStatus.AUTHORIZED,
                None,
                "confirmation_not_required",
            ),
            result,
            None,
        )

    def resolve(
        self,
        subject: PolicySubject,
        decision: ConfirmationDecision,
    ) -> PolicyActionExecution:
        authorization = self._broker.authorize(subject, decision)
        if (
            authorization.status is not AuthorizationStatus.AUTHORIZED
            or authorization.token is None
        ):
            return PolicyActionExecution(authorization, None, None)
        try:
            result = self._executor.execute(
                subject.action,
                authorization.token,
            )
        except ActionExecutionError:
            return PolicyActionExecution(
                authorization,
                None,
                "La acción confirmada no pudo completarse.",
            )
        return PolicyActionExecution(authorization, result, None)

    def cancel(self, request_id: str) -> AuthorizationStatus:
        return self._broker.cancel(request_id)


Clock = Callable[[], float]


class DesktopAgentService:
    """Fachada local transport-neutral para GUI y futuro adaptador remoto."""

    def __init__(
        self,
        controller: LocalAgentController,
        logger: logging.Logger,
        policy_coordinator: PolicyActionCoordinator | None = None,
        session_monitor: SessionMonitor | None = None,
        history_limit: int = MAX_HISTORY_ITEMS,
        session_check_interval_seconds: float = 1.0,
        clock: Clock = time.monotonic,
    ) -> None:
        if not isinstance(controller, LocalAgentController):
            raise TypeError("El servicio requiere un controlador local.")
        if policy_coordinator is not None and not isinstance(
            policy_coordinator,
            PolicyActionCoordinator,
        ):
            raise TypeError("El coordinador de permisos no es válido.")
        if session_monitor is not None and not isinstance(
            session_monitor,
            SessionMonitor,
        ):
            raise TypeError("El monitor de sesión no es válido.")
        if type(history_limit) is not int or not 1 <= history_limit <= 1000:
            raise ValueError("El límite de historial no es válido.")
        interval = session_check_interval_seconds
        if type(interval) not in (int, float) or not 0.1 <= float(interval) <= 60:
            raise ValueError("El intervalo de sesión no es válido.")
        self._controller = controller
        self._logger = logger
        self._policy = policy_coordinator
        self._session_monitor = session_monitor
        self._history_limit = history_limit
        self._session_interval = float(interval)
        self._clock = clock
        self._state = ServiceState.ACTIVE
        self._history: list[TaskHistoryEntry] = []
        self._active_tool: str | None = None
        self._pending_confirmation: PendingServiceConfirmation | None = None
        self._suspension_reason: str | None = None
        self._next_session_check = self._clock()
        self._lock = threading.RLock()

    @property
    def snapshot(self) -> ServiceSnapshot:
        with self._lock:
            return ServiceSnapshot(
                self._state,
                self._controller.snapshot,
                tuple(self._history),
                self._active_tool,
                self._pending_confirmation,
                self._suspension_reason,
            )

    def submit(self, command: str) -> str:
        with self._lock:
            self._require_active()
            self._reserve_history_slot()
            try:
                request_id = self._controller.submit(command)
            except LocalControllerError as error:
                raise ServiceError(str(error)) from error
            self._ensure_new_task_id(request_id)
            label = " ".join(command.strip().split())
            if len(label) > MAX_HISTORY_LABEL_LENGTH:
                label = f"{label[: MAX_HISTORY_LABEL_LENGTH - 1]}…"
            self._history.append(
                TaskHistoryEntry(
                    request_id,
                    TaskKind.COMMAND,
                    label,
                    TaskState.QUEUED,
                )
            )
            self._logger.info(
                "Local service: task=%s kind=command status=QUEUED",
                request_id,
            )
            return request_id

    def submit_voice_transcript(self, transcript: str) -> str:
        with self._lock:
            if self._pending_confirmation is not None:
                raise ServiceError(
                    "La voz no puede resolver una confirmación pendiente."
                )
            request_id = self.submit(transcript)
            self._replace_task(request_id, kind=TaskKind.VOICE_COMMAND)
            self._logger.info(
                "Local service: task=%s origin=voice status=QUEUED",
                request_id,
            )
            return request_id

    def submit_policy_action(
        self,
        subject: PolicySubject,
        channels: tuple[ConfirmationChannel, ...] = (
            ConfirmationChannel.LOCAL,
        ),
    ) -> str:
        with self._lock:
            self._require_active()
            if self._policy is None:
                raise ServiceError("El servicio no tiene política de acciones.")
            if self._pending_confirmation is not None:
                raise ServiceError("Ya existe una confirmación pendiente.")
            if not subject.action_id.startswith("policy-"):
                raise ServiceError(
                    "Las acciones de política requieren un ID con prefijo policy-."
                )
            self._reserve_history_slot()
            self._ensure_new_task_id(subject.action_id)
            preparation = self._policy.prepare(subject, channels)
            entry = TaskHistoryEntry(
                subject.action_id,
                TaskKind.POLICY_ACTION,
                f"Acción: {subject.capability_id}",
                TaskState.RUNNING,
                active_tool=subject.action.tool_name,
            )
            self._history.append(entry)
            if preparation.status is PreparationStatus.BLOCKED:
                self._finish_policy_task(
                    subject.action_id,
                    TaskState.FAILED,
                    "Acción bloqueada por política.",
                    "policy_block",
                    False,
                )
            elif preparation.status is PreparationStatus.NOT_REQUIRED:
                self._apply_policy_execution(
                    subject.action_id,
                    self._policy.execute_safe(subject),
                )
            else:
                request = preparation.request
                assert request is not None
                self._pending_confirmation = PendingServiceConfirmation(
                    subject.action_id,
                    subject,
                    request,
                )
                self._replace_task(
                    subject.action_id,
                    state=TaskState.AWAITING_CONFIRMATION,
                    result="Confirmación inmediata requerida.",
                )
            return subject.action_id

    def resolve_confirmation(
        self,
        task_id: str,
        decision: ConfirmationDecision,
    ) -> PolicyActionExecution:
        with self._lock:
            self._require_active()
            pending = self._pending_confirmation
            if pending is None or pending.task_id != task_id:
                raise ServiceError("La confirmación ya no está pendiente.")
            assert self._policy is not None
            execution = self._policy.resolve(pending.subject, decision)
            self._pending_confirmation = None
            self._apply_policy_execution(task_id, execution)
            return execution

    def cancel_task(self, task_id: str) -> bool:
        with self._lock:
            pending = self._pending_confirmation
            if pending is not None and pending.task_id == task_id:
                assert self._policy is not None
                self._policy.cancel(pending.request.request_id)
                self._pending_confirmation = None
                self._finish_policy_task(
                    task_id,
                    TaskState.CANCELLED,
                    "Confirmación cancelada.",
                    "cancelled",
                    False,
                )
                return True
            status = self._controller.cancel(task_id)
            if status is ControllerCancelStatus.CANCELLED:
                return True
            if status is ControllerCancelStatus.ACTIVE_NOT_INTERRUPTIBLE:
                raise ServiceError(
                    "La fase activa solo puede detenerse en su próximo límite seguro."
                )
            return False

    def request_stop(self, emergency: bool = False) -> None:
        with self._lock:
            if self._state in {ServiceState.CLOSING, ServiceState.CLOSED}:
                return
            if emergency:
                self._cancel_pending_confirmation("emergency_stop")
            self._controller.request_stop(emergency=emergency)

    def suspend(self, reason: str = "session_unavailable") -> None:
        with self._lock:
            if self._state is not ServiceState.ACTIVE:
                return
            self._state = ServiceState.SUSPENDED
            self._suspension_reason = reason
            self._cancel_pending_confirmation(reason)
            self._controller.request_stop(emergency=True)
            self._logger.info(
                "Local service: status=SUSPENDED reason=%s",
                reason,
            )

    def resume(self) -> None:
        with self._lock:
            if self._state is not ServiceState.SUSPENDED:
                raise ServiceError("El servicio no está suspendido.")
            if self._session_monitor is not None:
                availability = self._session_monitor.current()
                if availability is not SessionAvailability.AVAILABLE:
                    raise ServiceError(
                        "La sesión interactiva todavía no está disponible."
                    )
            self._state = ServiceState.ACTIVE
            self._suspension_reason = None
            self._next_session_check = self._clock() + self._session_interval
            self._logger.info("Local service: status=ACTIVE reason=manual_resume")

    def request_close(self) -> None:
        with self._lock:
            if self._state in {ServiceState.CLOSING, ServiceState.CLOSED}:
                return
            self._state = ServiceState.CLOSING
            self._cancel_pending_confirmation("service_closing")
            self._controller.request_close()
            self._logger.info("Local service: status=CLOSING")

    def close(self, timeout: float = 10.0) -> bool:
        self.request_close()
        closed = self._controller.close(timeout)
        with self._lock:
            if closed:
                self._state = ServiceState.CLOSED
                self._active_tool = None
        return closed

    def poll(self) -> tuple[ControllerUpdate, ...]:
        self._check_session()
        updates = self._controller.drain_updates()
        with self._lock:
            for update in updates:
                self._apply_controller_update(update)
            if self._controller.snapshot.state is ControllerState.CLOSED:
                self._state = ServiceState.CLOSED
                self._active_tool = None
        return updates

    def _check_session(self) -> None:
        monitor = self._session_monitor
        if monitor is None:
            return
        now = self._clock()
        with self._lock:
            if self._state is not ServiceState.ACTIVE:
                return
            if now < self._next_session_check:
                return
            self._next_session_check = now + self._session_interval
        availability = monitor.current()
        if availability is not SessionAvailability.AVAILABLE:
            self.suspend(f"session_{availability.value}")

    def _apply_controller_update(self, update: ControllerUpdate) -> None:
        task_id = update.request_id
        if update.kind is ControllerUpdateKind.PROGRESS and task_id is not None:
            self._active_tool = update.tool_name
            self._replace_task(
                task_id,
                state=TaskState.RUNNING,
                stage=update.stage,
                active_tool=update.tool_name,
            )
        elif update.kind is ControllerUpdateKind.RESULT and task_id is not None:
            execution = update.execution
            assert execution is not None
            state = TaskState.SUCCEEDED if execution.success else TaskState.FAILED
            result = execution.messages[-1] if execution.messages else "Sin detalle."
            evidence = ServiceEvidence(
                task_id,
                state,
                execution.tool_name,
                execution.success,
                update.queue_ms,
                update.total_ms,
                "observable_result" if execution.success else "execution_failed",
            )
            self._replace_task(
                task_id,
                state=state,
                stage=CommandStage.FINISHED,
                active_tool=None,
                result=result,
                evidence=evidence,
            )
            self._active_tool = None
        elif update.kind is ControllerUpdateKind.CANCELLED and task_id is not None:
            self._replace_task(
                task_id,
                state=TaskState.CANCELLED,
                active_tool=None,
                result=update.message or "Orden cancelada.",
                evidence=ServiceEvidence(
                    task_id,
                    TaskState.CANCELLED,
                    None,
                    False,
                    update.queue_ms,
                    update.total_ms,
                    "cancelled",
                ),
            )
        elif update.kind is ControllerUpdateKind.ERROR and task_id is not None:
            self._replace_task(
                task_id,
                state=TaskState.FAILED,
                active_tool=None,
                result=update.message or "Error interno redactado.",
                evidence=ServiceEvidence(
                    task_id,
                    TaskState.FAILED,
                    None,
                    False,
                    update.queue_ms,
                    update.total_ms,
                    "internal_error",
                ),
            )
            self._active_tool = None
        elif update.kind is ControllerUpdateKind.STATE:
            if update.state is not ControllerState.RUNNING:
                self._active_tool = None

    def _apply_policy_execution(
        self,
        task_id: str,
        execution: PolicyActionExecution,
    ) -> None:
        authorization = execution.authorization
        if execution.tool_result is not None and execution.tool_result.success:
            state = TaskState.SUCCEEDED
            result = execution.tool_result.message
            reason = "observable_result"
            success = True
        elif authorization.status in {
            AuthorizationStatus.REJECTED,
            AuthorizationStatus.CANCELLED,
        }:
            state = TaskState.CANCELLED
            result = "Acción no autorizada."
            reason = authorization.reason
            success = False
        else:
            state = TaskState.FAILED
            result = execution.error or "La acción no pudo completarse."
            reason = authorization.reason
            success = False
        self._finish_policy_task(task_id, state, result, reason, success)

    def _finish_policy_task(
        self,
        task_id: str,
        state: TaskState,
        result: str,
        reason: str,
        success: bool,
    ) -> None:
        entry = self._task(task_id)
        self._replace_task(
            task_id,
            state=state,
            active_tool=None,
            result=result,
            evidence=ServiceEvidence(
                task_id,
                state,
                entry.active_tool,
                success,
                None,
                None,
                reason,
            ),
        )
        self._active_tool = None
        self._logger.info(
            "Local service: task=%s kind=policy status=%s reason=%s",
            task_id,
            state.value.upper(),
            reason,
        )

    def _cancel_pending_confirmation(self, reason: str) -> None:
        pending = self._pending_confirmation
        if pending is None:
            return
        assert self._policy is not None
        self._policy.cancel(pending.request.request_id)
        self._pending_confirmation = None
        self._finish_policy_task(
            pending.task_id,
            TaskState.CANCELLED,
            "Confirmación cancelada.",
            reason,
            False,
        )

    def _reserve_history_slot(self) -> None:
        if len(self._history) < self._history_limit:
            return
        for index, entry in enumerate(self._history):
            if entry.state.terminal:
                del self._history[index]
                return
        raise ServiceError("El historial está ocupado por tareas activas.")

    def _ensure_new_task_id(self, task_id: str) -> None:
        if any(entry.task_id == task_id for entry in self._history):
            raise ServiceError("El identificador de tarea ya existe.")

    def _task(self, task_id: str) -> TaskHistoryEntry:
        matches = [entry for entry in self._history if entry.task_id == task_id]
        if len(matches) != 1:
            raise ServiceError("La tarea no existe o no es única.")
        return matches[0]

    def _replace_task(self, task_id: str, **changes: object) -> None:
        for index, entry in enumerate(self._history):
            if entry.task_id == task_id:
                self._history[index] = replace(entry, **changes)
                return
        raise ServiceError("La actualización refiere a una tarea desconocida.")

    def _require_active(self) -> None:
        if self._state is ServiceState.SUSPENDED:
            raise ServiceError("El servicio está suspendido.")
        if self._state in {ServiceState.CLOSING, ServiceState.CLOSED}:
            raise ServiceError("El servicio está cerrado.")
