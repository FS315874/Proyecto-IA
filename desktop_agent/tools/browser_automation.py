import logging
import time
from collections.abc import Callable

from desktop_agent.browser_contract import (
    BrowserAdapter,
    BrowserStepStatus,
    BrowserStepResult,
    ResumableBrowserAdapter,
    normalize_search_query,
)
from desktop_agent.catalog import SUPPORTED_SITES
from desktop_agent.browser_bridge import BrowserBridgeError
from desktop_agent.models import Intent, ToolResult
from desktop_agent.diagnostics import DEFINITIONS, emit_failure

AdapterFactory = Callable[[], BrowserAdapter]
ManagedSessionFactory = Callable[[], BrowserAdapter | None]
Clock = Callable[[], float]


class _PendingBrowserCleanup(RuntimeError):
    """No se inicia otro recorrido hasta cerrar los recursos anteriores."""


class BrowserNavigationTool:
    """Herramienta registrable que garantiza el cierre de una sesión semántica."""

    def __init__(
        self,
        adapter_factory: AdapterFactory,
        logger: logging.Logger,
        clock: Clock = time.perf_counter,
    ) -> None:
        if not callable(adapter_factory):
            raise TypeError("La fábrica del adaptador debe ser invocable.")
        if not isinstance(logger, logging.Logger):
            raise TypeError("El logger de la herramienta no es válido.")
        if not callable(clock):
            raise TypeError("El reloj de la herramienta debe ser invocable.")
        self._adapter_factory = adapter_factory
        self._logger = logger
        self._clock = clock

    def __call__(self, site_key: str) -> ToolResult:
        started_at = self._clock()
        safe_destination = (
            site_key
            if isinstance(site_key, str) and site_key in SUPPORTED_SITES
            else "unapproved"
        )
        self._logger.info(
            "Browser tool: intent=%s destination=%s",
            Intent.BROWSER_NAVIGATION.value,
            safe_destination,
        )

        adapter: BrowserAdapter | None = None
        open_success = False
        close_success = False
        try:
            candidate = self._adapter_factory()
            if not isinstance(candidate, BrowserAdapter):
                raise TypeError("invalid adapter")
            adapter = candidate
            open_result = adapter.open_site(site_key)
            open_success = open_result.status is BrowserStepStatus.SUCCESS
        except Exception:
            open_success = False
        finally:
            if adapter is not None:
                try:
                    close_result = adapter.close()
                    close_success = (
                        close_result.status is BrowserStepStatus.SUCCESS
                    )
                except Exception:
                    close_success = False

        success = open_success and close_success
        duration_ms = max(0.0, (self._clock() - started_at) * 1000)
        self._logger.info(
            "Browser tool: destination=%s status=%s duration_ms=%.3f",
            safe_destination,
            "success" if success else "failure",
            duration_ms,
        )

        if not success:
            return ToolResult(
                False,
                "La navegación segura no pudo completarse.",
            )
        site = SUPPORTED_SITES[site_key]
        return ToolResult(
            True,
            f"Navegación segura comprobada para {site.name}.",
        )


class YouTubePlaybackTool:
    """Mantiene una reproducción verificada hasta recibir una detención explícita."""

    def __init__(
        self,
        adapter_factory: AdapterFactory,
        logger: logging.Logger,
        clock: Clock = time.perf_counter,
        session_key: Callable[[], object] = lambda: None,
        managed_session_factory: ManagedSessionFactory | None = None,
    ) -> None:
        if not callable(adapter_factory):
            raise TypeError("La fábrica del adaptador debe ser invocable.")
        if not isinstance(logger, logging.Logger):
            raise TypeError("El logger de la herramienta no es válido.")
        if not callable(clock):
            raise TypeError("El reloj de la herramienta debe ser invocable.")
        if managed_session_factory is not None and not callable(
            managed_session_factory
        ):
            raise TypeError("La fábrica de sesión gestionada debe ser invocable.")
        self._adapter_factory = adapter_factory
        self._logger = logger
        self._clock = clock
        self._active_adapter: BrowserAdapter | None = None
        self._session_key = session_key
        self._managed_session_factory = managed_session_factory
        self._active_session_key: object = None
        self._stop_failed = False

    @property
    def has_active_session(self) -> bool:
        """Incluye recursos cuya detención aún no pudo confirmarse."""
        return self._active_adapter is not None

    def __call__(self, query: str) -> ToolResult:
        started_at = self._clock()
        if self._stop_failed:
            return ToolResult(False, "La detención anterior no se confirmó. Volvé a pulsar Detener antes de iniciar otra reproducción.",
                              error_code="browser_stop_failed", error_stage="close")
        self._logger.info(
            "Browser tool: intent=%s destination=youtube",
            Intent.BROWSER_NAVIGATION.value,
        )

        try:
            normalized_query = normalize_search_query(query)
        except ValueError:
            return ToolResult(
                False,
                "No se pudo verificar la reproducción segura en YouTube.",
                error_code="browser_invalid_input",
                error_stage="search",
            )

        adapter = None
        current_key: object = None
        flow_success = False
        failure_message = "No se pudo verificar la reproducción segura en YouTube."
        failure_code = "browser_failed"
        failure_stage = "open_site"
        try:
            current_key = self._session_key()
            if self._active_adapter is not None and current_key != self._active_session_key:
                if not self.stop().success:
                    return ToolResult(False, "No se pudo detener la sesión anterior al cambiar de navegador.",
                                      error_code="browser_stop_failed", error_stage="close")
            adapter = self._reuse_active_adapter()
            if adapter is None:
                candidate = self._adapter_factory()
                if not isinstance(candidate, BrowserAdapter):
                    raise TypeError("invalid adapter")
                adapter = candidate

            steps = (
                lambda: adapter.open_site("youtube"),
                lambda: adapter.search(normalized_query),
                adapter.select_first_result,
                adapter.start_playback,
                adapter.verify_playback,
            )
            for step in steps:
                step_result = step()
                failure_stage = step_result.operation.value.lower()
                if step_result.status is not BrowserStepStatus.SUCCESS:
                    if step_result.error is not None:
                        failure_code = "browser_" + step_result.error.code.value
                    definition = DEFINITIONS.get(failure_code, DEFINITIONS["browser_failed"])
                    failure_message = f"{definition.summary} (paso: {failure_stage}). {definition.next_check}"
                    break
            else:
                flow_success = True
                self._active_adapter = adapter
                self._active_session_key = current_key
                adapter = None
        except _PendingBrowserCleanup:
            failure_code = "browser_stop_failed"
            failure_stage = "close"
            failure_message = "No se confirmó el cierre anterior. Volvé a pulsar Detener; no se inició otra sesión."
        except BrowserBridgeError:
            failure_code = "browser_disconnected"
            failure_message = (
                "La extensión no está conectada al navegador elegido. Abrí ese navegador, "
                "recargá Desktop Agent Browser Bridge en su lista de extensiones y "
                "esperá a que Sesión web indique conectada. No se usó otro navegador."
            )
        except Exception:
            flow_success = False
        finally:
            if adapter is not None:
                try:
                    closed = adapter.close().status is BrowserStepStatus.SUCCESS
                except Exception:
                    closed = False
                if not closed:
                    self._active_adapter = adapter
                    self._active_session_key = current_key
                    self._stop_failed = True
                    failure_message += " Tampoco se confirmó la detención; volvé a pulsar Detener."
                    emit_failure(self._logger, "browser_stop_failed", "executor", stage="close", tool="stop_youtube")

        duration_ms = max(0.0, (self._clock() - started_at) * 1000)
        self._logger.info(
            "Browser tool: destination=youtube status=%s duration_ms=%.3f",
            "active" if flow_success else "failure",
            duration_ms,
        )

        if not flow_success:
            return ToolResult(
                False,
                failure_message,
                error_code=failure_code,
                error_stage=failure_stage,
            )
        return ToolResult(
            True,
            "La reproducción segura se verificó y permanece activa en YouTube.",
        )

    def _reuse_active_adapter(self) -> BrowserAdapter | None:
        adapter = self._active_adapter
        self._active_adapter = None
        if adapter is None:
            return None
        try:
            result = adapter.reset()
        except Exception:
            result = None
        if result is not None and result.status is BrowserStepStatus.SUCCESS:
            self._logger.info(
                "Browser tool: destination=youtube operation=reuse status=success"
            )
            return adapter
        try:
            closed = adapter.close().status is BrowserStepStatus.SUCCESS
        except Exception:
            closed = False
        if not closed:
            self._active_adapter = adapter
            self._stop_failed = True
            raise _PendingBrowserCleanup()
        self._logger.info(
            "Browser tool: destination=youtube operation=reuse status=failure"
        )
        return None

    def stop(self) -> ToolResult:
        """Detiene la sesión activa y libera sus recursos de forma idempotente."""

        if self._active_adapter is None:
            return self._stop_managed_session()
        if self._close_active_session():
            return ToolResult(True, "La reproducción de YouTube se detuvo.")
        return ToolResult(
            False,
            "No se pudo cerrar completamente la reproducción de YouTube.",
            error_code="browser_stop_failed",
            error_stage="close",
        )

    def resume(self) -> ToolResult:
        """Reanuda y verifica el video actual sin realizar una búsqueda nueva."""

        if self._stop_failed:
            return ToolResult(
                False,
                "La detención anterior no se confirmó. Volvé a pulsar Detener "
                "antes de reanudar.",
                error_code="browser_stop_failed",
                error_stage="close",
            )

        adapter = self._active_adapter
        if adapter is None:
            if self._managed_session_factory is None:
                return ToolResult(
                    False,
                    "No hay una reproducción gestionada de YouTube para reanudar.",
                    error_code="browser_content_unavailable",
                    error_stage="start_playback",
                )
            try:
                adapter = self._managed_session_factory()
            except BrowserBridgeError:
                return ToolResult(
                    False,
                    "No se pudo consultar la pestaña gestionada porque la "
                    "extensión no está conectada al navegador elegido.",
                    error_code="browser_disconnected",
                    error_stage="start_playback",
                )
            except Exception:
                return ToolResult(
                    False,
                    "No se pudo preparar la reanudación de YouTube.",
                    error_code="browser_failed",
                    error_stage="start_playback",
                )
            if adapter is None:
                return ToolResult(
                    False,
                    "Activá la pestaña gestionada para reanudar un video de "
                    "YouTube ya abierto.",
                    error_code="browser_content_unavailable",
                    error_stage="start_playback",
                )

        if not isinstance(adapter, ResumableBrowserAdapter):
            return ToolResult(
                False,
                "La sesión actual no admite reanudar el reproductor.",
                error_code="browser_invalid_state",
                error_stage="start_playback",
            )

        self._active_adapter = adapter
        try:
            self._active_session_key = self._session_key()
        except Exception:
            self._active_session_key = None

        start_result = adapter.resume_current_playback()
        if start_result.status is not BrowserStepStatus.SUCCESS:
            return self._failed_resume(start_result)
        verification = adapter.verify_playback()
        if verification.status is not BrowserStepStatus.SUCCESS:
            return self._failed_resume(verification)
        self._stop_failed = False
        return ToolResult(
            True,
            "La reproducción actual de YouTube se reanudó y fue verificada.",
        )

    def _failed_resume(self, result: BrowserStepResult) -> ToolResult:
        code = "browser_failed"
        if result.error is not None:
            code = "browser_" + result.error.code.value
        definition = DEFINITIONS.get(code, DEFINITIONS["browser_failed"])
        message = (
            f"{definition.summary} (paso: {result.operation.value.lower()}). "
            f"{definition.next_check}"
        )
        if not self._close_active_session():
            message += " Tampoco se confirmó la detención; volvé a pulsar Detener."
            emit_failure(
                self._logger,
                "browser_stop_failed",
                "executor",
                stage="close",
                tool="stop_youtube",
            )
        return ToolResult(
            False,
            message,
            error_code=code,
            error_stage=result.operation.value.lower(),
        )

    def _stop_managed_session(self) -> ToolResult:
        if self._managed_session_factory is None:
            return ToolResult(True, "No había una reproducción activa en YouTube.")
        try:
            candidate = self._managed_session_factory()
            if candidate is None:
                return ToolResult(
                    True,
                    "No había una reproducción activa en YouTube.",
                )
            if not isinstance(candidate, BrowserAdapter):
                raise TypeError("invalid adapter")
            self._active_adapter = candidate
        except BrowserBridgeError:
            return ToolResult(
                False,
                "No se pudo consultar la pestaña gestionada porque la extensión "
                "no está conectada al navegador elegido.",
                error_code="browser_disconnected",
                error_stage="close",
            )
        except Exception:
            return ToolResult(
                False,
                "No se pudo preparar la detención de YouTube.",
                error_code="browser_stop_failed",
                error_stage="close",
            )

        if self._close_active_session():
            return ToolResult(
                True,
                "La pestaña gestionada de YouTube quedó detenida.",
            )
        return ToolResult(
            False,
            "No se pudo confirmar la detención en la pestaña gestionada de YouTube.",
            error_code="browser_stop_failed",
            error_stage="close",
        )

    def _close_active_session(self) -> bool:
        started_at = self._clock()
        adapter = self._active_adapter
        success = False
        try:
            if adapter is not None:
                result = adapter.close()
                success = result.status is BrowserStepStatus.SUCCESS
        except Exception:
            success = False
        self._stop_failed = not success
        if success:
            self._active_adapter = None

        duration_ms = max(0.0, (self._clock() - started_at) * 1000)
        self._logger.info(
            "Browser tool: destination=youtube operation=stop "
            "status=%s duration_ms=%.3f",
            "success" if success else "failure",
            duration_ms,
        )
        return success
