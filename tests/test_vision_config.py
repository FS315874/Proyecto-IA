import unittest

from desktop_agent.vision_config import (
    VisionConfigurationError,
    load_vision_provider_config,
)


class VisionConfigTests(unittest.TestCase):
    def test_visual_sharing_is_disabled_by_default_and_separate_from_ai(self) -> None:
        self.assertIsNone(load_vision_provider_config({}))
        self.assertIsNone(
            load_vision_provider_config(
                {
                    "DESKTOP_AGENT_AI_ENABLED": "true",
                    "OPENAI_API_KEY": "test-key",
                }
            )
        )

    def test_requires_both_explicit_opt_ins_and_a_credential(self) -> None:
        with self.assertRaises(VisionConfigurationError):
            load_vision_provider_config(
                {"DESKTOP_AGENT_VISION_ENABLED": "true"}
            )

        config = load_vision_provider_config(
            {
                "DESKTOP_AGENT_VISION_ENABLED": "true",
                "DESKTOP_AGENT_AI_ENABLED": "true",
                "OPENAI_API_KEY": "test-key",
            }
        )
        self.assertIsNotNone(config)
        assert config is not None
        self.assertTrue(config.enabled)

    def test_rejects_unknown_boolean_without_echoing_it(self) -> None:
        secret = "not-a-boolean-secret"
        with self.assertRaises(VisionConfigurationError) as caught:
            load_vision_provider_config(
                {"DESKTOP_AGENT_VISION_ENABLED": secret}
            )
        self.assertNotIn(secret, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
