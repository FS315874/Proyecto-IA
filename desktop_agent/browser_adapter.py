import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import parse_qs, urlsplit

from desktop_agent.browser_contract import (
    BrowserAdapterDependencies,
    BrowserConsentRequiredError,
    BrowserContentUnavailableError,
    BrowserDomUnavailableError,
    BrowserError,
    BrowserErrorCode,
    BrowserLimits,
    BrowserNoResultsError,
    BrowserOperation,
    BrowserPayload,
    BrowserPlaybackNotConfirmedError,
    BrowserStepResult,
    BrowserStepStatus,
    PlaybackSnapshot,
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
    """Política fija para un navegador semántico de una sola página."""

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
        flags = (
            self.allow_downloads,
            self.allow_extensions,
            self.allow_file_access,
            self.persistent_profile,
        )
        if any(type(value) is not bool for value in flags):
            raise ValueError("La política del perfil contiene indicadores inválidos.")
        if self.allow_downloads or self.allow_file_access:
            raise ValueError("La política no permite descargas ni archivos locales.")
        if self.allow_extensions is not self.persistent_profile:
            raise ValueError(
                "Una sesión habitual debe declarar perfil persistente y extensión."
            )


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
        self._close_started = False
        self._closed_resources: set[str] = set()

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
                BrowserErrorCode.NO_RESULTS,
                "La búsqueda no produjo resultados permitidos.",
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

    def resume_current_playback(self) -> BrowserStepResult:
        """Reanuda el video conocido sin convertir la orden en una búsqueda."""

        if self._close_started or self._state not in {
            BrowserSessionState.READY,
            BrowserSessionState.PLAYING,
        }:
            return self._invalid_state(BrowserOperation.START_PLAYBACK)
        if (
            self._state is BrowserSessionState.READY
            and not self._policy.persistent_profile
        ):
            return self._invalid_state(BrowserOperation.START_PLAYBACK)
        if (
            self._state is BrowserSessionState.PLAYING
            and self._active_site_key != "youtube"
        ):
            return self._invalid_state(BrowserOperation.START_PLAYBACK)

        self._active_site_key = "youtube"
        result = self._execute(
            BrowserOperation.START_PLAYBACK,
            lambda: self._dependencies.page.start_playback(
                self._limits.operation_timeout_seconds
            ),
        )
        if result.status is BrowserStepStatus.SUCCESS:
            if not isinstance(result.payload, PlaybackSnapshot):
                return self._backend_contract_failure(
                    BrowserOperation.START_PLAYBACK
                )
            self._state = BrowserSessionState.PLAYING
        return result

    def read_playback(self) -> BrowserStepResult:
        if self._state is not BrowserSessionState.PLAYING:
            return self._invalid_state(BrowserOperation.READ_PLAYBACK)
        return self._execute(
            BrowserOperation.READ_PLAYBACK,
            self._dependencies.page.read_playback,
        )

    def verify_playback(self) -> BrowserStepResult:
        if self._state is not BrowserSessionState.PLAYING:
            return self._invalid_state(BrowserOperation.VERIFY_PLAYBACK)
        return self._execute(
            BrowserOperation.VERIFY_PLAYBACK,
            self._verify_playback_observable,
        )

    def reset(self) -> BrowserStepResult:
        """Reutiliza página y contexto para un nuevo flujo semántico."""

        if self._state is BrowserSessionState.CLOSED or self._close_started:
            return self._invalid_state(BrowserOperation.RESET)
        self._state = BrowserSessionState.READY
        self._active_site_key = None
        self._last_result_count = None
        result = BrowserStepResult(
            BrowserOperation.RESET,
            BrowserStepStatus.SUCCESS,
            0.0,
        )
        self._log_result(result)
        return result

    def close(self) -> BrowserStepResult:
        if self._state is BrowserSessionState.CLOSED:
            return BrowserStepResult(
                BrowserOperation.CLOSE,
                BrowserStepStatus.SUCCESS,
                0.0,
            )

        started_at = self._dependencies.clock()
        self._close_started = True
        failed = False
        for name in ("context", "browser"):
            if name in self._closed_resources:
                continue
            try:
                getattr(self._dependencies, name).close()
            except Exception:
                failed = True
            else:
                self._closed_resources.add(name)
        self._state = BrowserSessionState.FAILED if failed else BrowserSessionState.CLOSED
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
        except BrowserConsentRequiredError:
            self._state = BrowserSessionState.FAILED
            result = BrowserStepResult(
                operation,
                BrowserStepStatus.FAILURE,
                self._duration_ms(started_at),
                error=BrowserError(
                    BrowserErrorCode.CONSENT_REQUIRED,
                    "El sitio requiere una decisión de consentimiento.",
                ),
            )
        except BrowserNoResultsError:
            self._state = BrowserSessionState.FAILED
            result = BrowserStepResult(
                operation,
                BrowserStepStatus.FAILURE,
                self._duration_ms(started_at),
                error=BrowserError(
                    BrowserErrorCode.NO_RESULTS,
                    "El sitio no produjo resultados permitidos.",
                ),
            )
        except BrowserContentUnavailableError:
            self._state = BrowserSessionState.FAILED
            result = BrowserStepResult(
                operation,
                BrowserStepStatus.FAILURE,
                self._duration_ms(started_at),
                error=BrowserError(
                    BrowserErrorCode.CONTENT_UNAVAILABLE,
                    "El contenido seleccionado no está disponible.",
                ),
            )
        except BrowserDomUnavailableError:
            self._state = BrowserSessionState.FAILED
            result = BrowserStepResult(
                operation,
                BrowserStepStatus.FAILURE,
                self._duration_ms(started_at),
                error=BrowserError(
                    BrowserErrorCode.DOM_UNAVAILABLE,
                    "El sitio no expone la estructura esperada.",
                ),
            )
        except BrowserPlaybackNotConfirmedError:
            self._state = BrowserSessionState.FAILED
            result = BrowserStepResult(
                operation,
                BrowserStepStatus.FAILURE,
                self._duration_ms(started_at),
                error=BrowserError(
                    BrowserErrorCode.PLAYBACK_NOT_CONFIRMED,
                    "No se pudo confirmar una reproducción audible y progresiva.",
                ),
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

    def _verify_playback_observable(self) -> BrowserPayload:
        initial = self._dependencies.page.read_playback()
        initial_identity = self._validate_playback_snapshot(initial)
        self._dependencies.wait(self._limits.verification_window_seconds)
        final = self._dependencies.page.read_playback()
        final_identity = self._validate_playback_snapshot(final)

        if initial_identity != final_identity:
            raise BrowserPlaybackNotConfirmedError
        progress = final.current_time - initial.current_time
        if progress < self._limits.minimum_playback_progress_seconds:
            raise BrowserPlaybackNotConfirmedError
        return final

    def _validate_playback_snapshot(
        self,
        snapshot: BrowserPayload,
    ) -> tuple[str, str]:
        if not isinstance(snapshot, PlaybackSnapshot):
            raise BrowserPlaybackNotConfirmedError
        if snapshot.paused or snapshot.muted:
            raise BrowserPlaybackNotConfirmedError
        if snapshot.volume is not None and snapshot.volume <= 0:
            raise BrowserPlaybackNotConfirmedError
        return self._playback_identity(snapshot)

    def _playback_identity(
        self,
        snapshot: PlaybackSnapshot,
    ) -> tuple[str, str]:
        if self._active_site_key != "youtube":
            raise BrowserPlaybackNotConfirmedError
        canonical = urlsplit(resolve_site_url(self._active_site_key))
        observed = urlsplit(snapshot.page.url)
        video_ids = parse_qs(observed.query).get("v", [])
        if (
            observed.scheme != canonical.scheme
            or observed.hostname != canonical.hostname
            or observed.path != "/watch"
            or len(video_ids) != 1
            or not video_ids[0]
        ):
            raise BrowserPlaybackNotConfirmedError
        return observed.hostname, video_ids[0]

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
