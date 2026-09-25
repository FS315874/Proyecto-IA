import unittest

from desktop_agent.provider_config import (
    AI_ENABLED_ENV,
    AI_MONTHLY_BUDGET_ENV,
    AI_TIMEOUT_ENV,
    DEFAULT_MODEL,
    DEFAULT_MONTHLY_BUDGET_USD,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_TIMEOUT_SECONDS,
    InvalidProviderSettingError,
    MissingProviderCredentialError,
    OPENAI_API_KEY_ENV,
    ProviderConfig,
    load_provider_config,
)


class LoadProviderConfigTests(unittest.TestCase):
    def test_integration_is_disabled_by_default(self) -> None:
        config = load_provider_config({})

        self.assertFalse(config.enabled)
        self.assertEqual(config.provider_name, "openai")
        self.assertEqual(config.model, DEFAULT_MODEL)
        self.assertEqual(config.reasoning_effort, DEFAULT_REASONING_EFFORT)
        self.assertEqual(config.timeout_seconds, DEFAULT_TIMEOUT_SECONDS)
        self.assertEqual(config.monthly_budget_usd, DEFAULT_MONTHLY_BUDGET_USD)
        self.assertIsNone(config.api_key)

    def test_disabled_integration_ignores_provider_only_settings(self) -> None:
        config = load_provider_config(
            {
                AI_ENABLED_ENV: "off",
                AI_MONTHLY_BUDGET_ENV: "not-a-number",
                AI_TIMEOUT_ENV: "not-a-number",
                OPENAI_API_KEY_ENV: "test-secret-that-must-not-be-retained",
            }
        )

        self.assertFalse(config.enabled)
        self.assertEqual(config.timeout_seconds, DEFAULT_TIMEOUT_SECONDS)
        self.assertIsNone(config.api_key)

    def test_loads_valid_enabled_configuration(self) -> None:
        config = load_provider_config(
            {
                AI_ENABLED_ENV: "true",
                AI_MONTHLY_BUDGET_ENV: "0.75",
                AI_TIMEOUT_ENV: "2.5",
                OPENAI_API_KEY_ENV: "test-api-key",
            }
        )

        self.assertTrue(config.enabled)
        self.assertEqual(config.timeout_seconds, 2.5)
        self.assertEqual(config.monthly_budget_usd, 0.75)
        self.assertEqual(config.api_key, "test-api-key")

    def test_accepts_documented_boolean_values(self) -> None:
        for enabled_value in ("1", "TRUE", " yes ", "On"):
            with self.subTest(enabled_value=enabled_value):
                config = load_provider_config(
                    {
                        AI_ENABLED_ENV: enabled_value,
                        OPENAI_API_KEY_ENV: "test-api-key",
                    }
                )

                self.assertTrue(config.enabled)

    def test_rejects_invalid_enable_value_without_echoing_it(self) -> None:
        invalid_value = "secret-looking-invalid-value"

        with self.assertRaises(InvalidProviderSettingError) as context:
            load_provider_config({AI_ENABLED_ENV: invalid_value})

        self.assertEqual(context.exception.setting, AI_ENABLED_ENV)
        self.assertNotIn(invalid_value, str(context.exception))

    def test_requires_credential_only_when_enabled(self) -> None:
        for raw_key in (None, "", "   "):
            with self.subTest(raw_key=raw_key):
                environment = {AI_ENABLED_ENV: "true"}
                if raw_key is not None:
                    environment[OPENAI_API_KEY_ENV] = raw_key

                with self.assertRaises(MissingProviderCredentialError) as context:
                    load_provider_config(environment)

                self.assertEqual(context.exception.setting, OPENAI_API_KEY_ENV)

    def test_rejects_invalid_timeouts(self) -> None:
        invalid_timeouts = ("not-a-number", "nan", "inf", "0", "-1", "31")

        for invalid_timeout in invalid_timeouts:
            with self.subTest(invalid_timeout=invalid_timeout):
                with self.assertRaises(InvalidProviderSettingError) as context:
                    load_provider_config(
                        {
                            AI_ENABLED_ENV: "true",
                            AI_TIMEOUT_ENV: invalid_timeout,
                            OPENAI_API_KEY_ENV: "test-api-key",
                        }
                    )

                self.assertEqual(context.exception.setting, AI_TIMEOUT_ENV)

    def test_rejects_invalid_monthly_budgets(self) -> None:
        invalid_budgets = (
            "not-a-number",
            "nan",
            "inf",
            "0",
            "-1",
            "0.009",
            "1001",
        )

        for invalid_budget in invalid_budgets:
            with self.subTest(invalid_budget=invalid_budget):
                with self.assertRaises(InvalidProviderSettingError) as context:
                    load_provider_config(
                        {
                            AI_ENABLED_ENV: "true",
                            AI_MONTHLY_BUDGET_ENV: invalid_budget,
                            OPENAI_API_KEY_ENV: "test-api-key",
                        }
                    )

                self.assertEqual(
                    context.exception.setting,
                    AI_MONTHLY_BUDGET_ENV,
                )

    def test_redacts_api_key_from_config_representation(self) -> None:
        secret = "test-secret-that-must-not-appear"
        config = load_provider_config(
            {
                AI_ENABLED_ENV: "true",
                OPENAI_API_KEY_ENV: secret,
            }
        )

        self.assertNotIn(secret, repr(config))
        self.assertNotIn(secret, str(config))

    def test_redacts_invalid_api_key_from_error(self) -> None:
        secret = " test-secret-that-must-not-appear "

        with self.assertRaises(InvalidProviderSettingError) as context:
            load_provider_config(
                {
                    AI_ENABLED_ENV: "true",
                    OPENAI_API_KEY_ENV: secret,
                }
            )

        self.assertEqual(context.exception.setting, OPENAI_API_KEY_ENV)
        self.assertNotIn(secret, str(context.exception))


class ProviderConfigInvariantTests(unittest.TestCase):
    def test_revalidates_credential_on_direct_configuration(self) -> None:
        for raw_key, expected_error in (
            (None, MissingProviderCredentialError),
            ("", MissingProviderCredentialError),
            (7, InvalidProviderSettingError),
            (" test-api-key ", InvalidProviderSettingError),
        ):
            with self.subTest(raw_key=raw_key):
                with self.assertRaises(expected_error):
                    ProviderConfig(enabled=True, _api_key=raw_key)

    def test_rejects_secret_on_disabled_direct_configuration(self) -> None:
        with self.assertRaises(InvalidProviderSettingError):
            ProviderConfig(enabled=False, _api_key="test-api-key")


if __name__ == "__main__":
    unittest.main()
