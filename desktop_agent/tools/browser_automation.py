import logging
import time
from collections.abc import Callable

from desktop_agent.browser_contract import BrowserAdapter, BrowserStepStatus
from desktop_agent.catalog import SUPPORTED_SITES
from desktop_agent.models import Intent, ToolResult

AdapterFactory = Callable[[], BrowserAdapter]
Clock = Callable[[], float]


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
