import logging
import math
import re
import time
from collections.abc import Callable
from typing import Protocol, TypeVar
from urllib.parse import parse_qs, urlsplit, urlunsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from desktop_agent.browser_adapter import BrowserSecurityPolicy, SafeBrowserAdapter
from desktop_agent.browser_contract import (
    BrowserAdapterDependencies,
    BrowserConsentRequiredError,
    BrowserDomUnavailableError,
    BrowserLimits,
    BrowserNoResultsError,
    Clock,
    PageSnapshot,
    PlaybackSnapshot,
    SearchSnapshot,
    Wait,
)
from desktop_agent.catalog import SUPPORTED_SITES

YOUTUBE_CANONICAL_URL = SUPPORTED_SITES["youtube"].url
YOUTUBE_ALLOWED_HOST = "www.youtube.com"
YOUTUBE_CONSENT_HOST = "consent.youtube.com"
YOUTUBE_RESULTS_URL = "https://www.youtube.com/results*"
YOUTUBE_WATCH_URL = "https://www.youtube.com/watch*"

CONSENT_SELECTOR = (
    "form[action*='consent.youtube.com'], "
    "iframe[src*='consent.youtube.com'], "
    "ytd-consent-bump-v2-lightbox"
)
SEARCH_INPUT_SELECTORS = (
    "input[name='search_query']",
    "ytd-searchbox input#search",
)
VIDEO_RESULT_SELECTOR = "ytd-video-renderer a#video-title[href*='/watch']"
EMPTY_RESULTS_SELECTOR = (
    "ytd-search ytd-message-renderer, "
    "ytd-search ytd-background-promo-renderer"
)
RESULTS_READY_SELECTOR = f"{VIDEO_RESULT_SELECTOR}, {EMPTY_RESULTS_SELECTOR}"
VIDEO_SELECTOR = "video.html5-main-video"
PLAY_BUTTON_SELECTOR = "button.ytp-play-button"
MUTE_BUTTON_SELECTOR = "button.ytp-mute-button"
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,32}$")

PLAYBACK_STATE_EXPRESSION = """element => ({
    paused: element.paused,
    muted: element.muted,
    volume: Number.isFinite(element.volume) ? element.volume : null,
    currentTime: Number.isFinite(element.currentTime) ? element.currentTime : 0
})"""


class LocatorPort(Protocol):
    @property
    def first(self) -> "LocatorPort": ...

    def nth(self, index: int) -> "LocatorPort": ...

    def count(self) -> int: ...

    def is_visible(self, *, timeout: float | None = None) -> bool: ...

    def wait_for(
        self,
        *,
        timeout: float | None = None,
        state: str | None = None,
    ) -> None: ...

    def fill(self, value: str, *, timeout: float | None = None) -> None: ...

    def press(self, key: str, *, timeout: float | None = None) -> None: ...

    def get_attribute(
        self,
        name: str,
        *,
        timeout: float | None = None,
    ) -> str | None: ...

    def click(self, *, timeout: float | None = None) -> None: ...

    def evaluate(
        self,
        expression: str,
        *,
        timeout: float | None = None,
    ) -> object: ...


class PagePort(Protocol):
    @property
    def url(self) -> str: ...

    def title(self) -> str: ...

    def goto(
        self,
        url: str,
        *,
        timeout: float | None = None,
        wait_until: str | None = None,
    ) -> object: ...

    def locator(self, selector: str) -> LocatorPort: ...

    def wait_for_url(
        self,
        url: str,
        *,
        timeout: float | None = None,
        wait_until: str | None = None,
    ) -> None: ...


class BrowserTypePort(Protocol):
    def launch(self, **kwargs: object) -> "BrowserPort": ...


class BrowserPort(Protocol):
    def new_context(self, **kwargs: object) -> "ContextPort": ...

    def close(self) -> None: ...


class ContextPort(Protocol):
    def new_page(self) -> PagePort: ...

    def close(self) -> None: ...


class PlaywrightRuntimePort(Protocol):
    @property
    def chromium(self) -> BrowserTypePort: ...

    def stop(self) -> None: ...


RuntimeStarter = Callable[[], PlaywrightRuntimePort]
T = TypeVar("T")


class YouTubePlaywrightPage:
    """Implementa el puerto semántico con selectores fijos y datos validados."""

    def __init__(
        self,
        page: PagePort,
        observation_timeout_seconds: float,
    ) -> None:
        if not self._valid_timeout(observation_timeout_seconds):
            raise ValueError("El timeout de observación no es válido.")
        self._page = page
        self._observation_timeout_seconds = float(observation_timeout_seconds)

    def open_site(
        self,
        canonical_url: str,
        timeout_seconds: float,
    ) -> PageSnapshot:
        def operation() -> PageSnapshot:
            if canonical_url != YOUTUBE_CANONICAL_URL:
                raise BrowserDomUnavailableError
            timeout_ms = self._timeout_ms(timeout_seconds)
            self._page.goto(
                canonical_url,
                timeout=timeout_ms,
                wait_until="domcontentloaded",
            )
            self._raise_if_consent_required()
            return self._snapshot("/")

        return self._translate_errors(operation)

    def search(self, query: str, timeout_seconds: float) -> SearchSnapshot:
        def operation() -> SearchSnapshot:
            timeout_ms = self._timeout_ms(timeout_seconds)
            self._raise_if_consent_required()
            search_input = self._first_available(SEARCH_INPUT_SELECTORS)
            search_input.fill(query, timeout=timeout_ms)
            search_input.press("Enter", timeout=timeout_ms)
            self._page.wait_for_url(
                YOUTUBE_RESULTS_URL,
                timeout=timeout_ms,
                wait_until="domcontentloaded",
            )
            try:
                self._page.locator(RESULTS_READY_SELECTOR).first.wait_for(
                    timeout=timeout_ms,
                    state="attached",
                )
            except (TimeoutError, PlaywrightTimeoutError):
                self._raise_if_consent_required()
                raise

            self._raise_if_consent_required()
            result_count = self._page.locator(VIDEO_RESULT_SELECTOR).count()
            if result_count == 0 and not self._has_empty_results_marker():
                raise BrowserDomUnavailableError
            return SearchSnapshot(self._snapshot("/results"), result_count)

        return self._translate_errors(operation)

    def select_first_result(self, timeout_seconds: float) -> PageSnapshot:
        def operation() -> PageSnapshot:
            timeout_ms = self._timeout_ms(timeout_seconds)
            self._raise_if_consent_required()
            results = self._page.locator(VIDEO_RESULT_SELECTOR)
            if results.count() == 0:
                if self._has_empty_results_marker():
                    raise BrowserNoResultsError
                raise BrowserDomUnavailableError

            watch_url: str | None = None
            for index in range(results.count()):
                href = results.nth(index).get_attribute(
                    "href",
                    timeout=timeout_ms,
                )
                try:
                    watch_url = self._canonical_watch_url(href)
                except BrowserDomUnavailableError:
                    continue
                break
            if watch_url is None:
                raise BrowserDomUnavailableError
            self._page.goto(
                watch_url,
                timeout=timeout_ms,
                wait_until="domcontentloaded",
            )
            self._page.wait_for_url(
                YOUTUBE_WATCH_URL,
                timeout=timeout_ms,
                wait_until="domcontentloaded",
            )
            self._raise_if_consent_required()
            return self._snapshot("/watch")

        return self._translate_errors(operation)

    def start_playback(self, timeout_seconds: float) -> PlaybackSnapshot:
        def operation() -> PlaybackSnapshot:
            timeout_ms = self._timeout_ms(timeout_seconds)
            self._raise_if_consent_required()
            video = self._page.locator(VIDEO_SELECTOR).first
            video.wait_for(timeout=timeout_ms, state="attached")
            playback = self._read_playback(video, timeout_ms)

            if playback.paused:
                play_button = self._required_locator(PLAY_BUTTON_SELECTOR)
                play_button.click(timeout=timeout_ms)
                playback = self._read_playback(video, timeout_ms)

            if playback.muted:
                mute_button = self._required_locator(MUTE_BUTTON_SELECTOR)
                mute_button.click(timeout=timeout_ms)
                playback = self._read_playback(video, timeout_ms)

            if playback.paused or playback.muted:
                raise BrowserDomUnavailableError
            return playback

        return self._translate_errors(operation)

    def read_playback(self) -> PlaybackSnapshot:
        def operation() -> PlaybackSnapshot:
            timeout_ms = self._timeout_ms(self._observation_timeout_seconds)
            self._raise_if_consent_required()
            video = self._page.locator(VIDEO_SELECTOR).first
            video.wait_for(timeout=timeout_ms, state="attached")
            return self._read_playback(video, timeout_ms)

        return self._translate_errors(operation)

    def _translate_errors(self, operation: Callable[[], T]) -> T:
        try:
            return operation()
        except (
            BrowserConsentRequiredError,
            BrowserNoResultsError,
            BrowserDomUnavailableError,
        ):
            raise
        except (TimeoutError, PlaywrightTimeoutError):
            raise TimeoutError("browser operation timed out") from None
        except Exception:
            raise BrowserDomUnavailableError from None

    def _snapshot(self, expected_path: str) -> PageSnapshot:
        url = self._page.url
        if not isinstance(url, str):
            raise BrowserDomUnavailableError
        parsed = urlsplit(url)
        if parsed.hostname == YOUTUBE_CONSENT_HOST:
            raise BrowserConsentRequiredError
        if (
            parsed.scheme != "https"
            or parsed.hostname != YOUTUBE_ALLOWED_HOST
            or parsed.path != expected_path
        ):
            raise BrowserDomUnavailableError
        title = self._page.title()
        if not isinstance(title, str) or not title.strip():
            raise BrowserDomUnavailableError
        return PageSnapshot(url, title.strip())

    def _raise_if_consent_required(self) -> None:
        current_url = self._page.url
        if isinstance(current_url, str):
            if urlsplit(current_url).hostname == YOUTUBE_CONSENT_HOST:
                raise BrowserConsentRequiredError
        consent = self._page.locator(CONSENT_SELECTOR)
        if consent.count() > 0 and consent.first.is_visible(timeout=250.0):
            raise BrowserConsentRequiredError

    def _first_available(self, selectors: tuple[str, ...]) -> LocatorPort:
        for selector in selectors:
            locator = self._page.locator(selector)
            if locator.count() > 0:
                return locator.first
        raise BrowserDomUnavailableError

    def _required_locator(self, selector: str) -> LocatorPort:
        locator = self._page.locator(selector)
        if locator.count() == 0:
            raise BrowserDomUnavailableError
        return locator.first

    def _has_empty_results_marker(self) -> bool:
        marker = self._page.locator(EMPTY_RESULTS_SELECTOR)
        return marker.count() > 0 and marker.first.is_visible(timeout=250.0)

    def _canonical_watch_url(self, href: str | None) -> str:
        if not isinstance(href, str) or not href:
            raise BrowserDomUnavailableError
        parsed = urlsplit(href)
        if parsed.scheme or parsed.netloc:
            if (
                parsed.scheme != "https"
                or parsed.hostname != YOUTUBE_ALLOWED_HOST
            ):
                raise BrowserDomUnavailableError
        if parsed.path != "/watch":
            raise BrowserDomUnavailableError
        video_ids = parse_qs(parsed.query).get("v", [])
        if len(video_ids) != 1 or not VIDEO_ID_PATTERN.fullmatch(video_ids[0]):
            raise BrowserDomUnavailableError
        return urlunsplit(
            ("https", YOUTUBE_ALLOWED_HOST, "/watch", f"v={video_ids[0]}", "")
        )

    def _read_playback(
        self,
        video: LocatorPort,
        timeout_ms: float,
    ) -> PlaybackSnapshot:
        raw_state = video.evaluate(
            PLAYBACK_STATE_EXPRESSION,
            timeout=timeout_ms,
        )
        if not isinstance(raw_state, dict):
            raise BrowserDomUnavailableError
        paused = raw_state.get("paused")
        muted = raw_state.get("muted")
        volume = raw_state.get("volume")
        current_time = raw_state.get("currentTime")
        if type(paused) is not bool or type(muted) is not bool:
            raise BrowserDomUnavailableError
        if volume is not None and (
            type(volume) not in (int, float)
            or not math.isfinite(float(volume))
        ):
            raise BrowserDomUnavailableError
        if (
            type(current_time) not in (int, float)
            or not math.isfinite(float(current_time))
        ):
            raise BrowserDomUnavailableError
        return PlaybackSnapshot(
            self._snapshot("/watch"),
            paused,
            muted,
            volume,
            current_time,
        )

    @staticmethod
    def _valid_timeout(value: object) -> bool:
        return (
            type(value) in (int, float)
            and math.isfinite(float(value))
            and float(value) > 0
        )

    def _timeout_ms(self, timeout_seconds: float) -> float:
        if not self._valid_timeout(timeout_seconds):
            raise BrowserDomUnavailableError
        return float(timeout_seconds) * 1000


class ManagedPlaywrightBrowser:
    """Cierra el navegador y detiene el runtime, incluso ante un fallo parcial."""

    def __init__(
        self,
        browser: BrowserPort,
        runtime: PlaywrightRuntimePort,
    ) -> None:
        self._browser = browser
        self._runtime = runtime
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        failed = False
        try:
            self._browser.close()
        except Exception:
            failed = True
        try:
            self._runtime.stop()
        except Exception:
            failed = True
        if failed:
            raise RuntimeError("No se pudo cerrar completamente Playwright.")


def _start_playwright() -> PlaywrightRuntimePort:
    return sync_playwright().start()


def create_youtube_playwright_adapter(
    logger: logging.Logger,
    *,
    limits: BrowserLimits = BrowserLimits(),
    policy: BrowserSecurityPolicy = BrowserSecurityPolicy(),
    headless: bool = False,
    runtime_starter: RuntimeStarter = _start_playwright,
    clock: Clock = time.perf_counter,
    wait: Wait = time.sleep,
) -> SafeBrowserAdapter:
    """Crea una sesión efímera; invocar esta función sí inicia Chromium."""

    if not isinstance(logger, logging.Logger):
        raise TypeError("El logger del navegador no es válido.")
    if not isinstance(limits, BrowserLimits):
        raise TypeError("Los límites del navegador no son válidos.")
    if not isinstance(policy, BrowserSecurityPolicy):
        raise TypeError("La política del navegador no es válida.")
    if type(headless) is not bool:
        raise TypeError("El modo del navegador no es válido.")
    if not callable(runtime_starter):
        raise TypeError("El iniciador de Playwright no es válido.")
    if not callable(clock) or not callable(wait):
        raise TypeError("El reloj y la espera deben ser invocables.")

    runtime: PlaywrightRuntimePort | None = None
    browser: BrowserPort | None = None
    context: ContextPort | None = None
    try:
        runtime = runtime_starter()
        browser = runtime.chromium.launch(
            headless=headless,
            timeout=limits.navigation_timeout_seconds * 1000,
        )
        context = browser.new_context(
            accept_downloads=False,
            permissions=[],
            service_workers="block",
            ignore_https_errors=False,
        )
        raw_page = context.new_page()
        managed_browser = ManagedPlaywrightBrowser(browser, runtime)
        page = YouTubePlaywrightPage(
            raw_page,
            limits.operation_timeout_seconds,
        )
        dependencies = BrowserAdapterDependencies(
            managed_browser,
            context,
            page,
            clock=clock,
            wait=wait,
        )
        return SafeBrowserAdapter(
            dependencies,
            logger,
            limits=limits,
            policy=policy,
        )
    except Exception:
        _cleanup_failed_launch(context, browser, runtime)
        raise RuntimeError("No se pudo preparar el navegador aislado.") from None


def _cleanup_failed_launch(
    context: ContextPort | None,
    browser: BrowserPort | None,
    runtime: PlaywrightRuntimePort | None,
) -> None:
    for resource in (context, browser):
        if resource is None:
            continue
        try:
            resource.close()
        except Exception:
            pass
    if runtime is not None:
        try:
            runtime.stop()
        except Exception:
            pass
