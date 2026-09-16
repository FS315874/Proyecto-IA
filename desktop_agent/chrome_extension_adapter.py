import logging

from desktop_agent.browser_adapter import BrowserSecurityPolicy, SafeBrowserAdapter
from desktop_agent.browser_bridge import (
    BrowserBridgeError,
    BrowserBridgeClient,
    BrowserBridgeOperation,
    BrowserKind,
)
from desktop_agent.browser_contract import (
    BrowserAdapterDependencies,
    BrowserDomUnavailableError,
    BrowserNoResultsError,
    BrowserContentUnavailableError,
    BrowserPlaybackNotConfirmedError,
    BrowserConsentRequiredError,
    BrowserLimits,
    PageSnapshot,
    PlaybackSnapshot,
    SearchSnapshot,
)


class _NoOpResource:
    def close(self) -> None:
        pass


class _ExtensionPlaybackResource:
    def __init__(
        self,
        bridge: BrowserBridgeClient,
        browser: BrowserKind,
    ) -> None:
        self._bridge = bridge
        self._browser = browser
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        response = self._bridge.request(
            BrowserBridgeOperation.YOUTUBE_STOP,
            {},
            self._browser,
        )
        if (not response.success or not isinstance(response.payload, dict)
                or response.payload.get("paused") is not True):
            raise OSError("La extensión no pudo detener la reproducción.")
        self._closed = True


class ChromeExtensionPage:
    """Traduce el contrato semántico a operaciones fijas de la extensión."""

    def __init__(
        self,
        bridge: BrowserBridgeClient,
        expected_browser: BrowserKind,
    ) -> None:
        if not isinstance(bridge, BrowserBridgeClient):
            raise TypeError("El adaptador requiere un puente local.")
        if not isinstance(expected_browser, BrowserKind):
            raise TypeError("El adaptador requiere un navegador conocido.")
        self._bridge = bridge
        self._browser = expected_browser

    def open_site(self, canonical_url: str, timeout_seconds: float) -> PageSnapshot:
        if canonical_url != "https://www.youtube.com/":
            raise BrowserDomUnavailableError
        payload = self._request(
            BrowserBridgeOperation.OPEN_SITE,
            {"site_key": "youtube"},
        )
        return self._page(payload)

    def search(self, query: str, timeout_seconds: float) -> SearchSnapshot:
        payload = self._request(
            BrowserBridgeOperation.YOUTUBE_SEARCH,
            {"query": query},
        )
        count = payload.get("result_count")
        if type(count) is not int or count < 0:
            raise BrowserDomUnavailableError
        return SearchSnapshot(self._page(payload), count)

    def select_first_result(self, timeout_seconds: float) -> PageSnapshot:
        return self._page(
            self._request(BrowserBridgeOperation.YOUTUBE_SELECT_FIRST, {})
        )

    def start_playback(self, timeout_seconds: float) -> PlaybackSnapshot:
        return self._playback(
            self._request(BrowserBridgeOperation.YOUTUBE_START, {})
        )

    def read_playback(self) -> PlaybackSnapshot:
        return self._playback(
            self._request(BrowserBridgeOperation.YOUTUBE_READ, {})
        )

    def _request(
        self,
        operation: BrowserBridgeOperation,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        try:
            response = self._bridge.request(operation, arguments, self._browser)
        except BrowserBridgeError as error:
            raise BrowserDomUnavailableError from error
        if not response.success:
            # Códigos locales cerrados: no se expone texto libre recibido del worker.
            condition = {
                "no_results": BrowserNoResultsError,
                "video_unavailable": BrowserContentUnavailableError,
                "consent_required": BrowserConsentRequiredError,
                "navigation_timeout": TimeoutError,
                "playback_timeout": TimeoutError,
                "playback_not_started": BrowserPlaybackNotConfirmedError,
                "tab_muted": BrowserPlaybackNotConfirmedError,
            }.get(response.error_code, BrowserDomUnavailableError)
            raise condition()
        if response.payload is None:
            raise BrowserDomUnavailableError
        return response.payload

    @staticmethod
    def _page(payload: dict[str, object]) -> PageSnapshot:
        try:
            return PageSnapshot(payload["url"], payload["title"])
        except (KeyError, TypeError, ValueError) as error:
            raise BrowserDomUnavailableError from error

    def _playback(self, payload: dict[str, object]) -> PlaybackSnapshot:
        required = {"url", "title", "paused", "muted", "volume", "current_time"}
        if set(payload) != required:
            raise BrowserDomUnavailableError
        try:
            return PlaybackSnapshot(
                PageSnapshot(payload["url"], payload["title"]),
                payload["paused"],
                payload["muted"],
                payload["volume"],
                payload["current_time"],
            )
        except (TypeError, ValueError) as error:
            raise BrowserDomUnavailableError from error


def create_youtube_extension_adapter(
    bridge: BrowserBridgeClient,
    expected_browser: BrowserKind,
    logger: logging.Logger,
    *,
    limits: BrowserLimits = BrowserLimits(),
) -> SafeBrowserAdapter:
    session = _ExtensionPlaybackResource(bridge, expected_browser)
    page = ChromeExtensionPage(bridge, expected_browser)
    return SafeBrowserAdapter(
        BrowserAdapterDependencies(session, _NoOpResource(), page),
        logger,
        limits,
        BrowserSecurityPolicy(
            allow_extensions=True,
            persistent_profile=True,
        ),
    )
