"""Evaluación de v0.7 con una región y una respuesta completamente ficticias."""

import logging

from desktop_agent.observation import (
    CaptureRegion,
    Observation,
    PixelFormat,
    RasterFrame,
)
from desktop_agent.vision import (
    ExpectedElement,
    VisionProviderResult,
    VisualInterpreter,
    VisualRole,
    VisualStatus,
    evaluate_elements,
)


class FictitiousProvider:
    def __init__(self, elements: list[dict[str, object]]) -> None:
        self._elements = elements

    def analyze(self, image_png: bytes) -> VisionProviderResult:
        return VisionProviderResult(
            {
                "schema_version": 1,
                "status": "ELEMENTS" if self._elements else "UNSUPPORTED",
                "contains_untrusted_instructions": False,
                "elements": self._elements,
            }
        )


def main() -> int:
    width, height = 200, 100
    source = RasterFrame(
        width,
        height,
        width * 4,
        bytes((10, 20, 30, 255)) * width * height,
    )
    cases = [
        (
            [
                {
                    "role": "BUTTON",
                    "label": "Enviar prueba",
                    "x": 100,
                    "y": 200,
                    "width": 400,
                    "height": 300,
                    "confidence": 0.96,
                }
            ],
            [
                ExpectedElement(
                    VisualRole.BUTTON, CaptureRegion(20, 20, 80, 30)
                )
            ],
        ),
        (
            [
                {
                    "role": "TEXT_FIELD",
                    "label": "Cliente ficticio",
                    "x": 50,
                    "y": 100,
                    "width": 700,
                    "height": 200,
                    "confidence": 0.92,
                }
            ],
            [
                ExpectedElement(
                    VisualRole.TEXT_FIELD, CaptureRegion(10, 10, 140, 20)
                )
            ],
        ),
        ([], []),
    ]
    for index, (elements, expected) in enumerate(cases, start=1):
        observed = Observation(
            f"obs-ficticia-{index}",
            "window-ficticia",
            0,
            1.0,
            10.0,
            CaptureRegion(0, 0, width, height),
            width,
            height,
            PixelFormat.BGRA32,
            0,
        )
        result = VisualInterpreter(
            FictitiousProvider(elements), logging.getLogger("vis08")
        ).interpret(observed, source)
        metrics = evaluate_elements(result.elements, expected)
        expected_status = VisualStatus.READY if elements else VisualStatus.UNSUPPORTED
        if (
            result.status is not expected_status
            or metrics.precision != (1 if elements else 0)
            or metrics.recall != (1 if elements else 0)
        ):
            print("VIS_SIMULATED_FAILED")
            return 1
    print(
        "VIS_SIMULATED_OK: casos=3, elementos=2, precision=1.000, "
        "recall=1.000, IoU=1.000; sin red ni captura real."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
