import inspect
import math
import unittest

from desktop_agent.browser_contract import (
    MAX_SEARCH_QUERY_LENGTH,
    BrowserAdapter,
    BrowserAdapterDependencies,
    BrowserContractValidationError,
    BrowserError,
    BrowserErrorCode,
    BrowserLimits,
    BrowserOperation,
    BrowserStepResult,
    BrowserStepStatus,
    PageSnapshot,
    PlaybackSnapshot,
    SearchSnapshot,
    normalize_search_query,
    resolve_site_url,
)


class FakeBrowser:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class FakeContext(FakeBrowser):
    pass


class FakePage:
    def __init__(self) -> None:
        self.page = PageSnapshot("https://www.youtube.com/", "YouTube")

    def open_site(self, canonical_url: str, timeout_seconds: float) -> PageSnapshot:
        return PageSnapshot(canonical_url, "YouTube")

    def search(self, query: str, timeout_seconds: float) -> SearchSnapshot:
        return SearchSnapshot(self.page, 3)

    def select_first_result(self, timeout_seconds: float) -> PageSnapshot:
        return PageSnapshot("https://www.youtube.com/watch?v=test", "Test")

    def start_playback(self, timeout_seconds: float) -> PlaybackSnapshot:
        return PlaybackSnapshot(self.page, False, False, 0.5, 1.0)

    def read_playback(self) -> PlaybackSnapshot:
        return PlaybackSnapshot(self.page, False, False, 0.5, 2.0)


class FakeAdapter:
    def __init__(self) -> None:
        self._limits = BrowserLimits()

    @property
    def limits(self) -> BrowserLimits:
        return self._limits

    def open_site(self, site_key: str) -> BrowserStepResult:
        return BrowserStepResult(
            BrowserOperation.OPEN_SITE,
            BrowserStepStatus.SUCCESS,
            1.0,
            PageSnapshot(resolve_site_url(site_key), "YouTube"),
        )

    def search(self, query: str) -> BrowserStepResult:
        normalized = normalize_search_query(query, self._limits)
        return BrowserStepResult(
            BrowserOperation.SEARCH,
            BrowserStepStatus.SUCCESS,
            1.0,
            SearchSnapshot(
                PageSnapshot("https://www.youtube.com/results", normalized),
                1,
            ),
        )

    def select_first_result(self) -> BrowserStepResult:
        return BrowserStepResult(
            BrowserOperation.SELECT_FIRST_RESULT,
            BrowserStepStatus.SUCCESS,
            1.0,
            PageSnapshot("https://www.youtube.com/watch?v=test", "Test"),
        )

    def start_playback(self) -> BrowserStepResult:
        return self._playback_result(BrowserOperation.START_PLAYBACK)

    def read_playback(self) -> BrowserStepResult:
        return self._playback_result(BrowserOperation.READ_PLAYBACK)

    def close(self) -> BrowserStepResult:
        return BrowserStepResult(
            BrowserOperation.CLOSE,
            BrowserStepStatus.SUCCESS,
            1.0,
        )

    def _playback_result(self, operation: BrowserOperation) -> BrowserStepResult:
        return BrowserStepResult(
            operation,
            BrowserStepStatus.SUCCESS,
            1.0,
            PlaybackSnapshot(
                PageSnapshot("https://www.youtube.com/watch?v=test", "Test"),
                False,
                False,
                0.5,
                1.0,
            ),
        )


class BrowserLimitsTests(unittest.TestCase):
    def test_defaults_are_positive_and_bounded(self) -> None:
        limits = BrowserLimits()

        self.assertEqual(limits.navigation_timeout_seconds, 10.0)
        self.assertEqual(limits.operation_timeout_seconds, 5.0)
        self.assertEqual(limits.verification_window_seconds, 1.0)
        self.assertEqual(limits.flow_timeout_seconds, 30.0)
        self.assertEqual(limits.max_query_length, MAX_SEARCH_QUERY_LENGTH)

    def test_rejects_invalid_timeout_and_query_limits(self) -> None:
        invalid_values = (0, -1, math.nan, math.inf, 61, True, "5")
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(BrowserContractValidationError):
                    BrowserLimits(operation_timeout_seconds=value)  # type: ignore[arg-type]

        for value in (0, -1, MAX_SEARCH_QUERY_LENGTH + 1, True, 5.0):
            with self.subTest(max_query_length=value):
                with self.assertRaises(BrowserContractValidationError):
                    BrowserLimits(max_query_length=value)  # type: ignore[arg-type]

    def test_rejects_step_timeout_larger_than_flow_timeout(self) -> None:
        with self.assertRaises(BrowserContractValidationError):
            BrowserLimits(
                navigation_timeout_seconds=20,
                flow_timeout_seconds=10,
            )


class BrowserInputTests(unittest.TestCase):
    def test_resolves_only_canonical_catalog_keys(self) -> None:
        self.assertEqual(resolve_site_url("youtube"), "https://www.youtube.com/")

        for value in ("https://www.youtube.com/", "YouTube", "unknown", "", 7):
            with self.subTest(value=value):
                with self.assertRaises(BrowserContractValidationError):
                    resolve_site_url(value)  # type: ignore[arg-type]

    def test_normalizes_query_as_data(self) -> None:
        self.assertEqual(
            normalize_search_query("  lofi\n hip   hop  "),
            "lofi hip hop",
        )

    def test_rejects_invalid_queries(self) -> None:
        invalid_queries = ("", "   ", "x" * 201, 7, None)
        for query in invalid_queries:
            with self.subTest(query=query):
                with self.assertRaises(BrowserContractValidationError):
                    normalize_search_query(query)  # type: ignore[arg-type]


class BrowserSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.page = PageSnapshot("https://www.youtube.com/", "YouTube")

    def test_accepts_structured_page_search_and_playback_snapshots(self) -> None:
        search = SearchSnapshot(self.page, 3)
        playback = PlaybackSnapshot(self.page, False, False, 0.5, 12.5)

        self.assertEqual(search.result_count, 3)
        self.assertFalse(playback.paused)
        self.assertEqual(playback.volume, 0.5)

    def test_rejects_invalid_page_snapshots(self) -> None:
        invalid_values = (
            ("file:///private.txt", "Private"),
            ("javascript:alert(1)", "Script"),
            ("https://www.youtube.com/", ""),
        )
        for url, title in invalid_values:
            with self.subTest(url=url, title=title):
                with self.assertRaises(BrowserContractValidationError):
                    PageSnapshot(url, title)

    def test_rejects_invalid_search_counts(self) -> None:
        for count in (-1, True, 1.5):
            with self.subTest(count=count):
                with self.assertRaises(BrowserContractValidationError):
                    SearchSnapshot(self.page, count)  # type: ignore[arg-type]

    def test_rejects_invalid_playback_values(self) -> None:
        invalid_values = (
            (False, False, -0.1, 0),
            (False, False, 1.1, 0),
            (False, False, math.nan, 0),
            (False, False, 0.5, -1),
            (False, "false", 0.5, 0),
        )
        for paused, muted, volume, current_time in invalid_values:
            with self.subTest(
                muted=muted,
                volume=volume,
                current_time=current_time,
            ):
                with self.assertRaises(BrowserContractValidationError):
                    PlaybackSnapshot(
                        self.page,
                        paused,  # type: ignore[arg-type]
                        muted,  # type: ignore[arg-type]
                        volume,
                        current_time,
                    )


class BrowserStepResultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.page = PageSnapshot("https://www.youtube.com/", "YouTube")

    def test_accepts_operation_specific_success_payloads(self) -> None:
        valid_results = (
            BrowserStepResult(
                BrowserOperation.OPEN_SITE,
                BrowserStepStatus.SUCCESS,
                1,
                self.page,
            ),
            BrowserStepResult(
                BrowserOperation.SEARCH,
                BrowserStepStatus.SUCCESS,
                2,
                SearchSnapshot(self.page, 2),
            ),
            BrowserStepResult(
                BrowserOperation.READ_PLAYBACK,
                BrowserStepStatus.SUCCESS,
                3,
                PlaybackSnapshot(self.page, False, False, None, 1),
            ),
            BrowserStepResult(
                BrowserOperation.CLOSE,
                BrowserStepStatus.SUCCESS,
                4,
            ),
        )

        self.assertEqual(len(valid_results), 4)

    def test_accepts_structured_failure_without_payload(self) -> None:
        result = BrowserStepResult(
            BrowserOperation.SEARCH,
            BrowserStepStatus.FAILURE,
            5,
            error=BrowserError(
                BrowserErrorCode.TIMEOUT,
                "La búsqueda excedió el tiempo permitido.",
                retryable=False,
            ),
        )

        self.assertIs(result.status, BrowserStepStatus.FAILURE)
        self.assertIs(result.error.code, BrowserErrorCode.TIMEOUT)

    def test_rejects_incoherent_status_payload_or_duration(self) -> None:
        invalid_builders = (
            lambda: BrowserStepResult(
                BrowserOperation.OPEN_SITE,
                BrowserStepStatus.SUCCESS,
                1,
            ),
            lambda: BrowserStepResult(
                BrowserOperation.CLOSE,
                BrowserStepStatus.SUCCESS,
                1,
                self.page,
            ),
            lambda: BrowserStepResult(
                BrowserOperation.SEARCH,
                BrowserStepStatus.FAILURE,
                1,
            ),
            lambda: BrowserStepResult(
                BrowserOperation.SEARCH,
                BrowserStepStatus.FAILURE,
                1,
                error="fallo sin estructura",  # type: ignore[arg-type]
            ),
            lambda: BrowserStepResult(
                BrowserOperation.SEARCH,
                BrowserStepStatus.FAILURE,
                1,
                self.page,
                BrowserError(BrowserErrorCode.BACKEND_FAILURE, "Fallo seguro."),
            ),
            lambda: BrowserStepResult(
                BrowserOperation.CLOSE,
                BrowserStepStatus.SUCCESS,
                math.nan,
            ),
        )

        for builder in invalid_builders:
            with self.subTest(builder=builder):
                with self.assertRaises(BrowserContractValidationError):
                    builder()


class BrowserDependencyContractTests(unittest.TestCase):
    def test_accepts_injected_browser_context_page_clock_and_wait(self) -> None:
        browser = FakeBrowser()
        context = FakeContext()
        page = FakePage()
        clock = lambda: 10.0
        waits: list[float] = []
        dependencies = BrowserAdapterDependencies(
            browser,
            context,
            page,
            clock=clock,
            wait=waits.append,
        )

        self.assertIs(dependencies.browser, browser)
        self.assertIs(dependencies.context, context)
        self.assertIs(dependencies.page, page)
        self.assertEqual(dependencies.clock(), 10.0)
        dependencies.wait(0.5)
        self.assertEqual(waits, [0.5])

    def test_rejects_dependencies_that_do_not_implement_the_ports(self) -> None:
        valid = (FakeBrowser(), FakeContext(), FakePage())
        invalid_dependencies = (
            (object(), valid[1], valid[2]),
            (valid[0], object(), valid[2]),
            (valid[0], valid[1], object()),
        )
        for browser, context, page in invalid_dependencies:
            with self.subTest(browser=browser, context=context, page=page):
                with self.assertRaises(BrowserContractValidationError):
                    BrowserAdapterDependencies(browser, context, page)  # type: ignore[arg-type]

    def test_public_adapter_contract_exposes_only_semantic_inputs(self) -> None:
        expected_parameters = {
            "open_site": ["self", "site_key"],
            "search": ["self", "query"],
            "select_first_result": ["self"],
            "start_playback": ["self"],
            "read_playback": ["self"],
            "close": ["self"],
        }

        for method_name, expected in expected_parameters.items():
            with self.subTest(method=method_name):
                method = getattr(BrowserAdapter, method_name)
                self.assertEqual(list(inspect.signature(method).parameters), expected)

    def test_fake_adapter_satisfies_runtime_contract(self) -> None:
        adapter = FakeAdapter()

        self.assertIsInstance(adapter, BrowserAdapter)
        self.assertIs(
            adapter.open_site("youtube").status,
            BrowserStepStatus.SUCCESS,
        )
        self.assertIs(
            adapter.search("lofi hip hop").status,
            BrowserStepStatus.SUCCESS,
        )
        self.assertIs(
            adapter.select_first_result().operation,
            BrowserOperation.SELECT_FIRST_RESULT,
        )
        self.assertIs(
            adapter.start_playback().operation,
            BrowserOperation.START_PLAYBACK,
        )
        self.assertIs(
            adapter.read_playback().operation,
            BrowserOperation.READ_PLAYBACK,
        )
        self.assertIs(adapter.close().operation, BrowserOperation.CLOSE)


if __name__ == "__main__":
    unittest.main()
