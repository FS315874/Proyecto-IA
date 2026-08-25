import io
import logging
import unittest

from desktop_agent.observation import (
    CaptureRegion,
    ObservationError,
    ObservationLimits,
    ObservationService,
    PixelFormat,
    RasterFrame,
    RedactionRegion,
    WindowTarget,
    encode_bmp,
)


class MutableClock:
    def __init__(self) -> None:
        self.value = 10.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def target(
    window_id: str = "window-test",
    width: int = 8,
    height: int = 6,
    revision: int = 0,
) -> WindowTarget:
    return WindowTarget(window_id, 100, 200, width, height, revision)


def colored_frame(width: int, height: int) -> RasterFrame:
    stride = width * 4
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            pixels.extend((x + 1, y + 2, 100, 255))
    return RasterFrame(width, height, stride, bytes(pixels))


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[WindowTarget, CaptureRegion]] = []
        self.failure: Exception | None = None
        self.override: RasterFrame | None = None

    def capture(
        self,
        selected_target: WindowTarget,
        region: CaptureRegion,
    ) -> RasterFrame:
        self.calls.append((selected_target, region))
        if self.failure is not None:
            raise self.failure
        return self.override or colored_frame(region.width, region.height)


class ObservationContractTests(unittest.TestCase):
    def test_rejects_invalid_regions_targets_frames_and_limits(self) -> None:
        invalid_constructors = [
            lambda: CaptureRegion(-1, 0, 1, 1),
            lambda: CaptureRegion(0, 0, 0, 1),
            lambda: WindowTarget("", 1, 1, 1, 1),
            lambda: WindowTarget("window", 0, 1, 1, 1),
            lambda: RasterFrame(2, 2, 8, b"short"),
            lambda: ObservationLimits(max_width=0),
            lambda: ObservationLimits(retention_seconds=0),
        ]

        for constructor in invalid_constructors:
            with self.subTest(constructor=constructor):
                with self.assertRaises(ValueError):
                    constructor()

    def test_bmp_encoding_is_top_down_and_has_expected_size(self) -> None:
        frame = colored_frame(3, 2)

        encoded = encode_bmp(frame)

        self.assertEqual(encoded[:2], b"BM")
        self.assertEqual(len(encoded), 54 + len(frame.pixels))
        self.assertEqual(encoded[54:], frame.pixels)


class ObservationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = MutableClock()
        self.backend = FakeBackend()
        self.log_output = io.StringIO()
        self.logger = logging.Logger(self.id(), logging.INFO)
        self.logger.addHandler(logging.StreamHandler(self.log_output))
        self.ids = iter(("obs-1", "obs-2", "obs-3"))
        self.service = ObservationService(
            self.backend,
            self.logger,
            limits=ObservationLimits(
                max_width=8,
                max_height=6,
                max_pixels=48,
                min_interval_seconds=1,
                max_captures=2,
                retention_seconds=5,
            ),
            clock=self.clock,
            id_factory=lambda: next(self.ids),
        )

    def test_captures_only_requested_window_region_with_metadata(self) -> None:
        selected_target = target()
        region = CaptureRegion(1, 2, 4, 3)

        observation = self.service.capture(selected_target, region)
        frame = self.service.read_frame(
            observation.observation_id, selected_target
        )

        self.assertEqual(self.backend.calls, [(selected_target, region)])
        self.assertEqual(observation.observation_id, "obs-1")
        self.assertEqual(observation.window_id, selected_target.window_id)
        self.assertEqual(observation.window_revision, 0)
        self.assertEqual(observation.expires_at, 15.0)
        self.assertEqual((frame.width, frame.height), (4, 3))
        self.assertIs(frame.pixel_format, PixelFormat.BGRA32)

    def test_redacts_sensitive_pixels_before_the_frame_can_be_read(self) -> None:
        selected_target = target( width=4, height=3)
        sensitive = RedactionRegion(1, 1, 2, 1)

        observation = self.service.capture(
            selected_target,
            redactions=(sensitive,),
        )
        frame = self.service.read_frame(
            observation.observation_id, selected_target
        )

        first_redacted = frame.stride + 4
        second_redacted = frame.stride + 8
        self.assertEqual(
            frame.pixels[first_redacted : first_redacted + 4],
            b"\x00\x00\x00\xff",
        )
        self.assertEqual(
            frame.pixels[second_redacted : second_redacted + 4],
            b"\x00\x00\x00\xff",
        )
        self.assertNotEqual(frame.pixels[:4], b"\x00\x00\x00\xff")
        self.assertEqual(observation.redaction_count, 1)

    def test_rejects_out_of_window_oversized_and_invalid_redactions(self) -> None:
        invalid_requests = [
            (target(), CaptureRegion(7, 0, 2, 1), ()),
            (target(width=20), CaptureRegion(0, 0, 9, 1), ()),
            (
                target(),
                CaptureRegion(0, 0, 4, 4),
                (RedactionRegion(3, 3, 2, 2),),
            ),
        ]

        for selected_target, region, redactions in invalid_requests:
            with self.subTest(region=region):
                with self.assertRaises(ObservationError):
                    self.service.capture(selected_target, region, redactions)

        self.assertEqual(self.backend.calls, [])

    def test_enforces_frequency_and_total_capture_limits(self) -> None:
        selected_target = target()
        self.service.capture(selected_target)

        with self.assertRaisesRegex(ObservationError, "frecuencia"):
            self.service.capture(selected_target)
        self.clock.advance(1)
        self.service.capture(selected_target)
        self.clock.advance(1)
        with self.assertRaisesRegex(ObservationError, "máximo"):
            self.service.capture(selected_target)

        self.assertEqual(len(self.backend.calls), 2)

    def test_expiry_and_state_change_make_observations_unusable(self) -> None:
        selected_target = target()
        first = self.service.capture(selected_target)
        self.clock.advance(5)

        with self.assertRaisesRegex(ObservationError, "caducó"):
            self.service.read_frame(first.observation_id, selected_target)

        self.clock.advance(1)
        second = self.service.capture(selected_target)
        current_target = self.service.mark_state_changed(selected_target)
        with self.assertRaisesRegex(ObservationError, "obsoleta"):
            self.service.read_frame(second.observation_id, selected_target)
        with self.assertRaisesRegex(ObservationError, "caducó"):
            self.service.read_frame(second.observation_id, current_target)

    def test_cannot_read_an_observation_with_another_window(self) -> None:
        selected_target = target()
        observation = self.service.capture(selected_target)

        with self.assertRaisesRegex(ObservationError, "otro estado"):
            self.service.read_frame(
                observation.observation_id,
                target("window-other"),
            )

    def test_backend_failures_are_redacted_and_not_retained(self) -> None:
        secret = "private-window-title"
        self.backend.failure = RuntimeError(secret)

        with self.assertRaisesRegex(ObservationError, "captura local falló"):
            self.service.capture(target())

        log = self.log_output.getvalue()
        self.assertIn("status=FAILED", log)
        self.assertNotIn(secret, log)

    def test_rejects_backend_dimensions_that_do_not_match_request(self) -> None:
        self.backend.override = colored_frame(2, 2)

        with self.assertRaisesRegex(ObservationError, "dimensiones"):
            self.service.capture(target(), CaptureRegion(0, 0, 3, 2))

    def test_close_discards_retained_frames(self) -> None:
        selected_target = target()
        observation = self.service.capture(selected_target)

        self.service.close()

        with self.assertRaisesRegex(ObservationError, "caducó"):
            self.service.read_frame(observation.observation_id, selected_target)


if __name__ == "__main__":
    unittest.main()
