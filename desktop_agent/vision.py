import logging
import math
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from desktop_agent.interpretation import MonthlyUsageSnapshot, ProposalUsage
from desktop_agent.observation import (
    CaptureRegion,
    Observation,
    PixelFormat,
    RasterFrame,
    encode_png,
)

VISION_SCHEMA_VERSION = 1
MAX_VISUAL_ELEMENTS = 20
MIN_OPERATIONAL_CONFIDENCE = 0.80
_RESULT_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "contains_untrusted_instructions",
        "elements",
    }
)
_ELEMENT_FIELDS = frozenset(
    {"role", "label", "x", "y", "width", "height", "confidence"}
)
_INSTRUCTION_PATTERN = re.compile(
    r"(?:ignore\s+(?:all\s+)?(?:previous|prior)|"
    r"ignor[áa]\s+(?:las\s+)?instrucciones|"
    r"system\s+prompt|mensaje\s+del\s+sistema|"
    r"(?:run|execute|ejecut[áa])\s+(?:this\s+)?(?:command|comando|powershell|cmd)|"
    r"reveal\s+(?:the\s+)?prompt|revel[áa]\s+(?:el\s+)?prompt)",
    re.IGNORECASE,
)


class VisualRole(str, Enum):
    BUTTON = "BUTTON"
    TEXT_FIELD = "TEXT_FIELD"
    LINK = "LINK"
    CHECKBOX = "CHECKBOX"
    RADIO = "RADIO"
    MENU_ITEM = "MENU_ITEM"
    TEXT = "TEXT"
    OTHER = "OTHER"

    @property
    def actionable(self) -> bool:
        return self in {
            VisualRole.BUTTON,
            VisualRole.TEXT_FIELD,
            VisualRole.LINK,
            VisualRole.CHECKBOX,
            VisualRole.RADIO,
            VisualRole.MENU_ITEM,
        }


class VisualSource(str, Enum):
    VISION = "vision"
    ACCESSIBILITY_FUSED = "accessibility_fused"


class VisualStatus(str, Enum):
    READY = "ready"
    UNSUPPORTED = "unsupported"
    DISABLED = "disabled"
    PROVIDER_ERROR = "provider_error"
    INVALID_RESPONSE = "invalid_response"
    CONTENT_INSTRUCTION = "content_instruction"
    BUDGET_EXCEEDED = "budget_exceeded"
    USAGE_TRACKING_ERROR = "usage_tracking_error"


@dataclass(frozen=True)
class AccessibilityHint:
    role: VisualRole
    bounds: CaptureRegion
    name: str | None = None
    sensitive: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.role, VisualRole):
            raise ValueError("El rol accesible no es válido.")
        if not isinstance(self.bounds, CaptureRegion):
            raise ValueError("Los límites accesibles no son válidos.")
        if self.name is not None:
            if (
                not isinstance(self.name, str)
                or not self.name
                or self.name != self.name.strip()
                or len(self.name) > 120
            ):
                raise ValueError("El nombre accesible no es válido.")
        if type(self.sensitive) is not bool:
            raise ValueError("La marca de sensibilidad no es válida.")


@dataclass(frozen=True)
class VisualElement:
    element_id: str
    observation_id: str
    window_id: str
    window_revision: int
    role: VisualRole
    bounds: CaptureRegion
    label: str | None
    confidence: float
    source: VisualSource

    @property
    def actionable(self) -> bool:
        return self.role.actionable


@dataclass(frozen=True)
class VisionProviderResult:
    payload: object
    usage: ProposalUsage | None = None
    estimated_cost_usd: float | None = None
    monthly_usage: MonthlyUsageSnapshot | None = None


class VisionProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        provider_result: VisionProviderResult | None = None,
    ) -> None:
        super().__init__(message)
        self.provider_result = provider_result


class VisionBudgetExceededError(VisionProviderError):
    pass


class VisionUsageTrackingError(VisionProviderError):
    pass


@runtime_checkable
class VisionProvider(Protocol):
    def analyze(self, image_png: bytes) -> VisionProviderResult: ...


@dataclass(frozen=True)
class VisualInterpretation:
    status: VisualStatus
    elements: tuple[VisualElement, ...]
    duration_ms: float
    provider_configured: bool
    usage: ProposalUsage | None = None
    estimated_cost_usd: float | None = None
    monthly_usage: MonthlyUsageSnapshot | None = None


@dataclass(frozen=True)
class _Candidate:
    role: VisualRole
    label: str | None
    normalized_bounds: CaptureRegion
    confidence: float


Clock = Callable[[], float]


def _number(value: object, name: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{name} no es un número válido.")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"{name} no es un número válido.")
    return converted


def _parse_payload(payload: object) -> tuple[bool, tuple[_Candidate, ...]]:
    if not isinstance(payload, dict) or set(payload) != _RESULT_FIELDS:
        raise ValueError("La respuesta visual tiene una forma inválida.")
    if payload["schema_version"] != VISION_SCHEMA_VERSION:
        raise ValueError("La versión visual no es compatible.")
    status = payload["status"]
    if status not in {"ELEMENTS", "UNSUPPORTED"}:
        raise ValueError("El estado visual no es válido.")
    contains_instructions = payload["contains_untrusted_instructions"]
    if type(contains_instructions) is not bool:
        raise ValueError("La marca de contenido no es válida.")
    raw_elements = payload["elements"]
    if not isinstance(raw_elements, list):
        raise ValueError("Los elementos visuales no son válidos.")
    if status == "UNSUPPORTED" and raw_elements:
        raise ValueError("UNSUPPORTED no puede incluir elementos.")
    if status == "ELEMENTS" and not 1 <= len(raw_elements) <= MAX_VISUAL_ELEMENTS:
        raise ValueError("La cantidad de elementos visuales no es válida.")

    candidates: list[_Candidate] = []
    for raw in raw_elements:
        if not isinstance(raw, dict) or set(raw) != _ELEMENT_FIELDS:
            raise ValueError("Un elemento visual tiene una forma inválida.")
        try:
            role = VisualRole(raw["role"])
        except (TypeError, ValueError):
            raise ValueError("Un rol visual no está permitido.") from None
        label = raw["label"]
        if label is not None and (
            not isinstance(label, str)
            or not label
            or label != label.strip()
            or len(label) > 120
        ):
            raise ValueError("Una etiqueta visual no es válida.")
        coordinates = []
        for name in ("x", "y", "width", "height"):
            value = raw[name]
            if type(value) is not int:
                raise ValueError("Las coordenadas normalizadas deben ser enteras.")
            coordinates.append(value)
        x, y, width, height = coordinates
        if (
            x < 0
            or y < 0
            or width <= 0
            or height <= 0
            or x + width > 1000
            or y + height > 1000
        ):
            raise ValueError("Un elemento excede la imagen observada.")
        confidence = _number(raw["confidence"], "confidence")
        if not 0 <= confidence <= 1:
            raise ValueError("La confianza visual debe estar entre cero y uno.")
        candidates.append(
            _Candidate(
                role,
                label,
                CaptureRegion(x, y, width, height),
                confidence,
            )
        )
    return contains_instructions, tuple(candidates)


def _to_pixels(bounds: CaptureRegion, width: int, height: int) -> CaptureRegion:
    x = min(width - 1, round(bounds.x * width / 1000))
    y = min(height - 1, round(bounds.y * height / 1000))
    right = min(width, max(x + 1, round((bounds.x + bounds.width) * width / 1000)))
    bottom = min(
        height,
        max(y + 1, round((bounds.y + bounds.height) * height / 1000)),
    )
    return CaptureRegion(x, y, right - x, bottom - y)


def _intersection_over_union(first: CaptureRegion, second: CaptureRegion) -> float:
    left = max(first.x, second.x)
    top = max(first.y, second.y)
    right = min(first.x + first.width, second.x + second.width)
    bottom = min(first.y + first.height, second.y + second.height)
    intersection = max(0, right - left) * max(0, bottom - top)
    if intersection == 0:
        return 0.0
    union = first.width * first.height + second.width * second.height - intersection
    return intersection / union


def _best_accessibility_match(
    bounds: CaptureRegion,
    role: VisualRole,
    hints: Sequence[AccessibilityHint],
) -> AccessibilityHint | None:
    compatible = [
        hint
        for hint in hints
        if hint.role is role and _intersection_over_union(bounds, hint.bounds) >= 0.25
    ]
    if not compatible:
        return None
    return max(
        compatible,
        key=lambda hint: _intersection_over_union(bounds, hint.bounds),
    )


def _instruction_like(text: str | None) -> bool:
    return text is not None and _INSTRUCTION_PATTERN.search(text) is not None


class VisualInterpreter:
    """Valida observaciones y nunca convierte contenido visual en una acción."""

    def __init__(
        self,
        provider: VisionProvider | None,
        logger: logging.Logger,
        clock: Clock = time.monotonic,
    ) -> None:
        if provider is not None and not isinstance(provider, VisionProvider):
            raise TypeError("El proveedor visual no cumple el contrato.")
        self._provider = provider
        self._logger = logger
        self._clock = clock

    def interpret(
        self,
        observation: Observation,
        frame: RasterFrame,
        accessibility: Sequence[AccessibilityHint] = (),
    ) -> VisualInterpretation:
        started_at = self._clock()
        if self._provider is None:
            return self._result(VisualStatus.DISABLED, (), started_at)
        if not isinstance(observation, Observation) or not isinstance(
            frame, RasterFrame
        ):
            return self._result(VisualStatus.INVALID_RESPONSE, (), started_at)
        if (
            frame.width != observation.width
            or frame.height != observation.height
            or frame.pixel_format is not PixelFormat.BGRA32
        ):
            return self._result(VisualStatus.INVALID_RESPONSE, (), started_at)
        hints = tuple(accessibility)
        if any(not isinstance(hint, AccessibilityHint) for hint in hints):
            return self._result(VisualStatus.INVALID_RESPONSE, (), started_at)
        frame_bounds = CaptureRegion(0, 0, frame.width, frame.height)
        if any(not _contains(frame_bounds, hint.bounds) for hint in hints):
            return self._result(VisualStatus.INVALID_RESPONSE, (), started_at)
        if any(_instruction_like(hint.name) for hint in hints):
            return self._result(
                VisualStatus.CONTENT_INSTRUCTION, (), started_at
            )

        self._logger.info(
            "Vision: status=ANALYZING observation=%s width=%d height=%d",
            observation.observation_id,
            frame.width,
            frame.height,
        )
        try:
            provider_result = self._provider.analyze(encode_png(frame))
        except VisionBudgetExceededError as error:
            return self._error_result(
                VisualStatus.BUDGET_EXCEEDED, started_at, error
            )
        except VisionUsageTrackingError as error:
            return self._error_result(
                VisualStatus.USAGE_TRACKING_ERROR, started_at, error
            )
        except VisionProviderError as error:
            return self._error_result(
                VisualStatus.PROVIDER_ERROR, started_at, error
            )
        if not isinstance(provider_result, VisionProviderResult):
            return self._result(
                VisualStatus.INVALID_RESPONSE, (), started_at
            )
        try:
            contains_instructions, candidates = _parse_payload(
                provider_result.payload
            )
        except ValueError:
            return self._result(
                VisualStatus.INVALID_RESPONSE,
                (),
                started_at,
                provider_result,
            )
        if contains_instructions or any(
            _instruction_like(candidate.label) for candidate in candidates
        ):
            return self._result(
                VisualStatus.CONTENT_INSTRUCTION,
                (),
                started_at,
                provider_result,
            )

        elements: list[VisualElement] = []
        for candidate in candidates:
            if candidate.confidence < MIN_OPERATIONAL_CONFIDENCE:
                continue
            bounds = _to_pixels(
                candidate.normalized_bounds, frame.width, frame.height
            )
            hint = _best_accessibility_match(bounds, candidate.role, hints)
            label = candidate.label
            source = VisualSource.VISION
            if hint is not None:
                label = None if hint.sensitive else hint.name or label
                source = VisualSource.ACCESSIBILITY_FUSED
            elements.append(
                VisualElement(
                    element_id=(
                        f"{observation.observation_id}:element-{len(elements) + 1}"
                    ),
                    observation_id=observation.observation_id,
                    window_id=observation.window_id,
                    window_revision=observation.window_revision,
                    role=candidate.role,
                    bounds=bounds,
                    label=label,
                    confidence=candidate.confidence,
                    source=source,
                )
            )
        status = VisualStatus.READY if elements else VisualStatus.UNSUPPORTED
        return self._result(status, tuple(elements), started_at, provider_result)

    def _error_result(
        self,
        status: VisualStatus,
        started_at: float,
        error: VisionProviderError,
    ) -> VisualInterpretation:
        return self._result(status, (), started_at, error.provider_result)

    def _result(
        self,
        status: VisualStatus,
        elements: tuple[VisualElement, ...],
        started_at: float,
        provider_result: VisionProviderResult | None = None,
    ) -> VisualInterpretation:
        duration_ms = max(0.0, (self._clock() - started_at) * 1000)
        self._logger.info(
            "Vision: status=%s elements=%d duration_ms=%.3f",
            status.value.upper(),
            len(elements),
            duration_ms,
        )
        return VisualInterpretation(
            status=status,
            elements=elements,
            duration_ms=duration_ms,
            provider_configured=self._provider is not None,
            usage=provider_result.usage if provider_result else None,
            estimated_cost_usd=(
                provider_result.estimated_cost_usd if provider_result else None
            ),
            monthly_usage=(
                provider_result.monthly_usage if provider_result else None
            ),
        )


def _contains(outer: CaptureRegion, inner: CaptureRegion) -> bool:
    return (
        inner.x >= outer.x
        and inner.y >= outer.y
        and inner.x + inner.width <= outer.x + outer.width
        and inner.y + inner.height <= outer.y + outer.height
    )


@dataclass(frozen=True)
class ExpectedElement:
    role: VisualRole
    bounds: CaptureRegion


@dataclass(frozen=True)
class VisionEvaluation:
    expected_count: int
    predicted_count: int
    matched_count: int
    precision: float
    recall: float
    mean_iou: float


def evaluate_elements(
    predicted: Sequence[VisualElement],
    expected: Sequence[ExpectedElement],
    minimum_iou: float = 0.5,
) -> VisionEvaluation:
    if not 0 < minimum_iou <= 1:
        raise ValueError("El umbral IoU debe estar entre cero y uno.")
    remaining = set(range(len(expected)))
    scores: list[float] = []
    for element in predicted:
        candidates = [
            (index, _intersection_over_union(element.bounds, expected[index].bounds))
            for index in remaining
            if expected[index].role is element.role
        ]
        if not candidates:
            continue
        index, score = max(candidates, key=lambda item: item[1])
        if score >= minimum_iou:
            remaining.remove(index)
            scores.append(score)
    matched = len(scores)
    precision = matched / len(predicted) if predicted else 0.0
    recall = matched / len(expected) if expected else 0.0
    mean_iou = sum(scores) / matched if matched else 0.0
    return VisionEvaluation(
        expected_count=len(expected),
        predicted_count=len(predicted),
        matched_count=matched,
        precision=precision,
        recall=recall,
        mean_iou=mean_iou,
    )
