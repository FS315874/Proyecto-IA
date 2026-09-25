import json
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path

from desktop_agent.browser_bridge import (
    BRIDGE_SCHEMA_VERSION,
    BrowserBridgeError,
    BrowserBridgeOperation,
    BrowserBridgeState,
    BrowserKind,
    NativeBrowserBridge,
    _canonical_json,
    read_bridge_descriptor,
)
from desktop_agent.browser_preferences import PreferredBrowser


class IdentityProtector:
    def protect(self, value: bytes) -> bytes:
        return value

    def unprotect(self, value: bytes) -> bytes:
        return value


class FakeConnection:
    _EOF = object()

    def __init__(self) -> None:
        self.incoming: queue.Queue[object] = queue.Queue()
        self.sent: list[bytes] = []
        self.closed = False

    def send_bytes(self, value: bytes) -> None:
        self.sent.append(value)
        request = json.loads(value.decode("utf-8"))
        self.incoming.put(
            _canonical_json(
                {
                    "schema_version": BRIDGE_SCHEMA_VERSION,
                    "type": "response",
                    "request_id": request["request_id"],
                    "success": True,
                    "payload": {"site_key": "spotify"},
                    "error_code": None,
                }
            )
        )

    def recv_bytes(self, maxlength: int | None = None) -> bytes:
        value = self.incoming.get(timeout=2)
        if value is self._EOF:
            raise EOFError
        assert isinstance(value, bytes)
        return value

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.incoming.put(self._EOF)

    def hello(self, browser: BrowserKind) -> None:
        self.incoming.put(
            _canonical_json(
                {
                    "schema_version": BRIDGE_SCHEMA_VERSION,
                    "type": "hello",
                    "browser": browser.value,
                }
            )
        )


class FakeListener:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.closed = threading.Event()
        self.accepted = False

    def accept(self) -> FakeConnection:
        if not self.accepted:
            self.accepted = True
            return self.connection
        self.closed.wait(2)
        raise OSError("closed")

    def close(self) -> None:
        self.closed.set()


def wait_for_state(
    bridge: NativeBrowserBridge,
    state: BrowserBridgeState,
) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if bridge.snapshot.state is state:
            return
        time.sleep(0.005)
    raise AssertionError(f"bridge did not reach {state}")


class NativeBrowserBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "bridge.session"
        self.connection = FakeConnection()
        self.listener = FakeListener(self.connection)
        self.bridge = NativeBrowserBridge(
            self.path,
            IdentityProtector(),
            listener_factory=lambda **_kwargs: self.listener,
            timeout_seconds=0.5,
        )
        self.addCleanup(self.bridge.close)

    def test_descriptor_is_versioned_protected_and_removed_on_close(self) -> None:
        self.bridge.start()

        pipe_name, authkey = read_bridge_descriptor(
            self.path,
            IdentityProtector(),
        )

        self.assertTrue(pipe_name.startswith(r"\\.\pipe\desktop-agent-"))
        self.assertEqual(len(authkey), 32)
        self.bridge.close()
        self.assertFalse(self.path.exists())

    def test_authenticated_extension_can_complete_allowlisted_request(self) -> None:
        self.bridge.start()
        self.connection.hello(BrowserKind.CHROME)
        wait_for_state(self.bridge, BrowserBridgeState.CONNECTED)

        response = self.bridge.request(
            BrowserBridgeOperation.OPEN_SITE,
            {"site_key": "spotify"},
            BrowserKind.CHROME,
        )

        self.assertTrue(response.success)
        self.assertEqual(response.payload, {"site_key": "spotify"})
        self.assertTrue(
            self.bridge.open_site("spotify", PreferredBrowser.CHROME)
        )

    def test_rejects_wrong_browser_and_arbitrary_destination(self) -> None:
        self.bridge.start()
        self.connection.hello(BrowserKind.CHROME)
        wait_for_state(self.bridge, BrowserBridgeState.CONNECTED)

        with self.assertRaises(BrowserBridgeError):
            self.bridge.request(
                BrowserBridgeOperation.OPEN_SITE,
                {"site_key": "spotify"},
                BrowserKind.OPERA_GX,
            )
        with self.assertRaises(BrowserBridgeError):
            self.bridge.request(
                BrowserBridgeOperation.OPEN_SITE,
                {"site_key": "https://example.com"},
                BrowserKind.CHROME,
            )

    def test_disconnect_returns_to_waiting_without_claiming_connection(self) -> None:
        self.bridge.start()
        self.connection.hello(BrowserKind.OPERA_GX)
        wait_for_state(self.bridge, BrowserBridgeState.CONNECTED)

        self.connection.close()
        wait_for_state(self.bridge, BrowserBridgeState.WAITING)

        self.assertIsNone(self.bridge.snapshot.browser)


if __name__ == "__main__":
    unittest.main()
