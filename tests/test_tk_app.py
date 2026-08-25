import unittest

from desktop_agent.interpretation import MonthlyUsageSnapshot
from desktop_agent.local_controller import (
    ControllerState,
    ControllerUpdate,
    ControllerUpdateKind,
)
from desktop_agent.tk_app import format_latency, format_usage


class TkFormattingTests(unittest.TestCase):
    def test_formats_usage_without_exposing_content(self) -> None:
        snapshot = MonthlyUsageSnapshot(
            month="2026-08",
            request_count=2,
            input_tokens=20,
            output_tokens=5,
            total_tokens=25,
            cached_input_tokens=0,
            cache_write_tokens=0,
            estimated_cost_usd=0.000015,
            budget_usd=1.0,
            remaining_usd=0.999985,
        )

        self.assertEqual(
            format_usage(snapshot),
            "Uso IA 2026-08: 25 tokens · USD 0.000015 / USD 1.00",
        )
        self.assertEqual(format_usage(None), "Uso IA: no disponible")

    def test_formats_measured_latency_breakdown(self) -> None:
        update = ControllerUpdate(
            ControllerUpdateKind.RESULT,
            ControllerState.RUNNING,
            queue_ms=12.4,
            total_ms=112.8,
        )

        self.assertEqual(
            format_latency(update),
            "Latencia: total 113 ms · cola 12 ms · ejecución 100 ms",
        )


if __name__ == "__main__":
    unittest.main()
