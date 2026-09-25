import contextlib
import io
import logging
import unittest
from unittest.mock import patch

from desktop_agent.cli import build_executor, main
from desktop_agent.interpretation import (
    ProposalIntent,
    ProposalValidationError,
    build_action_from_proposal,
    validate_proposal,
)
from desktop_agent.models import Intent, RiskLevel, ToolResult
from desktop_agent.output_audio import (
    OutputAudioError,
    OutputDevice,
    OutputVolumeRequest,
    OutputVolumeTool,
    validate_output_percent,
)
from desktop_agent.parser import parse_command
from desktop_agent.plans import validate_plan_proposal


class FakeOutputBackend:
    def __init__(self, devices=(), observed=None, current=0.5, error=None):
        self.devices = tuple(devices)
        self.observed = observed
        self.current = current
        self.error = error
        self.calls = []

    def list_devices(self):
        if self.error is not None:
            raise self.error
        return self.devices

    def set_volume(self, endpoint_id, scalar):
        self.calls.append((endpoint_id, scalar))
        if self.error is not None:
            raise self.error
        return scalar if self.observed is None else self.observed

    def read_volume(self, endpoint_id):
        if self.error is not None:
            raise self.error
        return self.current


class OutputAudioTests(unittest.TestCase):
    def setUp(self):
        self.hyperx = OutputDevice(
            "endpoint-hyperx",
            "Auriculares (HyperX Cloud Alpha Wireless)",
        )

    def test_matches_natural_device_words_sets_and_verifies(self):
        backend = FakeOutputBackend((self.hyperx,), current=0.5)

        result = OutputVolumeTool(backend)(
            "los altavoces de mis auriculares HyperX",
            "35",
        )

        self.assertTrue(result.success)
        self.assertEqual(backend.calls, [("endpoint-hyperx", 0.35)])
        self.assertIn("35 %", result.message)
        self.assertIn("silencio no se modificó", result.message)
        self.assertNotIn("endpoint-hyperx", repr(self.hyperx))

    def test_missing_ambiguous_and_unconfirmed_fail_closed(self):
        cases = (
            (
                FakeOutputBackend(()),
                "HyperX",
                "audio_device_missing",
            ),
            (
                FakeOutputBackend((
                    self.hyperx,
                    OutputDevice("endpoint-2", "Altavoces HyperX USB"),
                )),
                "HyperX",
                "audio_device_ambiguous",
            ),
            (
                FakeOutputBackend((self.hyperx,), observed=0.5, current=0.2),
                "HyperX",
                "audio_volume_not_confirmed",
            ),
        )
        for backend, query, code in cases:
            with self.subTest(code=code):
                result = OutputVolumeTool(backend)(query, "35")
                self.assertFalse(result.success)
                self.assertEqual(result.error_code, code)

    def test_input_limit_and_backend_errors_are_structured(self):
        for value in ("81", "100", "035", "-1", "35.0"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_output_percent(value)
        backend = FakeOutputBackend(
            error=OutputAudioError("Core Audio no disponible", "audio_unavailable")
        )
        result = OutputVolumeTool(backend)("HyperX", "35")
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "audio_unavailable")
        self.assertEqual(result.error_stage, "audio_backend")

    def test_noop_is_verified_without_writing(self):
        backend = FakeOutputBackend((self.hyperx,), current=0.35)

        result = OutputVolumeTool(backend)("HyperX", "35")

        self.assertTrue(result.success)
        self.assertEqual(backend.calls, [])
        self.assertIn("ya estaba", result.message)

    def test_specific_voicemeeter_prefix_beats_broader_token_matches(self):
        backend = FakeOutputBackend((
            OutputDevice(
                "main",
                "Voicemeeter Input (VB-Audio Voicemeeter VAIO)",
            ),
            OutputDevice(
                "aux",
                "Voicemeeter AUX Input (VB-Audio Voicemeeter VAIO)",
            ),
        ))

        result = OutputVolumeTool(backend)("Voicemeeter Input", "35")

        self.assertTrue(result.success)
        self.assertEqual(backend.calls, [("main", 0.35)])

    def test_parser_accepts_flexible_phrase_without_confusing_spotify(self):
        action = parse_command(
            "Poné el volumen de los altavoces de mis auriculares HyperX "
            "al nivel de 35 %."
        )
        self.assertIsNotNone(action)
        self.assertEqual(action.tool_name, "set_output_volume")
        self.assertEqual(
            action.arguments,
            {
                "device": "los altavoces de mis auriculares HyperX",
                "percent": "35",
            },
        )
        self.assertIs(action.intent, Intent.SYSTEM_CHANGE)
        self.assertIs(action.risk_level, RiskLevel.SAFE)
        self.assertFalse(action.requires_confirmation)
        self.assertEqual(
            parse_command("Poné el volumen de Spotify al 35 %").tool_name,
            "set_spotify_volume",
        )
        self.assertIsNone(parse_command("Poné el volumen de HyperX al 81 %"))

    def test_ai_and_plan_targets_are_strict_objects(self):
        raw = {
            "schema_version": 1,
            "intent": "SET_OUTPUT_VOLUME",
            "target": {"device": "HyperX", "percent": "35"},
        }
        proposal = validate_proposal(raw)
        self.assertEqual(
            proposal.target,
            OutputVolumeRequest("HyperX", "35"),
        )
        action = build_action_from_proposal(proposal)
        self.assertEqual(action.tool_name, "set_output_volume")
        self.assertEqual(
            action.arguments,
            {"device": "HyperX", "percent": "35"},
        )
        plan = validate_plan_proposal({
            "schema_version": 1,
            "steps": [
                {"intent": "OPEN_APPLICATION", "target": "spotify"},
                {"intent": "SET_OUTPUT_VOLUME", "target": raw["target"]},
            ],
        })
        self.assertEqual(plan.steps[-1].action.tool_name, "set_output_volume")

        invalid = (
            {"device": "HyperX", "percent": "81"},
            {"device": "HyperX", "percent": 35},
            {"device": "HyperX", "percent": "35", "extra": "x"},
            "HyperX:35",
        )
        for target in invalid:
            with self.subTest(target=target), self.assertRaises(ProposalValidationError):
                validate_proposal({**raw, "target": target})

    def test_executor_registration_and_read_only_cli_listing(self):
        class FakePlayback:
            has_active_session = False

            def __call__(self, _query):
                return ToolResult(True, "ok")

            def stop(self):
                return ToolResult(True, "ok")

            def resume(self):
                return ToolResult(True, "ok")

        backend = FakeOutputBackend((self.hyperx,))
        output = OutputVolumeTool(backend)
        executor, _ = build_executor(
            logging.getLogger(self.id()),
            FakePlayback(),
            output_volume_controller=output,
        )
        result = executor.execute(parse_command("Poné el volumen de HyperX al 35 %"))
        self.assertTrue(result.success)

        with patch(
            "desktop_agent.cli.WindowsCoreAudioBackend",
            return_value=backend,
        ), contextlib.redirect_stdout(io.StringIO()) as listing:
            self.assertEqual(main(["--audio-outputs"]), 0)
        self.assertIn("HyperX Cloud", listing.getvalue())
        self.assertNotIn("endpoint-hyperx", listing.getvalue())


if __name__ == "__main__":
    unittest.main()
