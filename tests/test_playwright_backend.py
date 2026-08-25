import io
import logging
import unittest

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from desktop_agent.browser_contract import (
    BrowserConsentRequiredError,
    BrowserDomUnavailableError,
    BrowserNoResultsError,
    BrowserStepStatus,
)
from desktop_agent.executor import ActionExecutionError, ActionExecutor
from desktop_agent.models import Action, Intent, RiskLevel
from desktop_agent.playwright_backend import (
    CONSENT_SELECTOR,
    EMPTY_RESULTS_SELECTOR,
    MUTE_BUTTON_SELECTOR,
    PLAY_BUTTON_SELECTOR,
    RESULTS_READY_SELECTOR,
    SEARCH_INPUT_SELECTORS,
    VIDEO_RESULT_SELECTOR,
    VIDEO_SELECTOR,
    YOUTUBE_CANONICAL_URL,
    YouTubePlaywrightPage,
    create_youtube_playwright_adapter,
)
from desktop_agent.tools.browser_automation import YouTubePlaybackTool


class FakeLocator:
    def __init__(
        self,
        page,
        name: str,
        *,
        count: int = 0,
        visible: bool = False,
        href: str | None = None,
        playback_states: list[object] | None = None,
    ) -> None:
        self.page = page
        self.name = name
        self.count_value = count
        self.visible = visible
        self.href = href
        self.playback_states = list(playback_states or [])
        self.items: list[FakeLocator] | None = None
        self.calls: list[tuple[object, ...]] = []
        self.press_error: Exception | None = None
        self.wait_error: Exception | None = None

    @property
    def first(self):
        return self

    def nth(self, index: int):
        self.calls.append(("nth", index))
        if self.items is None:
            return self
        return self.items[index]

    def count(self) -> int:
        self.calls.append(("count",))
        return self.count_value

    def is_visible(self, *, timeout=None) -> bool:
        self.calls.append(("is_visible", timeout))
        return self.visible

    def wait_for(self, *, timeout=None, state=None) -> None:
        self.calls.append(("wait_for", timeout, state))
        if self.wait_error is not None:
            raise self.wait_error

    def fill(self, value: str, *, timeout=None) -> None:
        self.calls.append(("fill", value, timeout))

    def press(self, key: str, *, timeout=None) -> None:
        self.calls.append(("press", key, timeout))
        if self.press_error is not None:
            raise self.press_error
        self.page.url_value = "https://www.youtube.com/results?search_query=test"
        self.page.title_value = "Resultados - YouTube"

    def get_attribute(self, name: str, *, timeout=None) -> str | None:
        self.calls.append(("get_attribute", name, timeout))
        return self.href

    def click(self, *, timeout=None) -> None:
        self.calls.append(("click", timeout))

    def evaluate(self, expression: str, *, timeout=None) -> object:
        self.calls.append(("evaluate", expression, timeout))
        if not self.playback_states:
            raise RuntimeError("private missing playback state")
        if len(self.playback_states) == 1:
            return self.playback_states[0]
        return self.playback_states.pop(0)


class FakePage:
    def __init__(self) -> None:
        self.url_value = YOUTUBE_CANONICAL_URL
        self.title_value = "YouTube"
        self.goto_calls: list[tuple[object, ...]] = []
        self.wait_for_url_calls: list[tuple[object, ...]] = []
        self.locators: dict[str, FakeLocator] = {}
        self._configure_successful_flow()

    @property
    def url(self) -> str:
        return self.url_value

    def title(self) -> str:
        return self.title_value

    def goto(self, url: str, *, timeout=None, wait_until=None) -> object:
        self.goto_calls.append((url, timeout, wait_until))
        self.url_value = url
        self.title_value = (
            "Video - YouTube" if "/watch" in url else "YouTube"
        )
        return object()

    def locator(self, selector: str) -> FakeLocator:
        return self.locators.setdefault(
            selector,
            FakeLocator(self, selector),
        )

    def wait_for_url(self, url: str, *, timeout=None, wait_until=None) -> None:
        self.wait_for_url_calls.append((url, timeout, wait_until))

    def _configure_successful_flow(self) -> None:
        self.locators[CONSENT_SELECTOR] = FakeLocator(
            self,
            "consent",
        )
        self.locators[SEARCH_INPUT_SELECTORS[0]] = FakeLocator(
            self,
            "search",
            count=1,
            visible=True,
        )
        self.locators[RESULTS_READY_SELECTOR] = FakeLocator(
            self,
            "results_ready",
            count=1,
            visible=True,
        )
        self.locators[VIDEO_RESULT_SELECTOR] = FakeLocator(
            self,
            "video_results",
            count=2,
            visible=True,
            href="/watch?v=abcdefghijk&list=private-list",
        )
        self.locators[EMPTY_RESULTS_SELECTOR] = FakeLocator(
            self,
            "empty_results",
        )
        self.locators[VIDEO_SELECTOR] = FakeLocator(
            self,
            "video",
            count=1,
            visible=True,
            playback_states=[
                {
                    "paused": True,
                    "muted": True,
                    "volume": 0.5,
                    "currentTime": 0.0,
                },
                {
                    "paused": False,
                    "muted": True,
                    "volume": 0.5,
                    "currentTime": 0.5,
                },
                {
                    "paused": False,
                    "muted": False,
                    "volume": 0.5,
                    "currentTime": 1.0,
                },
            ],
        )
        self.locators[PLAY_BUTTON_SELECTOR] = FakeLocator(
            self,
            "play_button",
            count=1,
            visible=True,
        )
        self.locators[MUTE_BUTTON_SELECTOR] = FakeLocator(
            self,
            "mute_button",
            count=1,
            visible=True,
        )


class FakeContext:
    def __init__(self, page: FakePage, fail_new_page: bool = False) -> None:
        self.page = page
        self.fail_new_page = fail_new_page
        self.close_calls = 0

    def new_page(self) -> FakePage:
        if self.fail_new_page:
            raise RuntimeError("private page creation detail")
        return self.page

    def close(self) -> None:
        self.close_calls += 1


class FakeBrowser:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.context_kwargs: dict[str, object] | None = None
        self.close_calls = 0

    def new_context(self, **kwargs: object) -> FakeContext:
        self.context_kwargs = kwargs
        return self.context

    def close(self) -> None:
        self.close_calls += 1


class FakeBrowserType:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser
        self.launch_kwargs: dict[str, object] | None = None

    def launch(self, **kwargs: object) -> FakeBrowser:
        self.launch_kwargs = kwargs
        return self.browser


class FakeRuntime:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser_type = FakeBrowserType(browser)
        self.stop_calls = 0

    @property
    def chromium(self) -> FakeBrowserType:
        return self.browser_type

    def stop(self) -> None:
        self.stop_calls += 1


class YouTubePlaywrightPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.page = FakePage()
        self.backend = YouTubePlaywrightPage(self.page, 5.0)

    def test_executes_vertical_flow_with_only_canonical_navigation(self) -> None:
        opened = self.backend.open_site(YOUTUBE_CANONICAL_URL, 10.0)
        search = self.backend.search('lofi "</script>"', 5.0)
        selected = self.backend.select_first_result(5.0)
        playback = self.backend.start_playback(5.0)

        self.assertEqual(opened.url, YOUTUBE_CANONICAL_URL)
        self.assertEqual(search.result_count, 2)
        self.assertEqual(
            selected.url,
            "https://www.youtube.com/watch?v=abcdefghijk",
        )
        self.assertFalse(playback.paused)
        self.assertFalse(playback.muted)
        self.assertEqual(
            [call[0] for call in self.page.goto_calls],
            [
                YOUTUBE_CANONICAL_URL,
                "https://www.youtube.com/watch?v=abcdefghijk",
            ],
        )
        search_input = self.page.locators[SEARCH_INPUT_SELECTORS[0]]
        self.assertIn(("fill", 'lofi "</script>"', 5000.0), search_input.calls)
        self.assertEqual(
            len(self.page.locators[PLAY_BUTTON_SELECTOR].calls),
            2,
        )
        self.assertEqual(
            len(self.page.locators[MUTE_BUTTON_SELECTOR].calls),
            2,
        )

    def test_stops_when_consent_is_visible(self) -> None:
        consent = self.page.locators[CONSENT_SELECTOR]
        consent.count_value = 1
        consent.visible = True

        with self.assertRaises(BrowserConsentRequiredError):
            self.backend.open_site(YOUTUBE_CANONICAL_URL, 10.0)

    def test_reports_empty_results_without_selecting(self) -> None:
        self.page.locators[VIDEO_RESULT_SELECTOR].count_value = 0
        empty = self.page.locators[EMPTY_RESULTS_SELECTOR]
        empty.count_value = 1
        empty.visible = True

        search = self.backend.search("inexistente", 5.0)

        self.assertEqual(search.result_count, 0)
        with self.assertRaises(BrowserNoResultsError):
            self.backend.select_first_result(5.0)

    def test_reports_dom_change_when_search_input_is_missing(self) -> None:
        self.page.locators[SEARCH_INPUT_SELECTORS[0]].count_value = 0

        with self.assertRaises(BrowserDomUnavailableError):
            self.backend.search("lofi", 5.0)

    def test_translates_playwright_timeout_without_backend_detail(self) -> None:
        search = self.page.locators[SEARCH_INPUT_SELECTORS[0]]
        search.press_error = PlaywrightTimeoutError("private selector detail")

        with self.assertRaises(TimeoutError) as captured:
            self.backend.search("lofi", 5.0)

        self.assertNotIn("private", str(captured.exception))
        self.assertIsNone(captured.exception.__cause__)

    def test_rejects_external_result_href_before_navigation(self) -> None:
        results = self.page.locators[VIDEO_RESULT_SELECTOR]
        results.count_value = 1
        results.href = (
            "https://example.com/watch?v=abcdefghijk"
        )

        with self.assertRaises(BrowserDomUnavailableError):
            self.backend.select_first_result(5.0)

        self.assertEqual(self.page.goto_calls, [])

    def test_selects_first_result_that_meets_local_criteria(self) -> None:
        results = self.page.locators[VIDEO_RESULT_SELECTOR]
        results.items = [
            FakeLocator(
                self.page,
                "external_result",
                href="https://example.com/watch?v=abcdefghijk",
            ),
            FakeLocator(
                self.page,
                "allowed_result",
                href="/watch?v=lmnopqrstuv&list=private-list",
            ),
        ]

        selected = self.backend.select_first_result(5.0)

        self.assertEqual(
            selected.url,
            "https://www.youtube.com/watch?v=lmnopqrstuv",
        )

    def test_rejects_playback_that_remains_paused(self) -> None:
        self.page.url_value = "https://www.youtube.com/watch?v=abcdefghijk"
        video = self.page.locators[VIDEO_SELECTOR]
        video.playback_states = [
            {
                "paused": True,
                "muted": False,
                "volume": 0.5,
                "currentTime": 0.0,
            }
        ]

        with self.assertRaises(BrowserDomUnavailableError):
            self.backend.start_playback(5.0)


class PlaywrightFactoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.log_output = io.StringIO()
        self.logger = logging.Logger(self.id(), level=logging.INFO)
        self.logger.addHandler(logging.StreamHandler(self.log_output))
        self.page = FakePage()
        self.context = FakeContext(self.page)
        self.browser = FakeBrowser(self.context)
        self.runtime = FakeRuntime(self.browser)

    def test_factory_uses_ephemeral_restricted_context_and_closes_runtime(self) -> None:
        adapter = create_youtube_playwright_adapter(
            self.logger,
            headless=True,
            runtime_starter=lambda: self.runtime,
        )

        result = adapter.close()

        self.assertIs(result.status, BrowserStepStatus.SUCCESS)
        self.assertEqual(
            self.runtime.browser_type.launch_kwargs,
            {"headless": True, "timeout": 10000.0},
        )
        self.assertEqual(
            self.browser.context_kwargs,
            {
                "accept_downloads": False,
                "permissions": [],
                "service_workers": "block",
                "ignore_https_errors": False,
            },
        )
        self.assertEqual(self.context.close_calls, 1)
        self.assertEqual(self.browser.close_calls, 1)
        self.assertEqual(self.runtime.stop_calls, 1)

    def test_factory_cleans_partial_resources_and_redacts_failure(self) -> None:
        secret = "private page creation detail"
        self.context.fail_new_page = True

        with self.assertRaises(RuntimeError) as captured:
            create_youtube_playwright_adapter(
                self.logger,
                runtime_starter=lambda: self.runtime,
            )

        self.assertNotIn(secret, str(captured.exception))
        self.assertIsNone(captured.exception.__cause__)
        self.assertEqual(self.context.close_calls, 1)
        self.assertEqual(self.browser.close_calls, 1)
        self.assertEqual(self.runtime.stop_calls, 1)


class V04BrowserIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.log_output = io.StringIO()
        self.logger = logging.Logger(self.id(), level=logging.INFO)
        self.logger.addHandler(logging.StreamHandler(self.log_output))
        self.page = FakePage()
        self.context = FakeContext(self.page)
        self.browser = FakeBrowser(self.context)
        self.runtime = FakeRuntime(self.browser)
        self.waits: list[float] = []

    def executor(self) -> ActionExecutor:
        def adapter_factory():
            return create_youtube_playwright_adapter(
                self.logger,
                headless=True,
                runtime_starter=lambda: self.runtime,
                clock=lambda: 1.0,
                wait=self.waits.append,
            )

        return ActionExecutor(
            {
                "play_youtube": YouTubePlaybackTool(
                    adapter_factory,
                    self.logger,
                    clock=lambda: 1.0,
                )
            },
            self.logger,
        )

    def action(self, query: str = "lofi hip hop") -> Action:
        return Action(
            intent=Intent.BROWSER_NAVIGATION,
            tool_name="play_youtube",
            arguments={"query": query},
            risk_level=RiskLevel.SAFE,
            requires_confirmation=False,
        )

    def test_composes_complete_verified_flow_without_real_browser(self) -> None:
        video = self.page.locators[VIDEO_SELECTOR]
        video.playback_states = [
            {
                "paused": True,
                "muted": True,
                "volume": 0.5,
                "currentTime": 0.0,
            },
            {
                "paused": False,
                "muted": True,
                "volume": 0.5,
                "currentTime": 0.5,
            },
            {
                "paused": False,
                "muted": False,
                "volume": 0.5,
                "currentTime": 1.0,
            },
            {
                "paused": False,
                "muted": False,
                "volume": 0.5,
                "currentTime": 2.0,
            },
            {
                "paused": False,
                "muted": False,
                "volume": 0.5,
                "currentTime": 3.0,
            },
        ]

        result = self.executor().execute(self.action())

        self.assertTrue(result.success)
        self.assertEqual(self.waits, [1.0])
        self.assertEqual(self.context.close_calls, 1)
        self.assertEqual(self.browser.close_calls, 1)
        self.assertEqual(self.runtime.stop_calls, 1)
        log = self.log_output.getvalue()
        self.assertIn("operation=VERIFY_PLAYBACK", log)
        self.assertIn("status=success", log)
        self.assertNotIn("lofi hip hop", log)

    def test_composed_consent_failure_is_safe_and_closes_resources(self) -> None:
        consent = self.page.locators[CONSENT_SELECTOR]
        consent.count_value = 1
        consent.visible = True

        with self.assertRaises(ActionExecutionError):
            self.executor().execute(self.action())

        self.assertEqual(self.waits, [])
        self.assertEqual(self.context.close_calls, 1)
        self.assertEqual(self.browser.close_calls, 1)
        self.assertEqual(self.runtime.stop_calls, 1)
        log = self.log_output.getvalue()
        self.assertIn("error=consent_required", log)
        self.assertNotIn("lofi hip hop", log)

if __name__ == "__main__":
    unittest.main()
