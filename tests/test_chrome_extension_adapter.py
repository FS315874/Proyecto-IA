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
from desktop_agent.browser_contract import BrowserLimits, BrowserStepStatus
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
