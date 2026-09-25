"""Runner acotado para el checkpoint manual WEB-07.

Inicia Chromium visible y usa red. Las consultas fijas o interactivas se validan como
datos y no se incorporan a los logs.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from collections.abc import Callable, Sequence

from desktop_agent.browser_contract import (
    BrowserStepResult,
    BrowserStepStatus,
    normalize_search_query,
)
from desktop_agent.playwright_backend import create_youtube_playwright_adapter

VALID_QUERY = "Lofi Girl lofi hip hop radio beats to relax study to"
NO_RESULTS_QUERY = (
    "web07-no-results-7f4c9a2e1b8d6f3a5c0e9d2b7a4f1c8e6d3b0a9f"
)
QUERY_SCENARIOS = {
    "valid": VALID_QUERY,
    "no-results": NO_RESULTS_QUERY,
}
SCENARIOS = (*QUERY_SCENARIOS, "custom", "cancel")


def _step_record(result: BrowserStepResult, wall_ms: float) -> dict[str, object]:
    return {
        "operation": result.operation.value,
        "status": result.status.value,
        "adapter_duration_ms": round(result.duration_ms, 3),
        "wall_duration_ms": round(wall_ms, 3),
        "error": result.error.code.value if result.error is not None else None,
    }


def run_scenario(
    scenario: str,
    custom_query: str | None = None,
    stop_waiter: Callable[[], None] | None = None,
) -> tuple[dict[str, object], bool]:
    if scenario not in SCENARIOS:
        raise ValueError("Escenario WEB-07 no permitido.")
    query = None
    if scenario != "cancel":
        raw_query = (
            custom_query
            if scenario == "custom"
            else QUERY_SCENARIOS[scenario]
        )
        query = normalize_search_query(raw_query)

    logger = logging.getLogger("desktop_agent.web07")
    total_started = time.perf_counter()
    launch_started = time.perf_counter()
    adapter = None
    steps: list[dict[str, object]] = []
    close_record: dict[str, object] | None = None

    try:
        adapter = create_youtube_playwright_adapter(logger, headless=False)
        browser_start_ms = (time.perf_counter() - launch_started) * 1_000
    except Exception:
        report = {
            "scenario": scenario,
            "status": "failure",
            "interpretation": "not_applicable",
            "interpretation_ms": 0.0,
            "browser_start_ms": round(
                (time.perf_counter() - launch_started) * 1_000,
                3,
            ),
            "navigation_ms": 0.0,
            "active_hold_ms": 0.0,
            "total_ms": round(
                (time.perf_counter() - total_started) * 1_000,
                3,
            ),
            "steps": steps,
            "close": close_record,
            "error": "browser_start_failed",
        }
        return report, False

    navigation_started = time.perf_counter()
    navigation_ms = 0.0
    active_hold_ms = 0.0
    expected_observed = False
    try:
        if scenario == "cancel":
            step_started = time.perf_counter()
            result = adapter.open_site("youtube")
            steps.append(
                _step_record(
                    result,
                    (time.perf_counter() - step_started) * 1_000,
                )
            )
            if result.status is BrowserStepStatus.SUCCESS:
                navigation_ms = (
                    time.perf_counter() - navigation_started
                ) * 1_000
                expected_observed = True
                if stop_waiter is not None:
                    print("WEB-07 controlled stop ready", flush=True)
                    hold_started = time.perf_counter()
                    stop_waiter()
                    active_hold_ms = (
                        time.perf_counter() - hold_started
                    ) * 1_000
        else:
            operations: list[Callable[[], BrowserStepResult]] = [
                lambda: adapter.open_site("youtube"),
                lambda: adapter.search(query),
                adapter.select_first_result,
            ]
            if scenario in {"valid", "custom"}:
                operations.extend(
                    [adapter.start_playback, adapter.verify_playback]
                )

            for operation in operations:
                step_started = time.perf_counter()
                result = operation()
                wall_ms = (time.perf_counter() - step_started) * 1_000
                steps.append(_step_record(result, wall_ms))

                if result.status is BrowserStepStatus.FAILURE:
                    expected_observed = (
                        scenario == "no-results"
                        and result.error is not None
                        and result.error.code.value == "no_results"
                    )
                    break
            else:
                expected_observed = scenario in {"valid", "custom"}
            navigation_ms = (
                time.perf_counter() - navigation_started
            ) * 1_000
            if expected_observed and stop_waiter is not None:
                print("WEB-07 playback active", flush=True)
                hold_started = time.perf_counter()
                stop_waiter()
                active_hold_ms = (
                    time.perf_counter() - hold_started
                ) * 1_000
    finally:
        if navigation_ms == 0.0:
            navigation_ms = (
                time.perf_counter() - navigation_started
            ) * 1_000
        close_started = time.perf_counter()
        close_result = adapter.close()
        close_record = _step_record(
            close_result,
            (time.perf_counter() - close_started) * 1_000,
        )

    passed = (
        expected_observed
        and close_record is not None
        and close_record["status"] == BrowserStepStatus.SUCCESS.value
    )
    report = {
        "scenario": scenario,
        "status": "success" if passed else "failure",
        "interpretation": "not_applicable",
        "interpretation_ms": 0.0,
        "browser_start_ms": round(browser_start_ms, 3),
        "navigation_ms": round(navigation_ms, 3),
        "active_hold_ms": round(active_hold_ms, 3),
        "total_ms": round(
            (time.perf_counter() - total_started) * 1_000,
            3,
        ),
        "steps": steps,
        "close": close_record,
        "error": None if passed else "expected_outcome_not_observed",
    }
    return report, passed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=tuple(SCENARIOS))
    args = parser.parse_args(argv)

    custom_query = None
    if args.scenario == "custom":
        custom_query = input("Consulta pública para YouTube: ")

    stop_waiter = None
    if args.scenario in {"valid", "custom", "cancel"}:
        stop_waiter = lambda: input(
            "Sesión activa; presioná Enter para detenerla: "
        )

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
    )
    report, passed = run_scenario(
        args.scenario,
        custom_query,
        stop_waiter,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
