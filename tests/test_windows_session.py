import unittest

from desktop_agent.windows_session import (
    SessionAvailability,
    WindowsSessionMonitor,
)


class FakeSessionApi:
    def __init__(
        self,
        handle: int = 101,
        switchable: bool = True,
        closes: bool = True,
        error_at: str | None = None,
    ) -> None:
        self.handle = handle
        self.switchable = switchable
        self.closes = closes
        self.error_at = error_at
        self.closed: list[int] = []

    def open_input_desktop(self) -> int:
        if self.error_at == "open":
            raise OSError("private open detail")
        return self.handle

    def can_switch_desktop(self, handle: int) -> bool:
        if self.error_at == "switch":
            raise OSError("private switch detail")
        return self.switchable

    def close_desktop(self, handle: int) -> bool:
        self.closed.append(handle)
        if self.error_at == "close":
            raise OSError("private close detail")
        return self.closes


class WindowsSessionMonitorTests(unittest.TestCase):
    def test_reports_available_and_closes_handle(self) -> None:
        api = FakeSessionApi()

        result = WindowsSessionMonitor(api).current()

        self.assertEqual(result, SessionAvailability.AVAILABLE)
        self.assertEqual(api.closed, [101])

    def test_reports_locked_for_missing_or_non_switchable_desktop(self) -> None:
        self.assertEqual(
            WindowsSessionMonitor(FakeSessionApi(handle=0)).current(),
            SessionAvailability.LOCKED,
        )
        api = FakeSessionApi(switchable=False)
        self.assertEqual(
            WindowsSessionMonitor(api).current(),
            SessionAvailability.LOCKED,
        )
        self.assertEqual(api.closed, [101])

    def test_unknown_failure_is_redacted_and_attempts_cleanup(self) -> None:
        for stage in ("open", "switch", "close"):
            with self.subTest(stage=stage):
                api = FakeSessionApi(error_at=stage)
                result = WindowsSessionMonitor(api).current()
                self.assertEqual(result, SessionAvailability.UNKNOWN)
                if stage != "open":
                    self.assertIn(101, api.closed)

    def test_close_failure_without_exception_is_unknown(self) -> None:
        self.assertEqual(
            WindowsSessionMonitor(FakeSessionApi(closes=False)).current(),
            SessionAvailability.UNKNOWN,
        )


if __name__ == "__main__":
    unittest.main()
