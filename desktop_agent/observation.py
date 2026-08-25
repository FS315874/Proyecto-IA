import logging
import struct
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol, runtime_checkable


class ObservationError(RuntimeError):
    """La observación no pudo obtenerse o dejó de ser vigente."""


class PixelFormat(str, Enum):
    BGRA32 = "BGRA32"


@dataclass(frozen=True)
class CaptureRegion:
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        values = (self.x, self.y, self.width, self.height)
        if any(type(value) is not int for value in values):
            raise ValueError("La región debe usar enteros.")
        if self.x < 0 or self.y < 0 or self.width <= 0 or self.height <= 0:
            raise ValueError("La región debe tener origen y tamaño válidos.")


@dataclass(frozen=True)
class RedactionRegion(CaptureRegion):
    """Área relativa a la captura que debe reemplazarse antes de exponerla."""


@dataclass(frozen=True)
class WindowTarget:
    window_id: str
    native_handle: int
    process_id: int
    client_width: int
    client_height: int
    revision: int = 0

    def __post_init__(self) -> None:
        if not self.window_id or not isinstance(self.window_id, str):
            raise ValueError("La ventana debe tener un identificador local.")
        numeric = (
            self.native_handle,
            self.process_id,
            self.client_width,
            self.client_height,
            self.revision,
        )
        if any(type(value) is not int for value in numeric):
            raise ValueError("Los metadatos de ventana no son válidos.")
        if (
            self.native_handle <= 0
            or self.process_id <= 0
            or self.client_width <= 0
            or self.client_height <= 0
            or self.revision < 0
        ):
            raise ValueError("Los metadatos de ventana no son válidos.")


@dataclass(frozen=True)
class RasterFrame:
    width: int
    height: int
    stride: int
    pixels: bytes
    pixel_format: PixelFormat = PixelFormat.BGRA32

    def __post_init__(self) -> None:
        if type(self.width) is not int or type(self.height) is not int:
            raise ValueError("Las dimensiones de la imagen no son válidas.")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Las dimensiones de la imagen no son válidas.")
        if type(self.stride) is not int or self.stride < self.width * 4:
            raise ValueError("El stride de la imagen no es válido.")
        if not isinstance(self.pixels, bytes):
            raise ValueError("Los píxeles deben ser bytes inmutables.")
        if len(self.pixels) != self.stride * self.height:
            raise ValueError("La cantidad de píxeles no coincide con la imagen.")
        if self.pixel_format is not PixelFormat.BGRA32:
            raise ValueError("El formato de píxeles no está permitido.")


@dataclass(frozen=True)
class Observation:
    observation_id: str
    window_id: str
    window_revision: int
    captured_at: float
    expires_at: float
    region: CaptureRegion
    width: int
    height: int
    pixel_format: PixelFormat
    redaction_count: int


@dataclass(frozen=True)
class ObservationLimits:
    max_width: int = 1280
    max_height: int = 720
    max_pixels: int = 921_600
    min_interval_seconds: float = 0.25
    max_captures: int = 8
    retention_seconds: float = 30.0

    def __post_init__(self) -> None:
        integer_values = (
            self.max_width,
            self.max_height,
            self.max_pixels,
            self.max_captures,
        )
        if any(type(value) is not int or value <= 0 for value in integer_values):
            raise ValueError("Los límites enteros deben ser positivos.")
        real_values = (self.min_interval_seconds, self.retention_seconds)
        if any(
            type(value) not in (int, float) or float(value) <= 0
            for value in real_values
        ):
            raise ValueError("Los límites temporales deben ser positivos.")
        object.__setattr__(
            self, "min_interval_seconds", float(self.min_interval_seconds)
        )
        object.__setattr__(
            self, "retention_seconds", float(self.retention_seconds)
        )


@runtime_checkable
class CaptureBackend(Protocol):
    def capture(
        self,
        target: WindowTarget,
        region: CaptureRegion,
    ) -> RasterFrame: ...


Clock = Callable[[], float]
ObservationIdFactory = Callable[[], str]


def _new_observation_id() -> str:
    return f"obs-{uuid.uuid4().hex}"


def _inside(inner: CaptureRegion, outer: CaptureRegion) -> bool:
    return (
        inner.x >= outer.x
        and inner.y >= outer.y
        and inner.x + inner.width <= outer.x + outer.width
        and inner.y + inner.height <= outer.y + outer.height
    )


def _redact(
    frame: RasterFrame,
    regions: Sequence[RedactionRegion],
) -> RasterFrame:
    if not regions:
        return frame
    pixels = bytearray(frame.pixels)
    for region in regions:
        for y in range(region.y, region.y + region.height):
            row = y * frame.stride
            for x in range(region.x, region.x + region.width):
                offset = row + x * 4
                pixels[offset : offset + 4] = b"\x00\x00\x00\xff"
    return replace(frame, pixels=bytes(pixels))


class ObservationService:
    """Captura, minimiza y conserva imágenes solo durante una sesión acotada."""

    def __init__(
        self,
        backend: CaptureBackend,
        logger: logging.Logger,
        limits: ObservationLimits = ObservationLimits(),
        clock: Clock = time.monotonic,
        id_factory: ObservationIdFactory = _new_observation_id,
    ) -> None:
        if not isinstance(backend, CaptureBackend):
            raise TypeError("El backend de captura no cumple el contrato.")
        self._backend = backend
        self._logger = logger
        self._limits = limits
        self._clock = clock
        self._id_factory = id_factory
        self._records: dict[str, tuple[Observation, RasterFrame]] = {}
        self._revisions: dict[str, int] = {}
        self._last_capture: dict[str, float] = {}
        self._capture_count = 0

    def capture(
        self,
        target: WindowTarget,
        region: CaptureRegion | None = None,
        redactions: Sequence[RedactionRegion] = (),
    ) -> Observation:
        self._purge_expired()
        self._validate_target(target)
        selected = region or CaptureRegion(
            0, 0, target.client_width, target.client_height
        )
        self._validate_region(target, selected)
        normalized_redactions = tuple(redactions)
        capture_bounds = CaptureRegion(0, 0, selected.width, selected.height)
        if any(
            not isinstance(item, RedactionRegion)
            or not _inside(item, capture_bounds)
            for item in normalized_redactions
        ):
            raise ObservationError("Una región sensible excede la captura.")

        now = self._clock()
        last = self._last_capture.get(target.window_id)
        if last is not None and now - last < self._limits.min_interval_seconds:
            raise ObservationError("La frecuencia de captura supera el límite.")
        if self._capture_count >= self._limits.max_captures:
            raise ObservationError("La sesión alcanzó su máximo de capturas.")

        self._logger.info(
            "Observation: status=CAPTURING window=%s revision=%d "
            "width=%d height=%d redactions=%d",
            target.window_id,
            target.revision,
            selected.width,
            selected.height,
            len(normalized_redactions),
        )
        try:
            frame = self._backend.capture(target, selected)
        except ObservationError:
            self._logger.info(
                "Observation: status=FAILED window=%s", target.window_id
            )
            raise
        except Exception:
            self._logger.info(
                "Observation: status=FAILED window=%s", target.window_id
            )
            raise ObservationError("La captura local falló.") from None
        if frame.width != selected.width or frame.height != selected.height:
            raise ObservationError("El backend devolvió dimensiones inesperadas.")
        frame = _redact(frame, normalized_redactions)

        observation_id = self._id_factory()
        if not isinstance(observation_id, str) or not observation_id:
            raise ObservationError("No se pudo crear un identificador local.")
        observation = Observation(
            observation_id=observation_id,
            window_id=target.window_id,
            window_revision=target.revision,
            captured_at=now,
            expires_at=now + self._limits.retention_seconds,
            region=selected,
            width=frame.width,
            height=frame.height,
            pixel_format=frame.pixel_format,
            redaction_count=len(normalized_redactions),
        )
        self._records[observation_id] = (observation, frame)
        self._last_capture[target.window_id] = now
        self._capture_count += 1
        self._logger.info(
            "Observation: id=%s status=SUCCEEDED window=%s revision=%d",
            observation_id,
            target.window_id,
            target.revision,
        )
        return observation

    def read_frame(
        self,
        observation_id: str,
        target: WindowTarget,
    ) -> RasterFrame:
        self._purge_expired()
        self._validate_target(target)
        record = self._records.get(observation_id)
        if record is None:
            raise ObservationError("La observación no existe o caducó.")
        observation, frame = record
        if (
            observation.window_id != target.window_id
            or observation.window_revision != target.revision
        ):
            raise ObservationError("La observación pertenece a otro estado.")
        return frame

    def mark_state_changed(self, target: WindowTarget) -> WindowTarget:
        self._validate_target(target)
        next_revision = target.revision + 1
        self._revisions[target.window_id] = next_revision
        stale_ids = [
            observation_id
            for observation_id, (observation, _) in self._records.items()
            if observation.window_id == target.window_id
        ]
        for observation_id in stale_ids:
            del self._records[observation_id]
        self._logger.info(
            "Observation: status=INVALIDATED window=%s revision=%d",
            target.window_id,
            next_revision,
        )
        return replace(target, revision=next_revision)

    def close(self) -> None:
        self._records.clear()
        self._last_capture.clear()

    def _validate_target(self, target: WindowTarget) -> None:
        if not isinstance(target, WindowTarget):
            raise ObservationError("La ventana objetivo no es válida.")
        current = self._revisions.setdefault(target.window_id, target.revision)
        if current != target.revision:
            raise ObservationError("La referencia de ventana quedó obsoleta.")

    def _validate_region(
        self,
        target: WindowTarget,
        region: CaptureRegion,
    ) -> None:
        if not isinstance(region, CaptureRegion):
            raise ObservationError("La región de captura no es válida.")
        target_bounds = CaptureRegion(
            0, 0, target.client_width, target.client_height
        )
        if not _inside(region, target_bounds):
            raise ObservationError("La región excede la ventana objetivo.")
        if (
            region.width > self._limits.max_width
            or region.height > self._limits.max_height
            or region.width * region.height > self._limits.max_pixels
        ):
            raise ObservationError("La región excede los límites visuales.")

    def _purge_expired(self) -> None:
        now = self._clock()
        expired = [
            observation_id
            for observation_id, (observation, _) in self._records.items()
            if observation.expires_at <= now
        ]
        for observation_id in expired:
            del self._records[observation_id]


def encode_bmp(frame: RasterFrame) -> bytes:
    """Codifica BGRA top-down como BMP local sin agregar una dependencia."""

    if frame.stride != frame.width * 4:
        raise ObservationError("El stride no se puede codificar como BMP.")
    info_header = struct.pack(
        "<IiiHHIIiiII",
        40,
        frame.width,
        -frame.height,
        1,
        32,
        0,
        len(frame.pixels),
        2835,
        2835,
        0,
        0,
    )
    pixel_offset = 14 + len(info_header)
    file_header = struct.pack(
        "<2sIHHI",
        b"BM",
        pixel_offset + len(frame.pixels),
        0,
        0,
        pixel_offset,
    )
    return file_header + info_header + frame.pixels
