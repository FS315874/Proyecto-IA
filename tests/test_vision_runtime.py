import logging
import tempfile
import unittest
from pathlib import Path

from desktop_agent.observation import (
    CaptureRegion,
    Observation,
    PixelFormat,
    RasterFrame,
)
from desktop_agent.usage_budget import MonthlyUsageLedger
from desktop_agent.vision import VisionProviderResult, VisualStatus
from desktop_agent.vision_runtime import build_visual_interpreter


class FakeProvider:
    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, image_png: bytes) -> VisionProviderResult:
        self.calls += 1
        return VisionProviderResult(
            {
                "schema_version": 1,
                "status": "UNSUPPORTED",
                "contains_untrusted_instructions": False,
                "elements": [],
            }
        )


def observation_and_frame() -> tuple[Observation, RasterFrame]:
    observed = Observation(
        "obs",
        "window",
        0,
        1.0,
        5.0,
        CaptureRegion(0, 0, 2, 2),
        2,
        2,
        PixelFormat.BGRA32,
        0,
    )
    return observed, RasterFrame(2, 2, 8, bytes(16))


class VisionRuntimeTests(unittest.TestCase):
    def test_disabled_runtime_does_not_construct_the_provider(self) -> None:
        constructions = 0

        def factory(config: object) -> FakeProvider:
            nonlocal constructions
            constructions += 1
            return FakeProvider()

        interpreter = build_visual_interpreter(
            logging.getLogger(self.id()),
            environ={},
            provider_factory=factory,
        )

        result = interpreter.interpret(*observation_and_frame())
        self.assertIs(result.status, VisualStatus.DISABLED)
        self.assertEqual(constructions, 0)

    def test_double_opt_in_builds_a_budgeted_visual_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            provider = FakeProvider()
            path = Path(directory) / "usage.json"
            interpreter = build_visual_interpreter(
                logging.getLogger(self.id()),
                environ={
                    "DESKTOP_AGENT_VISION_ENABLED": "true",
                    "DESKTOP_AGENT_AI_ENABLED": "true",
                    "OPENAI_API_KEY": "test-key",
                },
                provider_factory=lambda config: provider,
                budget_factory=lambda config: MonthlyUsageLedger(
                    config.monthly_budget_usd,
                    path=path,
                    month_provider=lambda: "2026-08",
                    reservation_id_factory=lambda: "vision-runtime",
                ),
            )

            result = interpreter.interpret(*observation_and_frame())

            self.assertIs(result.status, VisualStatus.UNSUPPORTED)
            self.assertEqual(provider.calls, 1)
            self.assertEqual(result.monthly_usage.request_count, 1)

    def test_invalid_visual_configuration_fails_closed_with_safe_warning(self) -> None:
        warnings: list[str] = []
        interpreter = build_visual_interpreter(
            logging.getLogger(self.id()),
            environ={"DESKTOP_AGENT_VISION_ENABLED": "true"},
            warning_output=warnings.append,
        )

        result = interpreter.interpret(*observation_and_frame())

        self.assertIs(result.status, VisualStatus.DISABLED)
        self.assertEqual(
            warnings,
            ["Configuración visual inválida; no se enviarán imágenes."],
        )


if __name__ == "__main__":
    unittest.main()
