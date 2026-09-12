import unittest

from desktop_agent.provider_config import AI_MONTHLY_BUDGET_ENV, OPENAI_API_KEY_ENV
from desktop_agent.voice_transcription_config import (
    DEFAULT_TRANSCRIPTION_MODEL,
    InvalidVoiceTranscriptionSettingError,
    MissingVoiceTranscriptionCredentialError,
    VOICE_TRANSCRIPTION_ENABLED_ENV,
    VOICE_TRANSCRIPTION_TIMEOUT_ENV,
    load_voice_transcription_config,
)


class VoiceTranscriptionConfigTests(unittest.TestCase):
    def test_is_disabled_by_default_without_retaining_key(self) -> None:
        config = load_voice_transcription_config(
            {OPENAI_API_KEY_ENV: "private-test-key"}
        )

        self.assertFalse(config.enabled)
        self.assertIsNone(config.api_key)
        self.assertEqual(config.model, DEFAULT_TRANSCRIPTION_MODEL)

    def test_loads_explicit_opt_in_budget_timeout_and_key(self) -> None:
        config = load_voice_transcription_config(
            {
                VOICE_TRANSCRIPTION_ENABLED_ENV: "true",
                VOICE_TRANSCRIPTION_TIMEOUT_ENV: "12.5",
                AI_MONTHLY_BUDGET_ENV: "0.75",
                OPENAI_API_KEY_ENV: "private-test-key",
            }
        )

        self.assertTrue(config.enabled)
        self.assertEqual(config.timeout_seconds, 12.5)
        self.assertEqual(config.monthly_budget_usd, 0.75)
        self.assertEqual(config.api_key, "private-test-key")
        self.assertNotIn("private-test-key", repr(config))

    def test_enabled_voice_requires_key_without_echoing_values(self) -> None:
        with self.assertRaises(MissingVoiceTranscriptionCredentialError):
            load_voice_transcription_config(
                {VOICE_TRANSCRIPTION_ENABLED_ENV: "true"}
            )

        private = " invalid-private-key "
        with self.assertRaises(InvalidVoiceTranscriptionSettingError) as context:
            load_voice_transcription_config(
                {
                    VOICE_TRANSCRIPTION_ENABLED_ENV: "true",
                    OPENAI_API_KEY_ENV: private,
                }
            )
        self.assertNotIn(private, str(context.exception))

    def test_rejects_invalid_boolean_timeout_and_budget(self) -> None:
        cases = (
            {VOICE_TRANSCRIPTION_ENABLED_ENV: "PRIVATE-INVALID"},
            {
                VOICE_TRANSCRIPTION_ENABLED_ENV: "true",
                VOICE_TRANSCRIPTION_TIMEOUT_ENV: "0",
                OPENAI_API_KEY_ENV: "key",
            },
            {
                VOICE_TRANSCRIPTION_ENABLED_ENV: "true",
                AI_MONTHLY_BUDGET_ENV: "0.001",
                OPENAI_API_KEY_ENV: "key",
            },
        )
        for environ in cases:
            with self.subTest(environ=environ), self.assertRaises(
                InvalidVoiceTranscriptionSettingError
            ):
                load_voice_transcription_config(environ)


if __name__ == "__main__":
    unittest.main()
