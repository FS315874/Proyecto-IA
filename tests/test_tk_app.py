import unittest

from desktop_agent.browser_bridge import (
    BrowserBridgeSnapshot,
    BrowserBridgeState,
    BrowserKind,
)
from desktop_agent.browser_preferences import BrowserPreference, PreferredBrowser
from desktop_agent.interpretation import MonthlyUsageSnapshot
from desktop_agent.local_controller import (
    ControllerState,
    ControllerUpdate,
    ControllerUpdateKind,
)
from desktop_agent.tk_app import (
    _close_optional_voice,
    format_browser_bridge,
    format_latency,
    format_usage,
    format_voice_failure,
)
from desktop_agent.voice import VoiceFailureReason


class TkFormattingTests(unittest.TestCase):
    def test_optional_voice_close_succeeds_when_voice_is_disabled(self) -> None:
        self.assertTrue(_close_optional_voice(None, timeout=2.0))

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

    def test_formats_actionable_redacted_voice_failures(self) -> None:
        self.assertIn(
            "API key es inválida",
            format_voice_failure(VoiceFailureReason.AUTHENTICATION),
        )
        self.assertIn(
            "cuota o créditos",
            format_voice_failure(VoiceFailureReason.QUOTA_OR_RATE_LIMIT),
        )
        self.assertIn(
            "revisá la conexión",
            format_voice_failure(VoiceFailureReason.NETWORK),
        )
        self.assertIn("no identificada", format_voice_failure(None))

    def test_formats_connected_and_mismatched_browser_bridge(self) -> None:
        preference = BrowserPreference(PreferredBrowser.OPERA_GX, True)

        connected = format_browser_bridge(
            BrowserBridgeSnapshot(
                BrowserBridgeState.CONNECTED,
                BrowserKind.OPERA_GX,
            ),
            preference,
        )
        mismatched = format_browser_bridge(
            BrowserBridgeSnapshot(
                BrowserBridgeState.CONNECTED,
                BrowserKind.CHROME,
            ),
            preference,
        )

        self.assertIn("conectada a Opera GX", connected)
        self.assertIn("falta el elegido", mismatched)


if __name__ == "__main__":
    unittest.main()
