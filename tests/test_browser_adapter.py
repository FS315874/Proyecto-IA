import io
import logging
import unittest

from desktop_agent.browser_adapter import (
    BrowserSecurityPolicy,
    BrowserSessionState,
    SafeBrowserAdapter,
)
from desktop_agent.browser_contract import (
    BrowserAdapterDependencies,
    BrowserConsentRequiredError,
    BrowserDomUnavailableError,
    BrowserErrorCode,
    BrowserNoResultsError,
    BrowserOperation,
    BrowserStepStatus,
    PageSnapshot,
    PlaybackSnapshot,
    SearchSnapshot,
)
from desktop_agent.executor import ActionExecutionError, ActionExecutor
from desktop_agent.models import Action, Intent, RiskLevel
from desktop_agent.tools.browser_automation import (
    BrowserNavigationTool,
    YouTubePlaybackTool,
)


class FakeResource:
    def __init__(
        self,
        name: str,
        close_order: list[str],
        fail_on_close: bool = False,
    ) -> None:
        self.name = name
        self.close_order = close_order
        self.fail_on_close = fail_on_close
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        self.close_order.append(self.name)
        if self.fail_on_close:
            raise RuntimeError("private close detail")


class FakePage:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.result_count = 2
        self.failure: Exception | None = None
        self.page = PageSnapshot("https://www.youtube.com/", "YouTube")
        self.playback_snapshots = [
            PlaybackSnapshot(
                PageSnapshot("https://www.youtube.com/watch?v=test", "Test"),
                False,
                False,
                0.5,
                2.0,
            ),
            PlaybackSnapshot(
                PageSnapshot("https://www.youtube.com/watch?v=test", "Test"),
                False,
                False,
                0.5,
                3.0,
            ),
        ]

    def _raise_if_needed(self) -> None:
        if self.failure is not None:
            raise self.failure

    def open_site(self, canonical_url: str, timeout_seconds: float) -> PageSnapshot:
        self.calls.append(("open_site", canonical_url, timeout_seconds))
        self._raise_if_needed()
        return PageSnapshot(canonical_url, "YouTube")

    def search(self, query: str, timeout_seconds: float) -> SearchSnapshot:
        self.calls.append(("search", query, timeout_seconds))
        self._raise_if_needed()
        return SearchSnapshot(
            PageSnapshot("https://www.youtube.com/results", "Resultados"),
            self.result_count,
        )

    def select_first_result(self, timeout_seconds: float) -> PageSnapshot:
        self.calls.append(("select_first_result", timeout_seconds))
        self._raise_if_needed()
        return PageSnapshot("https://www.youtube.com/watch?v=test", "Test")

    def start_playback(self, timeout_seconds: float) -> PlaybackSnapshot:
        self.calls.append(("start_playback", timeout_seconds))
        self._raise_if_needed()
        return PlaybackSnapshot(
            PageSnapshot("https://www.youtube.com/watch?v=test", "Test"),
            False,
            False,
            0.5,
            1.0,
        )

    def read_playback(self) -> PlaybackSnapshot:
        self.calls.append(("read_playback",))
        self._raise_if_needed()
        if len(self.playback_snapshots) == 1:
            return self.playback_snapshots[0]
        return self.playback_snapshots.pop(0)


class SafeBrowserTestSupport:
    def setUp(self) -> None:
        self.log_output = io.StringIO()
        self.logger = logging.Logger(self.id(), level=logging.INFO)
        self.logger.addHandler(logging.StreamHandler(self.log_output))
        self.close_order: list[str] = []
        self.browser = FakeResource("browser", self.close_order)
        self.context = FakeResource("context", self.close_order)
        self.page = FakePage()
        self.waits: list[float] = []
        self.dependencies = BrowserAdapterDependencies(
            self.browser,
            self.context,
            self.page,
            clock=lambda: 1.0,
            wait=self.waits.append,
        )

    def adapter(
        self,
        policy: BrowserSecurityPolicy = BrowserSecurityPolicy(),
    ) -> SafeBrowserAdapter:
        return SafeBrowserAdapter(
            self.dependencies,
            self.logger,
            policy=policy,
        )


class BrowserSecurityPolicyTests(unittest.TestCase):
    def test_default_policy_is_isolated_and_youtube_only(self) -> None:
        policy = BrowserSecurityPolicy()

        self.assertEqual(policy.allowed_site_keys, frozenset({"youtube"}))
        self.assertEqual(policy.max_pages, 1)
        self.assertFalse(policy.allow_downloads)
        self.assertFalse(policy.allow_extensions)
        self.assertFalse(policy.allow_file_access)
        self.assertFalse(policy.persistent_profile)

    def test_rejects_unknown_destinations_or_broader_permissions(self) -> None:
        invalid_builders = (
            lambda: BrowserSecurityPolicy(frozenset()),
            lambda: BrowserSecurityPolicy(frozenset({"unknown"})),
            lambda: BrowserSecurityPolicy(frozenset({"google"})),
            lambda: BrowserSecurityPolicy(max_pages=2),
            lambda: BrowserSecurityPolicy(allow_downloads=True),
            lambda: BrowserSecurityPolicy(allow_extensions=True),
            lambda: BrowserSecurityPolicy(allow_file_access=True),
            lambda: BrowserSecurityPolicy(persistent_profile=True),
        )

        for builder in invalid_builders:
            with self.subTest(builder=builder):
                with self.assertRaises(ValueError):
                    builder()


class SafeBrowserAdapterTests(SafeBrowserTestSupport, unittest.TestCase):
    def playing_adapter(self) -> SafeBrowserAdapter:
        adapter = self.adapter()
        adapter.open_site("youtube")
        adapter.search("lofi")
        adapter.select_first_result()
        adapter.start_playback()
        return adapter

    def test_opens_only_allowlisted_site_using_canonical_url_and_timeout(self) -> None:
        adapter = self.adapter()

        result = adapter.open_site("youtube")

        self.assertIs(result.status, BrowserStepStatus.SUCCESS)
        self.assertIs(adapter.state, BrowserSessionState.SITE_OPEN)
        self.assertEqual(
            self.page.calls,
            [("open_site", "https://www.youtube.com/", 10.0)],
        )
        log = self.log_output.getvalue()
        self.assertIn("operation=OPEN_SITE", log)
        self.assertIn("destination=youtube", log)
        self.assertIn("status=success", log)

    def test_rejects_catalog_site_outside_local_allowlist_before_backend(self) -> None:
        adapter = self.adapter()

        result = adapter.open_site("google")

        self.assertIs(result.status, BrowserStepStatus.FAILURE)
        self.assertIs(result.error.code, BrowserErrorCode.INVALID_INPUT)
        self.assertEqual(self.page.calls, [])
        self.assertNotIn("destination=google", self.log_output.getvalue())

    def test_rejects_non_string_destination_as_structured_failure(self) -> None:
        adapter = self.adapter()

        result = adapter.open_site(["youtube"])  # type: ignore[arg-type]

        self.assertIs(result.status, BrowserStepStatus.FAILURE)
        self.assertIs(result.error.code, BrowserErrorCode.INVALID_INPUT)
        self.assertEqual(self.page.calls, [])

    def test_executes_valid_semantic_sequence_with_fake_page(self) -> None:
        adapter = self.adapter()

        self.assertIs(adapter.open_site("youtube").status, BrowserStepStatus.SUCCESS)
        self.assertIs(
            adapter.search("  lofi\n hip hop ").status,
            BrowserStepStatus.SUCCESS,
        )
        self.assertIs(
            adapter.select_first_result().status,
            BrowserStepStatus.SUCCESS,
        )
        self.assertIs(
            adapter.start_playback().status,
            BrowserStepStatus.SUCCESS,
        )
        playback = adapter.read_playback()

        self.assertIs(playback.status, BrowserStepStatus.SUCCESS)
        self.assertIs(adapter.state, BrowserSessionState.PLAYING)
        self.assertIn(("search", "lofi hip hop", 5.0), self.page.calls)
        self.assertNotIn("lofi hip hop", self.log_output.getvalue())

    def test_verifies_playback_identity_audio_and_progress(self) -> None:
        adapter = self.playing_adapter()

        result = adapter.verify_playback()

        self.assertIs(result.status, BrowserStepStatus.SUCCESS)
        self.assertIs(result.operation, BrowserOperation.VERIFY_PLAYBACK)
        self.assertEqual(result.payload.current_time, 3.0)
        self.assertEqual(self.waits, [1.0])
        self.assertEqual(
            [call[0] for call in self.page.calls[-2:]],
            ["read_playback", "read_playback"],
        )

    def test_allows_unavailable_volume_when_other_signals_pass(self) -> None:
        page = PageSnapshot("https://www.youtube.com/watch?v=test", "Test")
        self.page.playback_snapshots = [
            PlaybackSnapshot(page, False, False, None, 2.0),
            PlaybackSnapshot(page, False, False, None, 3.0),
        ]
        adapter = self.playing_adapter()

        result = adapter.verify_playback()

        self.assertIs(result.status, BrowserStepStatus.SUCCESS)
        self.assertIsNone(result.payload.volume)

    def test_rejects_unproven_playback_states(self) -> None:
        page = PageSnapshot("https://www.youtube.com/watch?v=test", "Test")
        other_page = PageSnapshot(
            "https://www.youtube.com/watch?v=other",
            "Other",
        )
        external_page = PageSnapshot(
            "https://example.com/watch?v=test",
            "External",
        )
        scenarios = (
            (
                PlaybackSnapshot(page, True, False, 0.5, 2.0),
                PlaybackSnapshot(page, False, False, 0.5, 3.0),
            ),
            (
                PlaybackSnapshot(page, False, False, 0.5, 2.0),
                PlaybackSnapshot(page, False, True, 0.5, 3.0),
            ),
            (
                PlaybackSnapshot(page, False, False, 0.5, 2.0),
                PlaybackSnapshot(page, False, False, 0.0, 3.0),
            ),
            (
                PlaybackSnapshot(page, False, False, 0.5, 2.0),
                PlaybackSnapshot(page, False, False, 0.5, 2.05),
            ),
            (
                PlaybackSnapshot(page, False, False, 0.5, 2.0),
                PlaybackSnapshot(other_page, False, False, 0.5, 3.0),
            ),
            (
                PlaybackSnapshot(page, False, False, 0.5, 2.0),
                PlaybackSnapshot(external_page, False, False, 0.5, 3.0),
            ),
        )

        for initial, final in scenarios:
            with self.subTest(initial=initial, final=final):
                self.page.playback_snapshots = [initial, final]
                adapter = self.playing_adapter()

                result = adapter.verify_playback()

                self.assertIs(result.status, BrowserStepStatus.FAILURE)
                self.assertIs(
                    result.error.code,
                    BrowserErrorCode.PLAYBACK_NOT_CONFIRMED,
                )

    def test_rejects_operation_out_of_order_without_backend_effect(self) -> None:
        adapter = self.adapter()

        result = adapter.search("lofi")

        self.assertIs(result.status, BrowserStepStatus.FAILURE)
        self.assertIs(result.error.code, BrowserErrorCode.INVALID_STATE)
        self.assertEqual(self.page.calls, [])

    def test_zero_results_prevents_selection(self) -> None:
        self.page.result_count = 0
        adapter = self.adapter()
        adapter.open_site("youtube")
        adapter.search("sin resultados")

        result = adapter.select_first_result()

        self.assertIs(result.status, BrowserStepStatus.FAILURE)
        self.assertIs(result.error.code, BrowserErrorCode.NO_RESULTS)
        self.assertNotIn(
            "select_first_result",
            [call[0] for call in self.page.calls],
        )

    def test_timeout_is_structured_and_backend_detail_is_not_logged(self) -> None:
        secret = "private selector and page content"
        self.page.failure = TimeoutError(secret)
        adapter = self.adapter()

        result = adapter.open_site("youtube")

        self.assertIs(result.status, BrowserStepStatus.FAILURE)
        self.assertIs(result.error.code, BrowserErrorCode.TIMEOUT)
        self.assertIs(adapter.state, BrowserSessionState.FAILED)
        self.assertNotIn(secret, result.error.message)
        self.assertNotIn(secret, self.log_output.getvalue())

    def test_unexpected_backend_error_is_redacted(self) -> None:
        secret = "private backend detail"
        self.page.failure = RuntimeError(secret)
        adapter = self.adapter()

        result = adapter.open_site("youtube")

        self.assertIs(result.error.code, BrowserErrorCode.BACKEND_FAILURE)
        self.assertNotIn(secret, result.error.message)
        self.assertNotIn(secret, self.log_output.getvalue())

    def test_maps_expected_web_conditions_to_specific_safe_errors(self) -> None:
        conditions = (
            (
                BrowserConsentRequiredError("private consent detail"),
                BrowserErrorCode.CONSENT_REQUIRED,
            ),
            (
                BrowserNoResultsError("private results detail"),
                BrowserErrorCode.NO_RESULTS,
            ),
            (
                BrowserDomUnavailableError("private dom detail"),
                BrowserErrorCode.DOM_UNAVAILABLE,
            ),
        )

        for condition, expected_code in conditions:
            with self.subTest(expected_code=expected_code):
                self.page.failure = condition
                adapter = self.adapter()

                result = adapter.open_site("youtube")

                self.assertIs(result.status, BrowserStepStatus.FAILURE)
                self.assertIs(result.error.code, expected_code)
                self.assertNotIn("private", result.error.message)
                self.assertNotIn("private", self.log_output.getvalue())

    def test_close_is_ordered_and_idempotent(self) -> None:
        adapter = self.adapter()

        first = adapter.close()
        second = adapter.close()

        self.assertIs(first.status, BrowserStepStatus.SUCCESS)
        self.assertIs(second.status, BrowserStepStatus.SUCCESS)
        self.assertEqual(self.close_order, ["context", "browser"])
        self.assertEqual(self.context.close_calls, 1)
        self.assertEqual(self.browser.close_calls, 1)
        self.assertIs(adapter.state, BrowserSessionState.CLOSED)

    def test_close_attempts_browser_when_context_close_fails(self) -> None:
        self.context.fail_on_close = True
        adapter = self.adapter()

        result = adapter.close()

        self.assertIs(result.status, BrowserStepStatus.FAILURE)
        self.assertIs(result.error.code, BrowserErrorCode.BACKEND_FAILURE)
        self.assertEqual(self.close_order, ["context", "browser"])
        self.assertNotIn("private close detail", self.log_output.getvalue())


class BrowserNavigationToolTests(SafeBrowserTestSupport, unittest.TestCase):
    def tool(self) -> BrowserNavigationTool:
        return BrowserNavigationTool(
            self.adapter,
            self.logger,
            clock=lambda: 2.0,
        )

    def test_tool_can_be_registered_in_action_executor(self) -> None:
        executor = ActionExecutor(
            {"navigate_browser": self.tool()},
            self.logger,
        )
        action = Action(
            intent=Intent.BROWSER_NAVIGATION,
            tool_name="navigate_browser",
            arguments={"site_key": "youtube"},
            risk_level=RiskLevel.SAFE,
            requires_confirmation=False,
        )

        result = executor.execute(action)

        self.assertTrue(result.success)
        self.assertEqual(
            result.message,
            "Navegación segura comprobada para YouTube.",
        )
        self.assertEqual(self.close_order, ["context", "browser"])
        log = self.log_output.getvalue()
        self.assertIn("Tool: navigate_browser", log)
        self.assertIn("intent=BROWSER_NAVIGATION destination=youtube", log)
        self.assertIn("status=success", log)

    def test_tool_rejects_unapproved_destination_and_closes_resources(self) -> None:
        result = self.tool()("google")

        self.assertFalse(result.success)
        self.assertEqual(self.page.calls, [])
        self.assertEqual(self.close_order, ["context", "browser"])
        self.assertIn("destination=google", self.log_output.getvalue())

    def test_tool_redacts_factory_failure(self) -> None:
        secret = "private factory detail"

        def failing_factory():
            raise RuntimeError(secret)

        tool = BrowserNavigationTool(
            failing_factory,
            self.logger,
            clock=lambda: 2.0,
        )

        result = tool("youtube")

        self.assertFalse(result.success)
        self.assertNotIn(secret, result.message)
        self.assertNotIn(secret, self.log_output.getvalue())

    def test_executor_reports_failed_browser_tool(self) -> None:
        executor = ActionExecutor(
            {"navigate_browser": self.tool()},
            self.logger,
        )
        action = Action(
            intent=Intent.BROWSER_NAVIGATION,
            tool_name="navigate_browser",
            arguments={"site_key": "google"},
            risk_level=RiskLevel.SAFE,
            requires_confirmation=False,
        )

        with self.assertRaises(ActionExecutionError):
            executor.execute(action)


class YouTubePlaybackToolTests(SafeBrowserTestSupport, unittest.TestCase):
    def tool(self, factory=None) -> YouTubePlaybackTool:
        return YouTubePlaybackTool(
            factory or self.adapter,
            self.logger,
            clock=lambda: 3.0,
        )

    def test_executes_bounded_vertical_flow_through_action_executor(self) -> None:
        executor = ActionExecutor(
            {"play_youtube": self.tool()},
            self.logger,
        )
        action = Action(
            intent=Intent.BROWSER_NAVIGATION,
            tool_name="play_youtube",
            arguments={"query": "  lofi\n hip hop  "},
            risk_level=RiskLevel.SAFE,
            requires_confirmation=False,
        )

        result = executor.execute(action)

        self.assertTrue(result.success)
        self.assertEqual(
            [call[0] for call in self.page.calls],
            [
                "open_site",
                "search",
                "select_first_result",
                "start_playback",
                "read_playback",
                "read_playback",
            ],
        )
        self.assertIn(("search", "lofi hip hop", 5.0), self.page.calls)
        self.assertEqual(self.close_order, ["context", "browser"])
        self.assertNotIn("lofi hip hop", self.log_output.getvalue())

    def test_stops_at_no_results_and_still_closes(self) -> None:
        self.page.result_count = 0

        result = self.tool()("sin resultados")

        self.assertFalse(result.success)
        self.assertEqual(
            [call[0] for call in self.page.calls],
            ["open_site", "search"],
        )
        self.assertEqual(self.close_order, ["context", "browser"])

    def test_rejects_invalid_query_before_creating_browser(self) -> None:
        factory_calls = 0

        def factory():
            nonlocal factory_calls
            factory_calls += 1
            return self.adapter()

        result = self.tool(factory)("   ")

        self.assertFalse(result.success)
        self.assertEqual(factory_calls, 0)
        self.assertEqual(self.page.calls, [])

    def test_failed_verification_prevents_success_and_closes(self) -> None:
        stalled = PlaybackSnapshot(
            PageSnapshot("https://www.youtube.com/watch?v=test", "Test"),
            False,
            False,
            0.5,
            2.0,
        )
        self.page.playback_snapshots = [stalled, stalled]

        result = self.tool()("lofi")

        self.assertFalse(result.success)
        self.assertEqual(self.close_order, ["context", "browser"])
        self.assertIn("operation=VERIFY_PLAYBACK", self.log_output.getvalue())
        self.assertIn("error=playback_not_confirmed", self.log_output.getvalue())


if __name__ == "__main__":
    unittest.main()
