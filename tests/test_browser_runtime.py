import logging
import tempfile
import unittest
from pathlib import Path

from desktop_agent.browser_bridge import (
    BrowserBridgeOperation,
    BrowserBridgeError,
    BrowserBridgeResponse,
    BrowserBridgeSnapshot,
    BrowserBridgeState,
    BrowserKind,
)
from desktop_agent.browser_preferences import (
    BrowserPreference,
    PreferredBrowser,
    StaticBrowserPreferenceSource,
)
from desktop_agent.browser_preferences import BrowserPreferenceStore
from desktop_agent.browser_runtime import BrowserRuntime, PreferredYouTubeAdapterFactory
from desktop_agent.preferred_browser import PreferredBrowserOpener


class FakeBridge:
    def __init__(self, opened: bool = True) -> None:
        self.opened = opened
        self.site_calls: list[tuple[str, PreferredBrowser]] = []

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
        return BrowserBridgeResponse("test", False, None, "not_used")

    def open_site(self, site_key: str, browser: PreferredBrowser) -> bool:
        self.site_calls.append((site_key, browser))
        return self.opened


class FakeNativeBridge(FakeBridge):
    def __init__(self) -> None:
        super().__init__()
        self.start_calls = 0
        self.close_calls = 0

    def start(self) -> None:
        self.start_calls += 1

    def close(self) -> None:
        self.close_calls += 1


class FakeWaitingBridge(FakeBridge):
    @property
    def snapshot(self) -> BrowserBridgeSnapshot:
        return BrowserBridgeSnapshot(BrowserBridgeState.WAITING, None)


class FakeLock:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.acquire_calls = 0
        self.close_calls = 0

    def acquire(self) -> None:
        self.acquire_calls += 1
        if self.failure is not None:
            raise self.failure

    def close(self) -> None:
        self.close_calls += 1


class PreferredBrowserRuntimeTests(unittest.TestCase):
    def test_closed_selected_browser_is_started_once_then_extension_is_used(self) -> None:
        from unittest.mock import Mock
        bridge = Mock()
        bridge.snapshot = BrowserBridgeSnapshot(BrowserBridgeState.WAITING, None)
        preference = StaticBrowserPreferenceSource(BrowserPreference(PreferredBrowser.CHROME, True))
        started = []

        def wait(_seconds):
            bridge.snapshot = BrowserBridgeSnapshot(BrowserBridgeState.CONNECTED, BrowserKind.CHROME)

        opener = PreferredBrowserOpener(preference, bridge=bridge, process_checker=lambda _: False,
            path_checker=lambda _: True, starter=started.append, waiter=wait)
        self.assertTrue(opener.ensure_current_session())
        self.assertEqual(len(started), 1)
        self.assertEqual(len(started[0]), 1)
        self.assertTrue(started[0][0].endswith("chrome.exe"))

    def test_running_browser_without_extension_is_not_relaunched_and_wait_is_bounded(self) -> None:
        from unittest.mock import Mock
        bridge = Mock()
        bridge.snapshot = BrowserBridgeSnapshot(BrowserBridgeState.WAITING, None)
        started, waits = [], []
        opener = PreferredBrowserOpener(
            StaticBrowserPreferenceSource(BrowserPreference(PreferredBrowser.CHROME, True)),
            bridge=bridge, process_checker=lambda _: True, starter=started.append, waiter=waits.append,
        )
        self.assertFalse(opener.ensure_current_session())
        self.assertEqual(started, [])
        self.assertEqual(len(waits), 50)
        self.assertAlmostEqual(sum(waits), 5)

    def test_other_connected_browser_is_not_replaced_or_used(self) -> None:
        started = []
        opener = PreferredBrowserOpener(
            StaticBrowserPreferenceSource(BrowserPreference(PreferredBrowser.CHROME, True)),
            bridge=FakeBridge(), starter=started.append,
        )
        self.assertFalse(opener.ensure_current_session())
        self.assertEqual(started, [])

    def test_disconnected_extension_fails_with_instruction_and_no_isolated_fallback(self) -> None:
        from unittest.mock import Mock
        from desktop_agent.tools.browser_automation import YouTubePlaybackTool
        isolated = Mock()
        factory = PreferredYouTubeAdapterFactory(
            StaticBrowserPreferenceSource(BrowserPreference(PreferredBrowser.OPERA_GX, True)),
            FakeBridge(), logging.getLogger(self.id()), isolated_factory=isolated,
            session_connector=lambda _: False,
        )
        result = YouTubePlaybackTool(factory, logging.getLogger(self.id()))("música de prueba")
        self.assertFalse(result.success)
        self.assertIn("extensión no está conectada", result.message)
        isolated.assert_not_called()

    def test_current_session_never_falls_back_to_new_browser(self) -> None:
        preference = StaticBrowserPreferenceSource(
            BrowserPreference(PreferredBrowser.OPERA_GX, True)
        )
        bridge = FakeBridge(opened=False)
        started: list[object] = []
        opener = PreferredBrowserOpener(
            preference,
            bridge=bridge,
            path_checker=lambda _path: True,
            starter=started.append,
        )

        self.assertFalse(opener("https://open.spotify.com/"))
        self.assertEqual(
            bridge.site_calls,
            [("spotify", PreferredBrowser.OPERA_GX)],
        )
        self.assertEqual(started, [])

    def test_isolated_factory_is_used_when_current_session_is_disabled(self) -> None:
        expected = object()
        factory = PreferredYouTubeAdapterFactory(
            StaticBrowserPreferenceSource(),
            None,
            logging.getLogger(self.id()),
            isolated_factory=lambda: expected,
        )

        self.assertIs(factory(), expected)

    def test_extension_adapter_is_selected_for_current_session(self) -> None:
        factory = PreferredYouTubeAdapterFactory(
            StaticBrowserPreferenceSource(
                BrowserPreference(PreferredBrowser.OPERA_GX, True)
            ),
            FakeBridge(),
            logging.getLogger(self.id()),
            isolated_factory=lambda: object(),
        )

        adapter = factory()

        self.assertTrue(adapter.policy.persistent_profile)

    def test_managed_stop_adapter_uses_connected_current_session(self) -> None:
        isolated_calls: list[object] = []
        factory = PreferredYouTubeAdapterFactory(
            StaticBrowserPreferenceSource(
                BrowserPreference(PreferredBrowser.OPERA_GX, True)
            ),
            FakeBridge(),
            logging.getLogger(self.id()),
            isolated_factory=lambda: isolated_calls.append(object()),
        )

        adapter = factory.current_session_adapter()

        self.assertIsNotNone(adapter)
        assert adapter is not None
        self.assertTrue(adapter.policy.persistent_profile)
        self.assertEqual(isolated_calls, [])

    def test_managed_stop_adapter_is_absent_when_current_session_is_disabled(self) -> None:
        isolated_calls: list[object] = []
        factory = PreferredYouTubeAdapterFactory(
            StaticBrowserPreferenceSource(),
            None,
            logging.getLogger(self.id()),
            isolated_factory=lambda: isolated_calls.append(object()),
        )

        self.assertIsNone(factory.current_session_adapter())
        self.assertEqual(isolated_calls, [])

    def test_managed_stop_does_not_launch_a_disconnected_browser(self) -> None:
        from unittest.mock import Mock

        bridge = FakeWaitingBridge()
        connector = Mock(return_value=True)
        isolated = Mock()
        factory = PreferredYouTubeAdapterFactory(
            StaticBrowserPreferenceSource(
                BrowserPreference(PreferredBrowser.CHROME, True)
            ),
            bridge,
            logging.getLogger(self.id()),
            isolated_factory=isolated,
            session_connector=connector,
        )

        with self.assertRaises(BrowserBridgeError):
            factory.current_session_adapter()

        connector.assert_not_called()
        isolated.assert_not_called()

    def test_runtime_owns_bridge_only_after_acquiring_single_instance_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bridge = FakeNativeBridge()
            lock = FakeLock(OSError("busy"))
            runtime = BrowserRuntime(
                BrowserPreferenceStore(Path(directory) / "browser.json"),
                bridge,  # type: ignore[arg-type]
                lock,
            )

            runtime.start()
            runtime.close()

        self.assertIs(runtime.snapshot.state, BrowserBridgeState.ERROR)
        self.assertEqual(bridge.start_calls, 0)
        self.assertEqual(bridge.close_calls, 0)
        self.assertEqual(lock.close_calls, 1)


if __name__ == "__main__":
    unittest.main()
