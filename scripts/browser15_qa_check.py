"""Aceptación simulada de v0.15 sin abrir navegador ni modificar Windows."""

import logging
import tempfile
from pathlib import Path

from desktop_agent.browser_bridge import (
    BrowserBridgeOperation,
    BrowserBridgeResponse,
    BrowserBridgeSnapshot,
    BrowserBridgeState,
    BrowserKind,
)
from desktop_agent.browser_preferences import (
    BrowserPreference,
    BrowserPreferenceStore,
    PreferredBrowser,
)
from desktop_agent.browser_runtime import PreferredYouTubeAdapterFactory
from desktop_agent.parser import parse_command
from desktop_agent.preferred_browser import PreferredBrowserOpener


class _Bridge:
    def __init__(self) -> None:
        self.sites: list[str] = []

    @property
    def snapshot(self) -> BrowserBridgeSnapshot:
        return BrowserBridgeSnapshot(
            BrowserBridgeState.CONNECTED,
            BrowserKind.OPERA_GX,
        )

    def request(
        self,
        operation: BrowserBridgeOperation,
        arguments: dict[str, object],
        expected_browser: BrowserKind,
    ) -> BrowserBridgeResponse:
        return BrowserBridgeResponse("qa", False, None, "simulated")

    def open_site(self, site_key: str, browser: PreferredBrowser) -> bool:
        if browser is not PreferredBrowser.OPERA_GX:
            return False
        self.sites.append(site_key)
        return True


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        store = BrowserPreferenceStore(Path(directory) / "browser.json")
        preference = BrowserPreference(PreferredBrowser.OPERA_GX, True)
        store.save(preference)
        bridge = _Bridge()
        opener = PreferredBrowserOpener(
            store,
            bridge=bridge,
            path_checker=lambda _path: False,
            finder=lambda _name: None,
        )
        extension_factory = PreferredYouTubeAdapterFactory(
            store,
            bridge,
            logging.getLogger("browser15-qa"),
            isolated_factory=lambda: None,
        )

        spotify_web = parse_command("abrí spotify")
        spotify_app = parse_command("abrí spotify app")
        if (
            spotify_web is None
            or spotify_web.tool_name != "open_url"
            or spotify_app is None
            or spotify_app.tool_name != "open_application"
            or not opener("https://open.spotify.com/")
            or bridge.sites != ["spotify"]
            or not extension_factory().policy.persistent_profile
            or store.load() != preference
        ):
            print("BROWSER15_QA_FAILED")
            return 1
    print(
        "BROWSER15_QA_OK: Spotify web/app, preferencia Opera GX y selección "
        "de sesión habitual verificadas sin efectos externos."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
