import os
import shutil
import subprocess
import time
import webbrowser
from collections.abc import Callable, Sequence

from desktop_agent.browser_preferences import BrowserPreference, BrowserPreferenceSource, PreferredBrowser
from desktop_agent.browser_bridge import BrowserBridgeClient, BrowserBridgeState, browser_kind_for_preference
from desktop_agent.catalog import SUPPORTED_BROWSERS, SUPPORTED_SITES
from desktop_agent.tools.applications import _is_any_process_running


ExecutableFinder = Callable[[str], str | None]
PathChecker = Callable[[str], bool]
ProcessStarter = Callable[[Sequence[str]], None]
SystemOpener = Callable[[str], bool]


def _start_browser(arguments: Sequence[str]) -> None:
    subprocess.Popen(list(arguments), close_fds=True)


class PreferredBrowserOpener:
    """Abre URLs en un navegador local elegido, sin shell ni texto ejecutable."""

    def __init__(
        self,
        preferences: BrowserPreferenceSource,
        *,
        system_opener: SystemOpener = webbrowser.open_new_tab,
        finder: ExecutableFinder = shutil.which,
        path_checker: PathChecker = os.path.isfile,
        starter: ProcessStarter = _start_browser,
        bridge: BrowserBridgeClient | None = None,
        process_checker: Callable[[tuple[str, ...]], bool] = _is_any_process_running,
        waiter: Callable[[float], None] = time.sleep,
    ) -> None:
        if not isinstance(preferences, BrowserPreferenceSource):
            raise TypeError("El abridor requiere preferencias locales.")
        self._preferences = preferences
        self._system_opener = system_opener
        self._finder = finder
        self._path_checker = path_checker
        self._starter = starter
        self._bridge = bridge
        self._process_checker = process_checker
        self._waiter = waiter

    def ensure_current_session(self, preference: BrowserPreference | None = None) -> bool:
        """Arranca sólo el navegador elegido y espera su extensión, sin fallback."""
        selected = preference or self._preferences.load()
        if not selected.use_current_session or self._bridge is None:
            return False
        expected = browser_kind_for_preference(selected.browser)
        snapshot = self._bridge.snapshot
        if snapshot.state is BrowserBridgeState.CONNECTED:
            return snapshot.browser is expected
        if snapshot.state is not BrowserBridgeState.WAITING:
            return False
        names = ("chrome.exe",) if selected.browser is PreferredBrowser.CHROME else ("opera.exe",)
        try:
            if not self._process_checker(names):
                browser = SUPPORTED_BROWSERS[selected.browser.value]
                executable = self._resolve(browser.windows_paths, browser.executable_names)
                if executable is None:
                    return False
                self._starter((executable,))
            for _ in range(50):
                snapshot = self._bridge.snapshot
                if snapshot.state is BrowserBridgeState.CONNECTED:
                    return snapshot.browser is expected
                if snapshot.state is not BrowserBridgeState.WAITING:
                    return False
                self._waiter(.1)
        except (OSError, ValueError):
            return False
        return False

    def __call__(self, url: str) -> bool:
        preference = self._preferences.load()
        if preference.browser is PreferredBrowser.SYSTEM_DEFAULT:
            return bool(self._system_opener(url))
        if preference.use_current_session:
            if not self.ensure_current_session(preference):
                return False
            site_key = next(
                (
                    key
                    for key, site in SUPPORTED_SITES.items()
                    if site.url == url
                ),
                None,
            )
            if site_key is None:
                return False
            return self._bridge.open_site(site_key, preference.browser)
        browser = SUPPORTED_BROWSERS[preference.browser.value]
        executable = self._resolve(browser.windows_paths, browser.executable_names)
        if executable is None:
            return False
        try:
            self._starter((executable, url))
        except OSError:
            return False
        return True

    def _resolve(
        self,
        paths: tuple[str, ...],
        names: tuple[str, ...],
    ) -> str | None:
        for template in paths:
            candidate = os.path.expandvars(template)
            if self._path_checker(candidate):
                return candidate
        for name in names:
            candidate = self._finder(name)
            if candidate is not None:
                return candidate
        return None
