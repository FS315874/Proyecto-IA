import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from desktop_agent.browser_bridge import (
    BrowserBridgeClient,
    BrowserBridgeError,
    BrowserBridgeSnapshot,
    BrowserBridgeState,
    NativeBrowserBridge,
    browser_kind_for_preference,
)
from desktop_agent.browser_preferences import (
    BrowserPreference,
    BrowserPreferenceSource,
    BrowserPreferenceStore,
)
from desktop_agent.chrome_extension_adapter import (
    create_youtube_extension_adapter,
)
from desktop_agent.local_controller import AlreadyRunningError, SingleInstanceLock
from desktop_agent.playwright_backend import create_youtube_playwright_adapter


class BrowserRuntimeLock(Protocol):
    def acquire(self) -> None: ...

    def close(self) -> None: ...


def default_browser_runtime_lock_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / "DesktopAgent" / "browser_bridge.lock"


class PreferredYouTubeAdapterFactory:
    def __init__(
        self,
        preferences: BrowserPreferenceSource,
        bridge: BrowserBridgeClient | None,
        logger: logging.Logger,
        isolated_factory: Callable[[], object] | None = None,
        session_connector: Callable[[BrowserPreference], bool] | None = None,
    ) -> None:
        if not isinstance(preferences, BrowserPreferenceSource):
            raise TypeError("La fábrica requiere preferencias de navegador.")
        if bridge is not None and not isinstance(bridge, BrowserBridgeClient):
            raise TypeError("El puente del navegador no cumple el contrato.")
        self._preferences = preferences
        self._bridge = bridge
        self._logger = logger
        self._session_connector = session_connector
        self._isolated_factory = isolated_factory or (
            lambda: create_youtube_playwright_adapter(logger)
        )

    def current_session_adapter(self, *, connect: bool = False):
        """Obtiene sólo la sesión gestionada, sin abrir un navegador por defecto."""

        preference = self._preferences.load()
        if not preference.use_current_session:
            return None
        expected_browser = browser_kind_for_preference(preference.browser)
        if (
            connect
            and self._session_connector is not None
            and not self._session_connector(preference)
        ):
            raise BrowserBridgeError("La extensión del navegador no está disponible.")
        if (
            self._bridge is None
            or self._bridge.snapshot.state is not BrowserBridgeState.CONNECTED
            or self._bridge.snapshot.browser is not expected_browser
        ):
            raise BrowserBridgeError(
                "La extensión del navegador no está disponible."
            )
        return create_youtube_extension_adapter(
            self._bridge,
            expected_browser,
            self._logger,
        )

    def __call__(self):
        adapter = self.current_session_adapter(connect=True)
        if adapter is not None:
            return adapter
        return self._isolated_factory()


class BrowserRuntime:
    """Posee preferencias y puente; la UI sólo observa y selecciona."""

    def __init__(
        self,
        preferences: BrowserPreferenceStore | None = None,
        bridge: NativeBrowserBridge | None = None,
        bridge_lock: BrowserRuntimeLock | None = None,
    ) -> None:
        self.preferences = preferences or BrowserPreferenceStore()
        self.bridge = bridge or NativeBrowserBridge()
        self._bridge_lock = bridge_lock or SingleInstanceLock(
            default_browser_runtime_lock_path()
        )
        self._start_error = False
        self._started = False

    @property
    def snapshot(self) -> BrowserBridgeSnapshot:
        if self._start_error:
            return BrowserBridgeSnapshot(BrowserBridgeState.ERROR, None)
        return self.bridge.snapshot

    def start(self) -> None:
        if self._started or self._start_error:
            return
        try:
            self._bridge_lock.acquire()
            self.bridge.start()
        except (AlreadyRunningError, BrowserBridgeError, OSError):
            self._start_error = True
            self._bridge_lock.close()
            return
        self._started = True

    def close(self) -> None:
        if not self._started:
            return
        self._started = False
        try:
            self.bridge.close()
        finally:
            self._bridge_lock.close()
