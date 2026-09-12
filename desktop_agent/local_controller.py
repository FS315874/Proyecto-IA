import logging
import os
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import BinaryIO, Protocol

from desktop_agent.command_processor import (
    CommandExecution,
    CommandProcessor,
    CommandProgress,
    CommandStage,
)
from desktop_agent.interpretation import MonthlyUsageSnapshot
from desktop_agent.models import ToolResult

MAX_LOCAL_COMMAND_LENGTH = 500


class ControllerState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPING = "stopping"
    CLOSED = "closed"


class ControllerCancelStatus(str, Enum):
    CANCELLED = "cancelled"
    ACTIVE_NOT_INTERRUPTIBLE = "active_not_interruptible"
    NOT_FOUND = "not_found"
    CLOSED = "closed"


class ControllerUpdateKind(str, Enum):
    ACKNOWLEDGED = "acknowledged"
    PROGRESS = "progress"
    RESULT = "result"
    STOP_RESULT = "stop_result"
    CANCELLED = "cancelled"
    STATE = "state"
    ERROR = "error"


@dataclass(frozen=True)
class ControllerUpdate:
    kind: ControllerUpdateKind
    state: ControllerState
    request_id: str | None = None
    stage: CommandStage | None = None
    tool_name: str | None = None
    execution: CommandExecution | None = None
    message: str | None = None
    queue_ms: float | None = None
    total_ms: float | None = None


@dataclass(frozen=True)
class ControllerSnapshot:
    state: ControllerState
    pending_commands: int
    active_request_id: str | None
    stop_requested: bool
    monthly_usage: MonthlyUsageSnapshot | None


@dataclass(frozen=True)
class _CommandRequest:
    request_id: str
    command: str
    submitted_at: float


class PlaybackController(Protocol):
    @property
    def has_active_session(self) -> bool: ...

    def stop(self) -> ToolResult: ...


class Processor(Protocol):
    def execute(
        self,
        command: str,
        output: Callable[[str], None] = print,
        progress: Callable[[CommandProgress], None] | None = None,
    ) -> CommandExecution: ...


class LocalControllerError(RuntimeError):
    """La orden local no puede aceptarse de forma segura."""


class LocalAgentController:
    """Mantiene un único núcleo y ejecuta órdenes en su hilo propietario."""

    def __init__(
        self,
        processor: CommandProcessor | Processor,
        playback_controller: PlaybackController,
        logger: logging.Logger,
        initial_usage: MonthlyUsageSnapshot | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._processor = processor
        self._playback_controller = playback_controller
        self._logger = logger
        self._clock = clock
        self._condition = threading.Condition()
        self._pending: deque[_CommandRequest] = deque()
        self._updates: queue.SimpleQueue[ControllerUpdate] = queue.SimpleQueue()
        self._state = ControllerState.IDLE
        self._active_request_id: str | None = None
        self._stop_requested = False
        self._active_cancellation = threading.Event()
        self._shutdown_requested = False
        self._monthly_usage = initial_usage
        self._next_request_number = 1
        self._thread = threading.Thread(
            target=self._run,
            name="desktop-agent-controller",
            daemon=True,
        )
        self._thread.start()

    @property
    def snapshot(self) -> ControllerSnapshot:
        with self._condition:
            return ControllerSnapshot(
                state=self._state,
                pending_commands=len(self._pending),
                active_request_id=self._active_request_id,
                stop_requested=self._stop_requested,
                monthly_usage=self._monthly_usage,
            )

    def update_monthly_usage(self, snapshot: MonthlyUsageSnapshot) -> None:
        """Sincroniza telemetría de otro adaptador que comparte el mismo ledger."""
        if not isinstance(snapshot, MonthlyUsageSnapshot):
            raise TypeError("El acumulado mensual no es válido.")
        with self._condition:
            self._monthly_usage = snapshot

    def submit(self, command: str) -> str:
        if not isinstance(command, str):
            raise LocalControllerError("La orden debe ser texto.")
        normalized = command.strip()
        if not normalized:
            raise LocalControllerError("La orden no puede estar vacía.")
        if len(normalized) > MAX_LOCAL_COMMAND_LENGTH:
            raise LocalControllerError(
                f"La orden supera {MAX_LOCAL_COMMAND_LENGTH} caracteres."
            )

        with self._condition:
            if self._shutdown_requested or self._state is ControllerState.CLOSED:
                raise LocalControllerError("El controlador está cerrado.")
            request_id = f"local-{self._next_request_number}"
            self._next_request_number += 1
            self._pending.append(
                _CommandRequest(request_id, normalized, self._clock())
            )
            state = self._state
            self._condition.notify()

        self._emit(
            ControllerUpdate(
                ControllerUpdateKind.ACKNOWLEDGED,
                state,
                request_id=request_id,
            )
        )
        return request_id

    def request_stop(self, emergency: bool = False) -> None:
        cancelled: list[_CommandRequest] = []
        with self._condition:
            if self._state is ControllerState.CLOSED:
                return
            if emergency:
                cancelled = list(self._pending)
                self._pending.clear()
            self._stop_requested = True
            self._active_cancellation.set()
            self._state = ControllerState.STOPPING
            self._condition.notify()

        for request in cancelled:
            self._emit(
                ControllerUpdate(
                    ControllerUpdateKind.CANCELLED,
                    ControllerState.STOPPING,
                    request_id=request.request_id,
                    message="Orden pendiente cancelada por detención de emergencia.",
                )
            )
        self._emit_state(ControllerState.STOPPING)

    def cancel(self, request_id: str) -> ControllerCancelStatus:
        if not isinstance(request_id, str) or not request_id:
            raise LocalControllerError("El identificador de orden no es válido.")
        cancelled: _CommandRequest | None = None
        with self._condition:
            if self._state is ControllerState.CLOSED or self._shutdown_requested:
                return ControllerCancelStatus.CLOSED
            if self._active_request_id == request_id:
                self._active_cancellation.set()
                return ControllerCancelStatus.ACTIVE_NOT_INTERRUPTIBLE
            remaining: deque[_CommandRequest] = deque()
            while self._pending:
                candidate = self._pending.popleft()
                if candidate.request_id == request_id and cancelled is None:
                    cancelled = candidate
                else:
                    remaining.append(candidate)
            self._pending = remaining
        if cancelled is None:
            return ControllerCancelStatus.NOT_FOUND
        self._emit(
            ControllerUpdate(
                ControllerUpdateKind.CANCELLED,
                self.snapshot.state,
                request_id=request_id,
                message="Orden pendiente cancelada.",
            )
        )
        return ControllerCancelStatus.CANCELLED

    def request_close(self) -> None:
        cancelled: list[_CommandRequest]
        with self._condition:
            if self._state is ControllerState.CLOSED:
                return
            cancelled = list(self._pending)
            self._pending.clear()
            self._shutdown_requested = True
            self._active_cancellation.set()
            self._stop_requested = True
            self._state = ControllerState.STOPPING
            self._condition.notify()

        for request in cancelled:
            self._emit(
                ControllerUpdate(
                    ControllerUpdateKind.CANCELLED,
                    ControllerState.STOPPING,
                    request_id=request.request_id,
                    message="Orden pendiente cancelada durante el cierre.",
                )
            )
        self._emit_state(ControllerState.STOPPING)

    def close(self, timeout: float = 10.0) -> bool:
        if type(timeout) not in (int, float) or timeout < 0:
            raise ValueError("El timeout de cierre debe ser no negativo.")
        self.request_close()
        self._thread.join(float(timeout))
        return not self._thread.is_alive()

    def drain_updates(self) -> tuple[ControllerUpdate, ...]:
        updates: list[ControllerUpdate] = []
        while True:
            try:
                updates.append(self._updates.get_nowait())
            except queue.Empty:
                return tuple(updates)

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(
                    lambda: self._shutdown_requested
                    or self._stop_requested
                    or bool(self._pending)
                )
                if self._shutdown_requested:
                    operation = "shutdown"
                    request = None
                elif self._stop_requested:
                    operation = "stop"
                    request = None
                    self._stop_requested = False
                else:
                    operation = "command"
                    request = self._pending.popleft()
                    self._active_request_id = request.request_id
                    self._active_cancellation.clear()
                    self._state = ControllerState.RUNNING

            if operation == "shutdown":
                self._stop_playback()
                with self._condition:
                    self._state = ControllerState.CLOSED
                    self._active_request_id = None
                self._emit_state(ControllerState.CLOSED)
                return

            if operation == "stop":
                result = self._stop_playback()
                with self._condition:
                    if self._shutdown_requested:
                        continue
                    self._state = ControllerState.IDLE
                self._emit(
                    ControllerUpdate(
                        ControllerUpdateKind.STOP_RESULT,
                        ControllerState.IDLE,
                        message=result.message,
                    )
                )
                self._emit_state(ControllerState.IDLE)
                continue

            assert request is not None
            started_at = self._clock()
            queue_ms = max(0.0, (started_at - request.submitted_at) * 1_000)
            self._emit_state(ControllerState.RUNNING, request.request_id)

            def progress(value: CommandProgress) -> None:
                self._emit(
                    ControllerUpdate(
                        ControllerUpdateKind.PROGRESS,
                        ControllerState.RUNNING,
                        request_id=request.request_id,
                        stage=value.stage,
                        tool_name=value.tool_name,
                    )
                )

            try:
                options = {"output": lambda _: None, "progress": progress}
                if isinstance(self._processor, CommandProcessor):
                    options["cancelled"] = self._active_cancellation.is_set
                execution = self._processor.execute(request.command, **options)
            except Exception:
                self._logger.error("Controller status: ERROR reason=internal_failure")
                total_ms = max(
                    0.0, (self._clock() - request.submitted_at) * 1_000
                )
                self._emit(
                    ControllerUpdate(
                        ControllerUpdateKind.ERROR,
                        ControllerState.RUNNING,
                        request_id=request.request_id,
                        message="El controlador encontró un error interno.",
                        queue_ms=queue_ms,
                        total_ms=total_ms,
                    )
                )
            else:
                total_ms = max(
                    0.0, (self._clock() - request.submitted_at) * 1_000
                )
                monthly = execution.interpretation.monthly_usage
                if monthly is not None:
                    with self._condition:
                        self._monthly_usage = monthly
                self._emit(
                    ControllerUpdate(
                        ControllerUpdateKind.CANCELLED if execution.cancelled else ControllerUpdateKind.RESULT,
                        ControllerState.RUNNING,
                        request_id=request.request_id,
                        execution=execution,
                        message="Orden cancelada antes de ejecutar acciones." if execution.cancelled else None,
                        queue_ms=queue_ms,
                        total_ms=total_ms,
                    )
                )

            with self._condition:
                self._active_request_id = None
                if not self._stop_requested and not self._shutdown_requested:
                    self._state = ControllerState.IDLE
                    state = ControllerState.IDLE
                else:
                    self._state = ControllerState.STOPPING
                    state = ControllerState.STOPPING
            self._emit_state(state)

    def _stop_playback(self) -> ToolResult:
        if not self._playback_controller.has_active_session:
            return ToolResult(True, "No había una reproducción activa.")
        try:
            return self._playback_controller.stop()
        except Exception:
            self._logger.error("Controller stop status: ERROR reason=internal_failure")
            return ToolResult(False, "No se pudo detener la reproducción activa.")

    def _emit_state(
        self,
        state: ControllerState,
        request_id: str | None = None,
    ) -> None:
        self._emit(
            ControllerUpdate(
                ControllerUpdateKind.STATE,
                state,
                request_id=request_id,
            )
        )

    def _emit(self, update: ControllerUpdate) -> None:
        self._updates.put(update)


class AlreadyRunningError(RuntimeError):
    """Ya existe otra consola local para el usuario actual."""


def default_instance_lock_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data and Path(local_app_data).is_absolute():
        return Path(local_app_data) / "DesktopAgent" / "desktop_agent.lock"
    return Path.home() / ".desktop_agent" / "desktop_agent.lock"


class SingleInstanceLock:
    """Lock por usuario mantenido por el sistema durante la vida del proceso."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or default_instance_lock_path()
        self._file: BinaryIO | None = None

    def acquire(self) -> None:
        if self._file is not None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        file = self._path.open("a+b")
        try:
            if file.seek(0, os.SEEK_END) == 0:
                file.write(b"\0")
                file.flush()
            file.seek(0)
            self._lock_file(file)
        except OSError as error:
            file.close()
            raise AlreadyRunningError(
                "Desktop Agent ya tiene una consola local abierta."
            ) from error
        self._file = file

    def close(self) -> None:
        file = self._file
        if file is None:
            return
        self._file = None
        try:
            file.seek(0)
            self._unlock_file(file)
        finally:
            file.close()

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    @staticmethod
    def _lock_file(file: BinaryIO) -> None:
        if os.name != "nt":
            raise OSError("El lock de instancia todavía requiere Windows.")
        import msvcrt

        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)

    @staticmethod
    def _unlock_file(file: BinaryIO) -> None:
        if os.name != "nt":
            return
        import msvcrt

        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
