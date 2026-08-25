import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from desktop_agent.browser_contract import (
    BrowserAdapterDependencies,
    BrowserError,
    BrowserErrorCode,
    BrowserLimits,
    BrowserOperation,
    BrowserPayload,
    BrowserStepResult,
    BrowserStepStatus,
    SearchSnapshot,
    normalize_search_query,
    resolve_site_url,
)
from desktop_agent.catalog import SUPPORTED_SITES


class BrowserSessionState(str, Enum):
    READY = "ready"
    SITE_OPEN = "site_open"
    SEARCHED = "searched"
    RESULT_SELECTED = "result_selected"
    PLAYING = "playing"
    FAILED = "failed"
    CLOSED = "closed"


@dataclass(frozen=True)
class BrowserSecurityPolicy:
    """Política fija de v0.4 para un navegador aislado y de una sola página."""

    allowed_site_keys: frozenset[str] = field(
        default_factory=lambda: frozenset({"youtube"})
    )
    max_pages: int = 1
    allow_downloads: bool = False
    allow_extensions: bool = False
    allow_file_access: bool = False
    persistent_profile: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.allowed_site_keys, frozenset)
            or self.allowed_site_keys != frozenset({"youtube"})
            or any(
                not isinstance(site_key, str)
                or site_key not in SUPPORTED_SITES
                for site_key in self.allowed_site_keys
            )
        ):
            raise ValueError("La allowlist del navegador no es válida.")
        if type(self.max_pages) is not int or self.max_pages != 1:
            raise ValueError("v0.4 permite exactamente una página.")
        restricted_flags = (
            self.allow_downloads,
            self.allow_extensions,
            self.allow_file_access,
            self.persistent_profile,
        )
        if any(type(value) is not bool or value for value in restricted_flags):
            raise ValueError("La política solicitada excede los permisos de v0.4.")


class SafeBrowserAdapter:
    """Aplica política, orden y observabilidad sobre un backend reemplazable."""

    def __init__(
        self,
        dependencies: BrowserAdapterDependencies,
        logger: logging.Logger,
        limits: BrowserLimits = BrowserLimits(),
        policy: BrowserSecurityPolicy = BrowserSecurityPolicy(),
    ) -> None:
        if not isinstance(dependencies, BrowserAdapterDependencies):
            raise TypeError("Las dependencias del navegador no son válidas.")
        if not isinstance(logger, logging.Logger):
            raise TypeError("El logger del navegador no es válido.")
        if not isinstance(limits, BrowserLimits):
            raise TypeError("Los límites del navegador no son válidos.")
        if not isinstance(policy, BrowserSecurityPolicy):
            raise TypeError("La política del navegador no es válida.")

        self._dependencies = dependencies
        self._logger = logger
        self._limits = limits
        self._policy = policy
        self._state = BrowserSessionState.READY
        self._active_site_key: str | None = None
        self._last_result_count: int | None = None

    @property
    def limits(self) -> BrowserLimits:
        return self._limits

    @property
    def policy(self) -> BrowserSecurityPolicy:
        return self._policy

    @property
    def state(self) -> BrowserSessionState:
        return self._state

    def open_site(self, site_key: str) -> BrowserStepResult:
        if self._state is not BrowserSessionState.READY:
            return self._invalid_state(BrowserOperation.OPEN_SITE)
        if (
            not isinstance(site_key, str)
            or site_key not in self._policy.allowed_site_keys
        ):
            return self._failure(
                BrowserOperation.OPEN_SITE,
                BrowserErrorCode.INVALID_INPUT,
                "El destino de navegación no está permitido.",
            )

        try:
            canonical_url = resolve_site_url(site_key)
        except ValueError:
            return self._failure(
                BrowserOperation.OPEN_SITE,
                BrowserErrorCode.INVALID_INPUT,
                "El destino de navegación no está permitido.",
            )

        result = self._execute(
            BrowserOperation.OPEN_SITE,
            lambda: self._dependencies.page.open_site(
                canonical_url,
                self._limits.navigation_timeout_seconds,
            ),
            destination=site_key,
        )
        if result.status is BrowserStepStatus.SUCCESS:
            self._active_site_key = site_key
            self._state = BrowserSessionState.SITE_OPEN
        return result

    def search(self, query: str) -> BrowserStepResult:
        if self._state is not BrowserSessionState.SITE_OPEN:
            return self._invalid_state(BrowserOperation.SEARCH)
        try:
            normalized_query = normalize_search_query(query, self._limits)
        except ValueError:
            return self._failure(
                BrowserOperation.SEARCH,
                BrowserErrorCode.INVALID_INPUT,
                "La consulta de navegación no es válida.",
            )

        result = self._execute(
            BrowserOperation.SEARCH,
            lambda: self._dependencies.page.search(
                normalized_query,
                self._limits.operation_timeout_seconds,
            ),
        )
        if result.status is BrowserStepStatus.SUCCESS:
            if not isinstance(result.payload, SearchSnapshot):
                return self._backend_contract_failure(BrowserOperation.SEARCH)
            self._last_result_count = result.payload.result_count
            self._state = BrowserSessionState.SEARCHED
        return result

    def select_first_result(self) -> BrowserStepResult:
        if self._state is not BrowserSessionState.SEARCHED:
            return self._invalid_state(BrowserOperation.SELECT_FIRST_RESULT)
        if self._last_result_count == 0:
            return self._failure(
                BrowserOperation.SELECT_FIRST_RESULT,
                BrowserErrorCode.INVALID_STATE,
                "La búsqueda no produjo resultados seleccionables.",
            )

        result = self._execute(
            BrowserOperation.SELECT_FIRST_RESULT,
            lambda: self._dependencies.page.select_first_result(
                self._limits.operation_timeout_seconds
            ),
        )
        if result.status is BrowserStepStatus.SUCCESS:
            self._state = BrowserSessionState.RESULT_SELECTED
        return result

    def start_playback(self) -> BrowserStepResult:
        if self._state is not BrowserSessionState.RESULT_SELECTED:
            return self._invalid_state(BrowserOperation.START_PLAYBACK)

        result = self._execute(
            BrowserOperation.START_PLAYBACK,
            lambda: self._dependencies.page.start_playback(
                self._limits.operation_timeout_seconds
            ),
        )
        if result.status is BrowserStepStatus.SUCCESS:
            self._state = BrowserSessionState.PLAYING
        return result

    def read_playback(self) -> BrowserStepResult:
        if self._state is not BrowserSessionState.PLAYING:
            return self._invalid_state(BrowserOperation.READ_PLAYBACK)
        return self._execute(
            BrowserOperation.READ_PLAYBACK,
            self._dependencies.page.read_playback,
        )

    def close(self) -> BrowserStepResult:
        if self._state is BrowserSessionState.CLOSED:
            return BrowserStepResult(
                BrowserOperation.CLOSE,
                BrowserStepStatus.SUCCESS,
                0.0,
            )

        started_at = self._dependencies.clock()
        failed = False
        try:
            self._dependencies.context.close()
        except Exception:
            failed = True
        try:
            self._dependencies.browser.close()
        except Exception:
            failed = True
        self._state = BrowserSessionState.CLOSED
        duration_ms = self._duration_ms(started_at)

        if failed:
            result = BrowserStepResult(
                BrowserOperation.CLOSE,
                BrowserStepStatus.FAILURE,
                duration_ms,
                error=BrowserError(
                    BrowserErrorCode.BACKEND_FAILURE,
                    "No se pudieron cerrar todos los recursos del navegador.",
                ),
            )
        else:
            result = BrowserStepResult(
                BrowserOperation.CLOSE,
                BrowserStepStatus.SUCCESS,
                duration_ms,
            )
        self._log_result(result)
        return result

    def _execute(
        self,
        operation: BrowserOperation,
        callback: Callable[[], BrowserPayload],
        destination: str | None = None,
    ) -> BrowserStepResult:
        started_at = self._dependencies.clock()
        try:
            payload = callback()
            result = BrowserStepResult(
                operation,
                BrowserStepStatus.SUCCESS,
                self._duration_ms(started_at),
                payload=payload,
            )
        except TimeoutError:
            self._state = BrowserSessionState.FAILED
            result = BrowserStepResult(
                operation,
                BrowserStepStatus.FAILURE,
                self._duration_ms(started_at),
                error=BrowserError(
                    BrowserErrorCode.TIMEOUT,
                    "La operación del navegador excedió el tiempo permitido.",
                ),
            )
        except Exception:
            self._state = BrowserSessionState.FAILED
            result = BrowserStepResult(
                operation,
                BrowserStepStatus.FAILURE,
                self._duration_ms(started_at),
                error=BrowserError(
                    BrowserErrorCode.BACKEND_FAILURE,
                    "El backend del navegador no pudo completar la operación.",
                ),
            )

        self._log_result(result, destination)
        return result

    def _invalid_state(self, operation: BrowserOperation) -> BrowserStepResult:
        return self._failure(
            operation,
            BrowserErrorCode.INVALID_STATE,
            "La operación no es válida en el estado actual del navegador.",
        )

    def _backend_contract_failure(
        self,
        operation: BrowserOperation,
    ) -> BrowserStepResult:
        self._state = BrowserSessionState.FAILED
        return self._failure(
            operation,
            BrowserErrorCode.BACKEND_FAILURE,
            "El backend del navegador devolvió un resultado no válido.",
        )

    def _failure(
        self,
        operation: BrowserOperation,
        code: BrowserErrorCode,
        message: str,
    ) -> BrowserStepResult:
        result = BrowserStepResult(
            operation,
            BrowserStepStatus.FAILURE,
            0.0,
            error=BrowserError(code, message),
        )
        self._log_result(result)
        return result

    def _duration_ms(self, started_at: float) -> float:
        return max(0.0, (self._dependencies.clock() - started_at) * 1000)

    def _log_result(
        self,
        result: BrowserStepResult,
        destination: str | None = None,
    ) -> None:
        safe_destination = destination or self._active_site_key or "none"
        error_code = result.error.code.value if result.error is not None else "none"
        self._logger.info(
            "Browser: operation=%s destination=%s status=%s "
            "duration_ms=%.3f error=%s",
            result.operation.value,
            safe_destination,
            result.status.value,
            result.duration_ms,
            error_code,
        )
