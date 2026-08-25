import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from desktop_agent.catalog import SUPPORTED_SITES

MAX_BROWSER_TIMEOUT_SECONDS = 60.0
MAX_SEARCH_QUERY_LENGTH = 200

Clock = Callable[[], float]
Wait = Callable[[float], None]


class BrowserOperation(str, Enum):
    """Operaciones semánticas que el núcleo puede solicitar al adaptador."""

    OPEN_SITE = "OPEN_SITE"
    SEARCH = "SEARCH"
    SELECT_FIRST_RESULT = "SELECT_FIRST_RESULT"
    START_PLAYBACK = "START_PLAYBACK"
    READ_PLAYBACK = "READ_PLAYBACK"
    VERIFY_PLAYBACK = "VERIFY_PLAYBACK"
    CLOSE = "CLOSE"


class BrowserStepStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"


class BrowserErrorCode(str, Enum):
    INVALID_INPUT = "invalid_input"
    INVALID_STATE = "invalid_state"
    TIMEOUT = "timeout"
    CONSENT_REQUIRED = "consent_required"
    NO_RESULTS = "no_results"
    DOM_UNAVAILABLE = "dom_unavailable"
    PLAYBACK_NOT_CONFIRMED = "playback_not_confirmed"
    BACKEND_FAILURE = "backend_failure"
    CANCELLED = "cancelled"


class BrowserContractValidationError(ValueError):
    """Una entrada o resultado no cumple el contrato local del navegador."""


class BrowserBackendCondition(RuntimeError):
    """Condición esperable del backend que el adaptador debe convertir."""


class BrowserConsentRequiredError(BrowserBackendCondition):
    """El sitio requiere una decisión de consentimiento no automatizada."""


class BrowserNoResultsError(BrowserBackendCondition):
    """El sitio no expone resultados que cumplan el criterio local."""


class BrowserDomUnavailableError(BrowserBackendCondition):
    """El DOM observado no cumple las expectativas locales del adaptador."""


class BrowserPlaybackNotConfirmedError(BrowserBackendCondition):
    """La observación no demuestra reproducción audible y progresiva."""


@dataclass(frozen=True)
class BrowserLimits:
    """Límites locales; ningún texto externo puede modificarlos."""

    navigation_timeout_seconds: float = 10.0
    operation_timeout_seconds: float = 5.0
    verification_window_seconds: float = 1.0
    minimum_playback_progress_seconds: float = 0.1
    flow_timeout_seconds: float = 30.0
    max_query_length: int = MAX_SEARCH_QUERY_LENGTH

    def __post_init__(self) -> None:
        timeout_values = (
            self.navigation_timeout_seconds,
            self.operation_timeout_seconds,
            self.verification_window_seconds,
            self.flow_timeout_seconds,
        )
        if any(
            type(value) not in (int, float)
            or not math.isfinite(float(value))
            or float(value) <= 0
            or float(value) > MAX_BROWSER_TIMEOUT_SECONDS
            for value in timeout_values
        ):
            raise BrowserContractValidationError(
                "Los tiempos del navegador deben ser positivos y acotados."
            )
        if (
            self.navigation_timeout_seconds > self.flow_timeout_seconds
            or self.operation_timeout_seconds > self.flow_timeout_seconds
            or self.verification_window_seconds > self.flow_timeout_seconds
        ):
            raise BrowserContractValidationError(
                "Un tiempo de paso no puede superar el límite del flujo."
            )
        if (
            type(self.minimum_playback_progress_seconds) not in (int, float)
            or not math.isfinite(
                float(self.minimum_playback_progress_seconds)
            )
            or float(self.minimum_playback_progress_seconds) <= 0
            or self.minimum_playback_progress_seconds
            > self.verification_window_seconds
        ):
            raise BrowserContractValidationError(
                "El progreso mínimo debe ser positivo y no superar la ventana."
            )
        if (
            type(self.max_query_length) is not int
            or self.max_query_length <= 0
            or self.max_query_length > MAX_SEARCH_QUERY_LENGTH
        ):
            raise BrowserContractValidationError(
                "El límite de consulta del navegador no es válido."
            )


def resolve_site_url(site_key: str) -> str:
    """Resuelve una clave canónica; nunca acepta una URL proporcionada externamente."""

    if not isinstance(site_key, str) or not site_key:
        raise BrowserContractValidationError(
            "El destino del navegador debe ser una clave canónica."
        )
    site = SUPPORTED_SITES.get(site_key)
    if site is None:
        raise BrowserContractValidationError(
            "El destino del navegador no pertenece al catálogo."
        )
    return site.url


def normalize_search_query(
    query: str,
    limits: BrowserLimits = BrowserLimits(),
) -> str:
    """Normaliza una consulta tratándola únicamente como datos."""

    if not isinstance(query, str):
        raise BrowserContractValidationError(
            "La consulta del navegador debe ser texto."
        )
    normalized = re.sub(r"\s+", " ", query).strip()
    if not normalized:
        raise BrowserContractValidationError(
            "La consulta del navegador no puede estar vacía."
        )
    if len(normalized) > limits.max_query_length:
        raise BrowserContractValidationError(
            "La consulta del navegador supera el límite permitido."
        )
    return normalized


@dataclass(frozen=True)
class PageSnapshot:
    url: str
    title: str

    def __post_init__(self) -> None:
        if not isinstance(self.url, str) or not self.url:
            raise BrowserContractValidationError(
                "La observación de página requiere una URL."
            )
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise BrowserContractValidationError(
                "La observación de página contiene una URL inválida."
            )
        if not isinstance(self.title, str) or not self.title.strip():
            raise BrowserContractValidationError(
                "La observación de página requiere un título."
            )


@dataclass(frozen=True)
class SearchSnapshot:
    page: PageSnapshot
    result_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.page, PageSnapshot):
            raise BrowserContractValidationError(
                "La búsqueda requiere una observación de página."
            )
        if type(self.result_count) is not int or self.result_count < 0:
            raise BrowserContractValidationError(
                "La cantidad de resultados no es válida."
            )


@dataclass(frozen=True)
class PlaybackSnapshot:
    page: PageSnapshot
    paused: bool
    muted: bool
    volume: float | None
    current_time: float

    def __post_init__(self) -> None:
        if not isinstance(self.page, PageSnapshot):
            raise BrowserContractValidationError(
                "La reproducción requiere una observación de página."
            )
        if type(self.paused) is not bool or type(self.muted) is not bool:
            raise BrowserContractValidationError(
                "El estado de reproducción contiene indicadores inválidos."
            )
        if self.volume is not None and (
            type(self.volume) not in (int, float)
            or not math.isfinite(float(self.volume))
            or not 0 <= float(self.volume) <= 1
        ):
            raise BrowserContractValidationError(
                "El volumen observado debe estar entre cero y uno."
            )
        if (
            type(self.current_time) not in (int, float)
            or not math.isfinite(float(self.current_time))
            or float(self.current_time) < 0
        ):
            raise BrowserContractValidationError(
                "El tiempo de reproducción observado no es válido."
            )


@dataclass(frozen=True)
class BrowserError:
    code: BrowserErrorCode
    message: str
    retryable: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.code, BrowserErrorCode):
            raise BrowserContractValidationError(
                "El error del navegador requiere un código conocido."
            )
        if not isinstance(self.message, str) or not self.message.strip():
            raise BrowserContractValidationError(
                "El error del navegador requiere un mensaje seguro."
            )
        if type(self.retryable) is not bool:
            raise BrowserContractValidationError(
                "El indicador de reintento del navegador no es válido."
            )


BrowserPayload = PageSnapshot | SearchSnapshot | PlaybackSnapshot


@dataclass(frozen=True)
class BrowserStepResult:
    operation: BrowserOperation
    status: BrowserStepStatus
    duration_ms: float
    payload: BrowserPayload | None = None
    error: BrowserError | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, BrowserOperation):
            raise BrowserContractValidationError(
                "El resultado requiere una operación conocida."
            )
        if not isinstance(self.status, BrowserStepStatus):
            raise BrowserContractValidationError(
                "El resultado requiere un estado conocido."
            )
        if (
            type(self.duration_ms) not in (int, float)
            or not math.isfinite(float(self.duration_ms))
            or float(self.duration_ms) < 0
        ):
            raise BrowserContractValidationError(
                "La duración del navegador no es válida."
            )
        if self.error is not None and not isinstance(self.error, BrowserError):
            raise BrowserContractValidationError(
                "El resultado contiene un error no estructurado."
            )

        if self.status is BrowserStepStatus.FAILURE:
            if self.error is None or self.payload is not None:
                raise BrowserContractValidationError(
                    "Un fallo requiere error y no puede incluir resultado."
                )
            return

        if self.error is not None:
            raise BrowserContractValidationError(
                "Un resultado exitoso no puede incluir un error."
            )

        expected_payloads: dict[BrowserOperation, type[object] | None] = {
            BrowserOperation.OPEN_SITE: PageSnapshot,
            BrowserOperation.SEARCH: SearchSnapshot,
            BrowserOperation.SELECT_FIRST_RESULT: PageSnapshot,
            BrowserOperation.START_PLAYBACK: PlaybackSnapshot,
            BrowserOperation.READ_PLAYBACK: PlaybackSnapshot,
            BrowserOperation.VERIFY_PLAYBACK: PlaybackSnapshot,
            BrowserOperation.CLOSE: None,
        }
        expected_payload = expected_payloads[self.operation]
        if expected_payload is None:
            if self.payload is not None:
                raise BrowserContractValidationError(
                    "El cierre no puede incluir una observación."
                )
        elif not isinstance(self.payload, expected_payload):
            raise BrowserContractValidationError(
                "La observación no corresponde a la operación."
            )


@runtime_checkable
class BrowserHandle(Protocol):
    def close(self) -> None: ...


@runtime_checkable
class BrowserContextHandle(Protocol):
    def close(self) -> None: ...


@runtime_checkable
class BrowserPage(Protocol):
    """Puerto interno; la implementación conserva sus selectores localmente."""

    def open_site(self, canonical_url: str, timeout_seconds: float) -> PageSnapshot: ...

    def search(self, query: str, timeout_seconds: float) -> SearchSnapshot: ...

    def select_first_result(self, timeout_seconds: float) -> PageSnapshot: ...

    def start_playback(self, timeout_seconds: float) -> PlaybackSnapshot: ...

    def read_playback(self) -> PlaybackSnapshot: ...


@dataclass(frozen=True)
class BrowserAdapterDependencies:
    """Efectos reemplazables requeridos por el futuro adaptador."""

    browser: BrowserHandle
    context: BrowserContextHandle
    page: BrowserPage
    clock: Clock = time.perf_counter
    wait: Wait = time.sleep

    def __post_init__(self) -> None:
        if not isinstance(self.browser, BrowserHandle):
            raise BrowserContractValidationError(
                "La dependencia browser no cumple el contrato."
            )
        if not isinstance(self.context, BrowserContextHandle):
            raise BrowserContractValidationError(
                "La dependencia context no cumple el contrato."
            )
        if not isinstance(self.page, BrowserPage):
            raise BrowserContractValidationError(
                "La dependencia page no cumple el contrato."
            )
        if not callable(self.clock) or not callable(self.wait):
            raise BrowserContractValidationError(
                "El reloj y la espera deben ser reemplazables."
            )


@runtime_checkable
class BrowserAdapter(Protocol):
    """Contrato público semántico; no expone URLs, selectores ni scripts."""

    @property
    def limits(self) -> BrowserLimits: ...

    def open_site(self, site_key: str) -> BrowserStepResult: ...

    def search(self, query: str) -> BrowserStepResult: ...

    def select_first_result(self) -> BrowserStepResult: ...

    def start_playback(self) -> BrowserStepResult: ...

    def read_playback(self) -> BrowserStepResult: ...

    def verify_playback(self) -> BrowserStepResult: ...

    def close(self) -> BrowserStepResult: ...
