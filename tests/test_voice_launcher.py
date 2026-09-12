import contextlib
import io
import unittest

from desktop_agent.provider_config import (
    AI_ENABLED_ENV,
    AI_MONTHLY_BUDGET_ENV,
    OPENAI_API_KEY_ENV,
)
from desktop_agent.voice_transcription_config import (
    VOICE_TRANSCRIPTION_ENABLED_ENV,
)
from scripts.start_voice_gui import start_voice_gui


class VoiceLauncherTests(unittest.TestCase):
    def test_passes_hidden_credential_only_in_ephemeral_gui_environment(self) -> None:
        original = {"EXISTING": "value"}
        received = []

        result = start_voice_gui(
            original,
            secret_reader=lambda _prompt: "private-test-key",
            gui_runner=lambda environ: received.append(dict(environ)) or 7,
        )

        self.assertEqual(result, 7)
        self.assertEqual(original, {"EXISTING": "value"})
        self.assertEqual(received[0][OPENAI_API_KEY_ENV], "private-test-key")
        self.assertEqual(received[0][AI_ENABLED_ENV], "true")
        self.assertEqual(
            received[0][VOICE_TRANSCRIPTION_ENABLED_ENV],
            "true",
        )
        self.assertEqual(received[0][AI_MONTHLY_BUDGET_ENV], "1.00")

    def test_reuses_existing_key_and_preserves_explicit_budget(self) -> None:
        prompts = []
        received = []

        result = start_voice_gui(
            {
                OPENAI_API_KEY_ENV: "existing-test-key",
                AI_MONTHLY_BUDGET_ENV: "0.50",
            },
            secret_reader=lambda prompt: prompts.append(prompt) or "unused",
            gui_runner=lambda environ: received.append(dict(environ)) or 0,
        )

        self.assertEqual(result, 0)
        self.assertEqual(prompts, [])
        self.assertEqual(received[0][AI_MONTHLY_BUDGET_ENV], "0.50")
        self.assertEqual(received[0][AI_ENABLED_ENV], "true")

    def test_empty_or_interrupted_secret_never_starts_gui_or_echoes_value(self) -> None:
        for reader in (
            lambda _prompt: None,
            lambda _prompt: "   ",
            lambda _prompt: (_ for _ in ()).throw(EOFError()),
        ):
            with self.subTest(reader=reader):
                calls = []
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    result = start_voice_gui(
                        {},
                        secret_reader=reader,
                        gui_runner=lambda _environ: calls.append(True) or 0,
                    )

                self.assertEqual(result, 2)
                self.assertEqual(calls, [])
                self.assertNotIn("private", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
