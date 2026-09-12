import logging
import math
import queue
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


MAX_TRANSCRIPT_LENGTH = 500
_CULTURE = re.compile(r"^(?:[a-z]{2}-[A-Z]{2}|none)$")
_VOICE_CANCEL_COMMANDS = frozenset(
    {"cancelar agente", "detener agente", "parar agente"}
)


class VoiceError(RuntimeError):
    """La captura o transcripción no cumple el contrato local."""


class VoiceResultStatus(str, Enum):
    READY = "ready"
    AMBIGUOUS = "ambiguous"
    NO_SPEECH = "no_speech"
    CANCELLED = "cancelled"
    UNAVAILABLE = "unavailable"
    BUDGET_EXCEEDED = "budget_exceeded"
    FAILED = "failed"


class VoiceFailureReason(str, Enum):
    PROVIDER_SETUP = "provider_setup"
    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    MODEL_UNAVAILABLE = "model_unavailable"
    QUOTA_OR_RATE_LIMIT = "quota_or_rate_limit"
    REQUEST_REJECTED = "request_rejected"
    TIMEOUT = "timeout"
    NETWORK = "network"
    SERVICE_UNAVAILABLE = "service_unavailable"
    INVALID_RESPONSE = "invalid_response"
    UNKNOWN = "unknown"


class VoiceState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    READY = "ready"
    AMBIGUOUS = "ambiguous"
    ERROR = "error"
    CANCELLED = "cancelled"
    CLOSED = "closed"


@dataclass(frozen=True)
class VoiceBackendResult:
    status: VoiceResultStatus
    transcript: str | None
    confidence: float | None
    culture: str
    capture_ms: float
    transcription_ms: float
    estimated_cost_usd: float | None = None
    failure_reason: VoiceFailureReason | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, VoiceResultStatus):
            raise VoiceError("El estado de voz no es válido.")
        if self.failure_reason is not None:
            if not isinstance(self.failure_reason, VoiceFailureReason):
                raise VoiceError("La causa del fallo de voz no es válida.")
            if self.status is not VoiceResultStatus.FAILED:
                raise VoiceError(
                    "Una causa externa sólo corresponde a una transcripción fallida."
                )
        confidence = self.confidence
        if confidence is not None:
            if (
                type(confidence) not in (int, float)
                or not math.isfinite(float(confidence))
                or not 0 <= float(confidence) <= 1
            ):
                raise VoiceError("La confianza de voz no es válida.")
        if not isinstance(self.culture, str) or _CULTURE.fullmatch(
            self.culture
        ) is None:
            raise VoiceError("La cultura de voz no es válida.")
        timings = (self.capture_ms, self.transcription_ms)
        if any(
            type(value) not in (int, float)
            or not math.isfinite(float(value))
            or float(value) < 0
            for value in timings
        ):
            raise VoiceError("La latencia de voz no es válida.")
        if self.status in {
            VoiceResultStatus.READY,
            VoiceResultStatus.AMBIGUOUS,
        }:
            transcript = normalize_transcript(self.transcript)
            object.__setattr__(self, "transcript", transcript)
        elif self.transcript is not None:
            raise VoiceError("Un resultado sin voz no puede contener texto.")
        if confidence is not None:
            object.__setattr__(self, "confidence", float(confidence))
        object.__setattr__(self, "capture_ms", float(self.capture_ms))
        object.__setattr__(
            self,
            "transcription_ms",
            float(self.transcription_ms),
        )
        cost = self.estimated_cost_usd
        if cost is not None:
            if (
                type(cost) not in (int, float)
                or not math.isfinite(float(cost))
                or float(cost) < 0
            ):
                raise VoiceError("El costo de voz no es válido.")
            object.__setattr__(self, "estimated_cost_usd", float(cost))


def normalize_transcript(value: object) -> str:
    if not isinstance(value, str):
        raise VoiceError("La transcripción no es texto.")
    normalized = " ".join(value.strip().split())
    if not normalized or len(normalized) > MAX_TRANSCRIPT_LENGTH:
        raise VoiceError("La transcripción está vacía o es demasiado larga.")
    if any(ord(character) < 32 for character in normalized):
        raise VoiceError("La transcripción contiene controles no permitidos.")
    return normalized


def is_voice_cancel_command(transcript: object) -> bool:
    try:
        normalized = normalize_transcript(transcript)
    except VoiceError:
        return False
    return normalized.casefold().strip(".!?¡¿ ") in _VOICE_CANCEL_COMMANDS


@runtime_checkable
class VoiceBackend(Protocol):
    def recognize(self, cancellation: threading.Event) -> VoiceBackendResult: ...


@dataclass(frozen=True)
class VoiceUpdate:
    capture_id: str
    state: VoiceState
    result: VoiceBackendResult | None
    total_ms: float
    reason: str


Clock = Callable[[], float]


class VoiceController:
    """Activa una captura por gesto explícito y nunca escucha en background."""

    def __init__(
        self,
        backend: VoiceBackend,
        logger: logging.Logger,
        clock: Clock = time.monotonic,
    ) -> None:
        if not isinstance(backend, VoiceBackend):
            raise TypeError("El backend de voz no cumple su contrato.")
        self._backend = backend
        self._logger = logger
        self._clock = clock
        self._lock = threading.Lock()
        self._updates: queue.SimpleQueue[VoiceUpdate] = queue.SimpleQueue()
        self._state = VoiceState.IDLE
        self._capture_id: str | None = None
        self._cancellation: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._next_capture = 1

    @property
    def state(self) -> VoiceState:
        with self._lock:
            return self._state

    def start(self) -> str:
        with self._lock:
            if self._state is VoiceState.CLOSED:
                raise VoiceError("El controlador de voz está cerrado.")
            if self._thread is not None and self._thread.is_alive():
                raise VoiceError("Ya existe una captura de voz activa.")
            capture_id = f"voice-{self._next_capture}"
            self._next_capture += 1
            cancellation = threading.Event()
            self._capture_id = capture_id
            self._cancellation = cancellation
            self._state = VoiceState.LISTENING
            thread = threading.Thread(
                target=self._run,
                args=(capture_id, cancellation),
                name="desktop-agent-voice",
                daemon=True,
            )
            self._thread = thread
        self._updates.put(
            VoiceUpdate(
                capture_id,
                VoiceState.LISTENING,
                None,
                0.0,
                "push_to_talk_started",
            )
        )
        thread.start()
        self._logger.info(
            "Voice: capture=%s state=LISTENING trigger=explicit",
            capture_id,
        )
        return capture_id

    def cancel(self) -> bool:
        with self._lock:
            cancellation = self._cancellation
            thread = self._thread
            if cancellation is None or thread is None or not thread.is_alive():
                return False
            cancellation.set()
            return True

    def close(self, timeout: float = 2.0) -> bool:
        if type(timeout) not in (int, float) or float(timeout) < 0:
            raise ValueError("El timeout de voz no es válido.")
        self.cancel()
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(float(timeout))
        with self._lock:
            closed = thread is None or not thread.is_alive()
            if closed:
                self._state = VoiceState.CLOSED
                self._capture_id = None
                self._cancellation = None
        return closed

    def drain_updates(self) -> tuple[VoiceUpdate, ...]:
        updates: list[VoiceUpdate] = []
        while True:
            try:
                updates.append(self._updates.get_nowait())
            except queue.Empty:
                return tuple(updates)

    def _run(
        self,
        capture_id: str,
        cancellation: threading.Event,
    ) -> None:
        started = self._clock()
        try:
            result = self._backend.recognize(cancellation)
            if not isinstance(result, VoiceBackendResult):
                raise VoiceError("El backend devolvió un resultado inválido.")
        except Exception:
            result = VoiceBackendResult(
                VoiceResultStatus.FAILED,
                None,
                None,
                "none",
                0,
                0,
            )
        if cancellation.is_set() and result.status is not VoiceResultStatus.CANCELLED:
            result = VoiceBackendResult(
                VoiceResultStatus.CANCELLED,
                None,
                0,
                result.culture,
                result.capture_ms,
                result.transcription_ms,
                result.estimated_cost_usd,
            )
        state = {
            VoiceResultStatus.READY: VoiceState.READY,
            VoiceResultStatus.AMBIGUOUS: VoiceState.AMBIGUOUS,
            VoiceResultStatus.CANCELLED: VoiceState.CANCELLED,
            VoiceResultStatus.NO_SPEECH: VoiceState.ERROR,
            VoiceResultStatus.UNAVAILABLE: VoiceState.ERROR,
            VoiceResultStatus.BUDGET_EXCEEDED: VoiceState.ERROR,
            VoiceResultStatus.FAILED: VoiceState.ERROR,
        }[result.status]
        total_ms = max(0.0, (self._clock() - started) * 1000)
        with self._lock:
            if self._capture_id == capture_id:
                self._state = state
                self._cancellation = None
        update = VoiceUpdate(
            capture_id,
            state,
            result,
            total_ms,
            result.status.value,
        )
        self._updates.put(update)
        self._logger.info(
            "Voice: capture=%s state=%s status=%s culture=%s "
            "confidence=%s capture_ms=%.3f transcription_ms=%.3f "
            "total_ms=%.3f estimated_cost_usd=%s",
            capture_id,
            state.value.upper(),
            result.status.value,
            result.culture,
            (
                f"{result.confidence:.3f}"
                if result.confidence is not None
                else "none"
            ),
            result.capture_ms,
            result.transcription_ms,
            total_ms,
            (
                f"{result.estimated_cost_usd:.9f}"
                if result.estimated_cost_usd is not None
                else "none"
            ),
        )
