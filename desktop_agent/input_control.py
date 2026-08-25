import logging
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from desktop_agent.models import RiskLevel
from desktop_agent.observation import (
    CaptureRegion,
    Observation,
    ObservationError,
    ObservationService,
    WindowTarget,
)
from desktop_agent.vision import (
    VisualElement,
    VisualInterpretation,
    VisualRole,
    VisualStatus,
)

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")


class InputControlError(RuntimeError):
    """La acción de entrada no cumple el contrato o el estado requerido."""


class InputKind(str, Enum):
    CLICK_ELEMENT = "click_element"
    TYPE_TEXT = "type_text"


class InputMethod(str, Enum):
    ACCESSIBILITY = "accessibility"
    COORDINATE_FALLBACK = "coordinate_fallback"


class InputStatus(str, Enum):
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass(frozen=True)
class InputLimits:
    max_actions: int = 5
    max_text_characters: int = 200
    confirmation_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if type(self.max_actions) is not int or not 1 <= self.max_actions <= 20:
            raise ValueError("El máximo de inputs debe estar entre 1 y 20.")
        if (
            type(self.max_text_characters) is not int
            or not 1 <= self.max_text_characters <= 1000
        ):
            raise ValueError("El máximo de texto debe estar entre 1 y 1000.")
        timeout = self.confirmation_timeout_seconds
        if type(timeout) not in (int, float) or not 1 <= float(timeout) <= 300:
            raise ValueError("El timeout de confirmación debe estar entre 1 y 300.")
        object.__setattr__(
            self, "confirmation_timeout_seconds", float(timeout)
        )


@dataclass(frozen=True)
class InputAction:
    action_id: str
    kind: InputKind
    window_id: str
    window_revision: int
    observation_id: str
    element_id: str
    text: str | None = None
    allow_coordinate_fallback: bool = False
    risk_level: RiskLevel = RiskLevel.CAUTION
    requires_confirmation: bool = True

    def __post_init__(self) -> None:
        identifiers = (
            self.action_id,
            self.window_id,
            self.observation_id,
            self.element_id,
        )
        if any(
            not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value)
            for value in identifiers
        ):
            raise ValueError("Los identificadores de input no son válidos.")
        if not isinstance(self.kind, InputKind):
            raise ValueError("El tipo de input no es válido.")
        if type(self.window_revision) is not int or self.window_revision < 0:
            raise ValueError("La revisión de ventana no es válida.")
        if type(self.allow_coordinate_fallback) is not bool:
            raise ValueError("La política de fallback no es válida.")
        if self.risk_level is not RiskLevel.CAUTION:
            raise ValueError("Todo input de escritorio debe declarar CAUTION.")
        if self.requires_confirmation is not True:
            raise ValueError("Todo input de escritorio requiere confirmación.")
        if self.kind is InputKind.TYPE_TEXT:
            if not isinstance(self.text, str) or not self.text:
                raise ValueError("La escritura requiere texto.")
            if self.allow_coordinate_fallback:
                raise ValueError("La escritura no admite fallback por coordenadas.")
        elif self.text is not None:
            raise ValueError("El clic no admite texto.")


@dataclass(frozen=True)
class PreparedInput:
    action: InputAction
    challenge: str
    expires_at: float


@dataclass(frozen=True)
class InputConfirmation:
    action_id: str
    challenge: str
    approved: bool

    def __post_init__(self) -> None:
        if not self.action_id or not self.challenge:
            raise ValueError("La confirmación no es válida.")
        if type(self.approved) is not bool:
            raise ValueError("La decisión de confirmación no es válida.")


@dataclass(frozen=True)
class InputWindowState:
    exists: bool
    visible: bool
    minimized: bool
    foreground: bool
    process_id: int
    client_width: int
    client_height: int
    unknown_modal: bool = False
    blocked_context: bool = False
    focused_control_handle: int | None = None


@dataclass(frozen=True)
class AccessibleControl:
    native_handle: int
    role: VisualRole
    bounds: CaptureRegion
    enabled: bool
    focusable: bool
    is_password: bool

    def __post_init__(self) -> None:
        if type(self.native_handle) is not int or self.native_handle <= 0:
            raise ValueError("El control accesible no tiene un handle válido.")
        if not isinstance(self.role, VisualRole):
            raise ValueError("El control accesible no tiene un rol válido.")
        if not isinstance(self.bounds, CaptureRegion):
            raise ValueError("El control accesible no tiene límites válidos.")
        flags = (self.enabled, self.focusable, self.is_password)
        if any(type(value) is not bool for value in flags):
            raise ValueError("Los estados del control accesible no son válidos.")


@runtime_checkable
class InputBackend(Protocol):
    def inspect_window(self, target: WindowTarget) -> InputWindowState: ...

    def focus_window(self, target: WindowTarget) -> bool: ...

    def resolve_accessible(
        self,
        target: WindowTarget,
        element: VisualElement,
    ) -> AccessibleControl | None: ...

    def click_accessible(
        self,
        target: WindowTarget,
        control: AccessibleControl,
    ) -> bool: ...

    def focus_control(
        self,
        target: WindowTarget,
        control: AccessibleControl,
    ) -> bool: ...

    def type_text(
        self,
        target: WindowTarget,
        control: AccessibleControl,
        text: str,
    ) -> bool: ...

    def verify_control_text(
        self,
        target: WindowTarget,
        control: AccessibleControl,
        text: str,
    ) -> bool: ...

    def click_client_point(
        self,
        target: WindowTarget,
        x: int,
        y: int,
    ) -> bool: ...


class EmergencyStop:
    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def is_triggered(self) -> bool:
        return self._event.is_set()

    def trigger(self) -> None:
        self._event.set()


@dataclass(frozen=True)
class InputExecution:
    action_id: str
    status: InputStatus
    method: InputMethod | None
    message: str
    duration_ms: float
    current_target: WindowTarget | None = None
    failure_stage: str | None = None


@dataclass(frozen=True)
class _InputContext:
    target: WindowTarget
    observation: Observation
    elements: dict[str, VisualElement]


Clock = Callable[[], float]
ChallengeFactory = Callable[[], str]


def _new_challenge() -> str:
    return secrets.token_urlsafe(24)


def _valid_typing_text(text: str, maximum: int) -> bool:
    if len(text) > maximum:
        return False
    return all(character >= " " and character != "\x7f" for character in text)


class InputController:
    """Autoriza un único input contra una observación vigente y exacta."""

    def __init__(
        self,
        observation_service: ObservationService,
        backend: InputBackend,
        logger: logging.Logger,
        emergency: EmergencyStop,
        limits: InputLimits = InputLimits(),
        clock: Clock = time.monotonic,
        challenge_factory: ChallengeFactory = _new_challenge,
    ) -> None:
        if not isinstance(backend, InputBackend):
            raise TypeError("El backend de input no cumple el contrato.")
        self._observations = observation_service
        self._backend = backend
        self._logger = logger
        if not isinstance(emergency, EmergencyStop):
            raise TypeError("El canal de emergencia no es válido.")
        self._emergency = emergency
        self._limits = limits
        self._clock = clock
        self._challenge_factory = challenge_factory
        self._contexts: dict[str, _InputContext] = {}
        self._prepared: dict[str, PreparedInput] = {}
        self._action_count = 0

    def register_context(
        self,
        target: WindowTarget,
        observation: Observation,
        interpretation: VisualInterpretation,
    ) -> None:
        if interpretation.status is not VisualStatus.READY:
            raise InputControlError("La interpretación visual no está lista.")
        if (
            observation.window_id != target.window_id
            or observation.window_revision != target.revision
        ):
            raise InputControlError("La observación no coincide con la ventana.")
        try:
            self._observations.read_frame(observation.observation_id, target)
        except ObservationError as error:
            raise InputControlError(str(error)) from error
        elements = {}
        for element in interpretation.elements:
            if (
                element.observation_id != observation.observation_id
                or element.window_id != target.window_id
                or element.window_revision != target.revision
                or element.element_id in elements
            ):
                raise InputControlError("Un elemento visual no coincide con el estado.")
            elements[element.element_id] = element
        if not elements:
            raise InputControlError("No hay elementos visuales operativos.")
        self._contexts[observation.observation_id] = _InputContext(
            target, observation, elements
        )

    def prepare(self, action: InputAction) -> PreparedInput:
        context, element = self._validate_action_context(action)
        if self._action_count >= self._limits.max_actions:
            raise InputControlError("La sesión alcanzó su presupuesto de inputs.")
        if action.kind is InputKind.TYPE_TEXT:
            assert action.text is not None
            if element.role is not VisualRole.TEXT_FIELD:
                raise InputControlError("El elemento no admite escritura.")
            if not _valid_typing_text(
                action.text, self._limits.max_text_characters
            ):
                raise InputControlError("El texto supera la política de escritura.")
        elif not element.actionable:
            raise InputControlError("El elemento no admite clic.")
        challenge = self._challenge_factory()
        if not isinstance(challenge, str) or not challenge:
            raise InputControlError("No se pudo preparar la confirmación.")
        prepared = PreparedInput(
            action,
            challenge,
            self._clock() + self._limits.confirmation_timeout_seconds,
        )
        self._prepared[action.action_id] = prepared
        self._logger.info(
            "Input: action=%s kind=%s status=CONFIRMATION_PENDING window=%s",
            action.action_id,
            action.kind.value,
            context.target.window_id,
        )
        return prepared

    def execute(
        self,
        prepared: PreparedInput,
        confirmation: InputConfirmation,
    ) -> InputExecution:
        started_at = self._clock()
        action = prepared.action
        stored = self._prepared.pop(action.action_id, None)
        if stored != prepared:
            return self._finish(
                action,
                InputStatus.REJECTED,
                None,
                "La preparación no es válida o ya fue utilizada.",
                started_at,
            )
        if (
            confirmation.action_id != action.action_id
            or confirmation.challenge != prepared.challenge
            or self._clock() > prepared.expires_at
        ):
            return self._finish(
                action,
                InputStatus.REJECTED,
                None,
                "La confirmación no coincide o caducó.",
                started_at,
            )
        if not confirmation.approved:
            return self._finish(
                action,
                InputStatus.REJECTED,
                None,
                "La persona usuaria rechazó el input.",
                started_at,
            )
        stop = self._emergency
        if stop.is_triggered:
            return self._finish(
                action,
                InputStatus.CANCELLED,
                None,
                "El input fue cancelado antes de comenzar.",
                started_at,
            )

        touched_state = False
        delivered = False
        current_target: WindowTarget | None = None
        method: InputMethod | None = None
        active_stage = "context_validation"
        try:
            context, element = self._validate_action_context(action)
            if self._action_count >= self._limits.max_actions:
                raise InputControlError(
                    "La sesión alcanzó su presupuesto de inputs."
                )
            active_stage = "window_validation"
            self._require_safe_window(context.target, require_foreground=False)
            touched_state = True
            active_stage = "window_focus"
            if not self._backend.focus_window(context.target):
                raise InputControlError("No se pudo enfocar la ventana exacta.")
            self._require_safe_window(context.target, require_foreground=True)
            if stop.is_triggered:
                raise InterruptedError

            active_stage = "control_resolution"
            control = self._backend.resolve_accessible(
                context.target, element
            )
            if stop.is_triggered:
                raise InterruptedError
            active_stage = "pre_delivery_validation"
            self._require_safe_window(
                context.target, require_foreground=True
            )
            if action.kind is InputKind.TYPE_TEXT:
                if control is None:
                    raise InputControlError(
                        "No se encontró el campo accesible observado."
                    )
                if not control.enabled or not control.focusable:
                    raise InputControlError("El campo accesible no está disponible.")
                if control.is_password:
                    raise InputControlError(
                        "No se permite escribir en campos secretos."
                    )
                active_stage = "control_focus"
                if not self._backend.focus_control(context.target, control):
                    raise InputControlError("No se pudo enfocar el campo exacto.")
                focused = self._require_safe_window(
                    context.target, require_foreground=True
                )
                if focused.focused_control_handle != control.native_handle:
                    raise InputControlError("El foco cambió antes de escribir.")
                if stop.is_triggered:
                    raise InterruptedError
                assert action.text is not None
                active_stage = "text_delivery"
                delivered = self._backend.type_text(
                    context.target, control, action.text
                )
                if not delivered or not self._backend.verify_control_text(
                    context.target, control, action.text
                ):
                    raise InputControlError("La escritura no pudo verificarse.")
                method = InputMethod.ACCESSIBILITY
            else:
                if control is not None:
                    if not control.enabled:
                        raise InputControlError(
                            "El control accesible no está habilitado."
                        )
                    if control.is_password:
                        raise InputControlError(
                            "No se permite actuar sobre campos secretos."
                        )
                    active_stage = "click_delivery"
                    delivered = self._backend.click_accessible(
                        context.target, control
                    )
                    method = InputMethod.ACCESSIBILITY
                elif action.allow_coordinate_fallback:
                    x = element.bounds.x + element.bounds.width // 2
                    y = element.bounds.y + element.bounds.height // 2
                    active_stage = "coordinate_delivery"
                    delivered = self._backend.click_client_point(
                        context.target, x, y
                    )
                    method = InputMethod.COORDINATE_FALLBACK
                else:
                    raise InputControlError(
                        "No se encontró un control accesible y el fallback "
                        "está apagado."
                    )
                if not delivered:
                    raise InputControlError("Windows no confirmó la entrega del clic.")

            self._action_count += 1
            active_stage = "post_validation"
            final_state = self._require_safe_window(
                context.target, require_foreground=True
            )
            current_target = self._invalidate(context)
            if final_state.unknown_modal:
                return self._finish(
                    action,
                    InputStatus.STOPPED,
                    method,
                    "El input se entregó, pero apareció un modal desconocido.",
                    started_at,
                    current_target,
                )
            return self._finish(
                action,
                InputStatus.SUCCEEDED,
                method,
                "Input entregado y estado anterior invalidado.",
                started_at,
                current_target,
            )
        except InterruptedError:
            if touched_state:
                current_target = self._invalidate_by_action(action)
            return self._finish(
                action,
                InputStatus.CANCELLED,
                method,
                "El input fue cancelado en un límite seguro.",
                started_at,
                current_target,
                active_stage,
            )
        except (InputControlError, ObservationError):
            if touched_state and current_target is None:
                current_target = self._invalidate_by_action(action)
            status = InputStatus.STOPPED if delivered else InputStatus.FAILED
            return self._finish(
                action,
                status,
                method,
                "El input se detuvo porque el estado dejó de ser seguro.",
                started_at,
                current_target,
                active_stage,
            )
        except Exception:
            if touched_state and current_target is None:
                current_target = self._invalidate_by_action(action)
            return self._finish(
                action,
                InputStatus.STOPPED if delivered else InputStatus.FAILED,
                method,
                "El backend de input falló de forma segura.",
                started_at,
                current_target,
                active_stage,
            )

    def _validate_action_context(
        self,
        action: InputAction,
    ) -> tuple[_InputContext, VisualElement]:
        if not isinstance(action, InputAction):
            raise InputControlError("La acción de input no cumple el contrato.")
        context = self._contexts.get(action.observation_id)
        if context is None:
            raise InputControlError(
                "La observación no está registrada o quedó obsoleta."
            )
        if (
            action.window_id != context.target.window_id
            or action.window_revision != context.target.revision
        ):
            raise InputControlError("La acción no coincide con la ventana observada.")
        element = context.elements.get(action.element_id)
        if element is None:
            raise InputControlError("El elemento no pertenece a la observación.")
        self._observations.read_frame(action.observation_id, context.target)
        return context, element

    def _require_safe_window(
        self,
        target: WindowTarget,
        require_foreground: bool,
    ) -> InputWindowState:
        state = self._backend.inspect_window(target)
        if (
            not state.exists
            or not state.visible
            or state.minimized
            or state.process_id != target.process_id
            or state.client_width != target.client_width
            or state.client_height != target.client_height
            or state.unknown_modal
            or state.blocked_context
            or (require_foreground and not state.foreground)
        ):
            raise InputControlError("La ventana exacta dejó de ser segura.")
        return state

    def _invalidate(self, context: _InputContext) -> WindowTarget:
        self._contexts.pop(context.observation.observation_id, None)
        return self._observations.mark_state_changed(context.target)

    def _invalidate_by_action(self, action: InputAction) -> WindowTarget | None:
        context = self._contexts.pop(action.observation_id, None)
        if context is None:
            return None
        try:
            return self._observations.mark_state_changed(context.target)
        except ObservationError:
            return None

    def _finish(
        self,
        action: InputAction,
        status: InputStatus,
        method: InputMethod | None,
        message: str,
        started_at: float,
        current_target: WindowTarget | None = None,
        failure_stage: str | None = None,
    ) -> InputExecution:
        duration_ms = max(0.0, (self._clock() - started_at) * 1000)
        self._logger.info(
            "Input: action=%s kind=%s method=%s status=%s duration_ms=%.3f",
            action.action_id,
            action.kind.value,
            method.value if method else "none",
            status.value.upper(),
            duration_ms,
        )
        return InputExecution(
            action.action_id,
            status,
            method,
            message,
            duration_ms,
            current_target,
            failure_stage,
        )
