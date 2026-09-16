import logging
import unittest

from desktop_agent.browser_adapter import BrowserSessionState
from desktop_agent.browser_bridge import (
    BrowserBridgeOperation,
    BrowserBridgeResponse,
    BrowserBridgeSnapshot,
    BrowserBridgeState,
    BrowserKind,
)
from desktop_agent.browser_contract import BrowserErrorCode, BrowserLimits, BrowserStepStatus
from desktop_agent.browser_preferences import PreferredBrowser
from desktop_agent.chrome_extension_adapter import (
    create_youtube_extension_adapter,
)


class FakeBridge:
    def __init__(self) -> None:
        self.operations: list[BrowserBridgeOperation] = []
        self.read_time = 1.3

    @property
    def snapshot(self) -> BrowserBridgeSnapshot:
        return BrowserBridgeSnapshot(
            BrowserBridgeState.CONNECTED,
            BrowserKind.CHROME,
        )

    def open_site(self, site_key: str, browser: PreferredBrowser) -> bool:
        return True

    def request(
        self,
        operation: BrowserBridgeOperation,
        arguments: dict[str, object],
        expected_browser: BrowserKind,
    ) -> BrowserBridgeResponse:
        self.operations.append(operation)
        base = {"url": "https://www.youtube.com/", "title": "YouTube"}
        if operation is BrowserBridgeOperation.YOUTUBE_SEARCH:
            payload = {
                "url": "https://www.youtube.com/results?search_query=lofi",
                "title": "lofi - YouTube",
                "result_count": 1,
            }
        elif operation in {
            BrowserBridgeOperation.YOUTUBE_START,
            BrowserBridgeOperation.YOUTUBE_READ,
            BrowserBridgeOperation.YOUTUBE_STOP,
        }:
            paused = operation is BrowserBridgeOperation.YOUTUBE_STOP
            current_time = (
                self.read_time
                if operation is BrowserBridgeOperation.YOUTUBE_READ
                else 1.0
            )
            if operation is BrowserBridgeOperation.YOUTUBE_READ:
                self.read_time += 0.2
            payload = {
                "url": "https://www.youtube.com/watch?v=abcdef12345",
                "title": "Lofi - YouTube",
                "paused": paused,
                "muted": False,
                "volume": 0.5,
                "current_time": current_time,
            }
        elif operation is BrowserBridgeOperation.YOUTUBE_SELECT_FIRST:
            payload = {
                "url": "https://www.youtube.com/watch?v=abcdef12345",
                "title": "Lofi - YouTube",
            }
        else:
            payload = base
        return BrowserBridgeResponse("test", True, payload, None)


class ChromeExtensionAdapterTests(unittest.TestCase):
    def test_resumes_and_verifies_current_managed_video_without_search(self) -> None:
        bridge = FakeBridge()
        adapter = create_youtube_extension_adapter(
            bridge,
            BrowserKind.CHROME,
            logging.getLogger(self.id()),
            limits=BrowserLimits(
                verification_window_seconds=0.01,
                minimum_playback_progress_seconds=0.001,
            ),
        )

        resumed = adapter.resume_current_playback()
        verified = adapter.verify_playback()

        self.assertIs(resumed.status, BrowserStepStatus.SUCCESS)
        self.assertIs(verified.status, BrowserStepStatus.SUCCESS)
        self.assertEqual(
            bridge.operations,
            [
                BrowserBridgeOperation.YOUTUBE_START,
                BrowserBridgeOperation.YOUTUBE_READ,
                BrowserBridgeOperation.YOUTUBE_READ,
            ],
        )

    def test_known_worker_errors_preserve_their_diagnostic_category(self):
        for code, expected in (
            ("no_results", BrowserErrorCode.NO_RESULTS),
            ("video_unavailable", BrowserErrorCode.CONTENT_UNAVAILABLE),
            ("consent_required", BrowserErrorCode.CONSENT_REQUIRED),
            ("navigation_timeout", BrowserErrorCode.TIMEOUT),
            ("playback_timeout", BrowserErrorCode.TIMEOUT),
            ("playback_not_started", BrowserErrorCode.PLAYBACK_NOT_CONFIRMED),
            ("tab_muted", BrowserErrorCode.PLAYBACK_NOT_CONFIRMED),
            ("search_dom_unavailable", BrowserErrorCode.DOM_UNAVAILABLE),
            ("private_unknown_error", BrowserErrorCode.DOM_UNAVAILABLE),
        ):
            with self.subTest(code=code):
                bridge = FakeBridge()
                bridge.request = lambda *_: BrowserBridgeResponse("test", False, None, code)
                adapter = create_youtube_extension_adapter(bridge, BrowserKind.CHROME, logging.getLogger(self.id()))
                result = adapter.open_site("youtube")
                self.assertIs(result.error.code, expected)
                self.assertNotIn("private", result.error.message)

    def test_success_flag_without_observed_pause_is_not_a_successful_stop(self):
        for state in ({}, {"paused": False}, {"paused": 1}, None):
            with self.subTest(state=state):
                bridge = FakeBridge()
                original_request = bridge.request
                bridge.request = lambda *_: BrowserBridgeResponse("test", True, state, None)
                adapter = create_youtube_extension_adapter(bridge, BrowserKind.CHROME, logging.getLogger(self.id()))
                self.assertIs(adapter.close().status, BrowserStepStatus.FAILURE)
                bridge.request = original_request
                self.assertIs(adapter.close().status, BrowserStepStatus.SUCCESS)

    def test_failed_stop_can_be_retried_without_reporting_false_success(self):
        from desktop_agent.tools.browser_automation import YouTubePlaybackTool
        bridge = FakeBridge()
        original_request = bridge.request
        stops = []
        def request(operation, arguments, expected):
            if operation is BrowserBridgeOperation.YOUTUBE_STOP:
                stops.append(operation)
                if len(stops) == 1:
                    return BrowserBridgeResponse("test", False, None, "playback_not_stopped")
            return original_request(operation, arguments, expected)
        bridge.request = request
        logger = logging.getLogger(self.id())
        tool = YouTubePlaybackTool(lambda: create_youtube_extension_adapter(
            bridge, BrowserKind.CHROME, logger,
            limits=BrowserLimits(verification_window_seconds=.01, minimum_playback_progress_seconds=.001)), logger)
        self.assertTrue(tool("synthetic music").success)
        self.assertFalse(tool.stop().success)
        self.assertTrue(tool.has_active_session)
        self.assertTrue(tool.stop().success)
        self.assertFalse(tool.has_active_session)
        self.assertEqual(len(stops), 2)

    def test_runs_semantic_flow_and_pauses_without_closing_browser(self) -> None:
        bridge = FakeBridge()
        adapter = create_youtube_extension_adapter(
            bridge,
            BrowserKind.CHROME,
            logging.getLogger(self.id()),
            limits=BrowserLimits(
                verification_window_seconds=0.01,
                minimum_playback_progress_seconds=0.001,
            ),
        )

        results = (
            adapter.open_site("youtube"),
            adapter.search("lofi"),
            adapter.select_first_result(),
            adapter.start_playback(),
            adapter.verify_playback(),
            adapter.close(),
        )

        self.assertTrue(
            all(result.status is BrowserStepStatus.SUCCESS for result in results)
        )
        self.assertIn(BrowserBridgeOperation.YOUTUBE_STOP, bridge.operations)
        self.assertIs(adapter.state, BrowserSessionState.CLOSED)
        self.assertTrue(adapter.policy.allow_extensions)
        self.assertTrue(adapter.policy.persistent_profile)

    def test_bridge_failure_is_converted_to_safe_adapter_failure(self) -> None:
        bridge = FakeBridge()

        def invalid_request(*_args, **_kwargs):
            return BrowserBridgeResponse("test", True, {"url": "bad"}, None)

        bridge.request = invalid_request  # type: ignore[method-assign]
        adapter = create_youtube_extension_adapter(
            bridge,
            BrowserKind.CHROME,
            logging.getLogger(self.id()),
        )

        result = adapter.open_site("youtube")

        self.assertIs(result.status, BrowserStepStatus.FAILURE)


if __name__ == "__main__":
    unittest.main()
