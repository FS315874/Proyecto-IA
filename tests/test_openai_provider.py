import json
import sys
import types
import unittest
from unittest.mock import patch

from desktop_agent.interpretation import (
    HybridInterpreter,
    ProposalProviderError,
    ProposalProviderResult,
    ProposalUsage,
)
from desktop_agent.openai_provider import (
    MAX_COMMAND_CHARACTERS,
    MAX_OUTPUT_TOKENS,
    OpenAIProposalProvider,
)
from desktop_agent.provider_config import ProviderConfig, ProviderConfigurationError


class FakeResponse:
    def __init__(
        self,
        output_text: object,
        status: object = "completed",
        usage: object | None = None,
    ) -> None:
        self.output_text = output_text
        self.status = status
        self.usage = usage


class FakeResponsesResource:
    def __init__(
        self,
        response: object | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


class FakeClient:
    def __init__(self, responses: FakeResponsesResource) -> None:
        self.responses = responses


def enabled_config(
    api_key: str = "test-api-key",
    timeout_seconds: float = 2.5,
) -> ProviderConfig:
    return ProviderConfig(
        enabled=True,
        timeout_seconds=timeout_seconds,
        _api_key=api_key,
    )


def provider_with_response(
    payload: object,
    *,
    status: object = "completed",
    usage: object | None = None,
) -> tuple[OpenAIProposalProvider, FakeResponsesResource]:
    responses = FakeResponsesResource(
        FakeResponse(json.dumps(payload), status=status, usage=usage)
    )
    provider = OpenAIProposalProvider(enabled_config(), FakeClient(responses))
    return provider, responses


class OpenAIProposalProviderTests(unittest.TestCase):
    def test_sends_one_minimal_structured_request(self) -> None:
        payload = {
            "schema_version": 1,
            "intent": "OPEN_URL",
            "target": "youtube",
        }
        provider, responses = provider_with_response(payload)

        result = provider.propose("  poneme youtube  ")

        self.assertIsInstance(result, ProposalProviderResult)
        self.assertEqual(result.payload, payload)
        self.assertIsNone(result.usage)
        self.assertIsNone(result.estimated_cost_usd)
        self.assertEqual(len(responses.calls), 1)
        request = responses.calls[0]
        self.assertEqual(request["model"], "gpt-5.6-luna")
        self.assertEqual(request["input"], "poneme youtube")
        self.assertEqual(request["reasoning"], {"effort": "none"})
        self.assertEqual(request["max_output_tokens"], MAX_OUTPUT_TOKENS)
        self.assertEqual(request["timeout"], 2.5)
        self.assertIs(request["store"], False)
        self.assertNotIn("test-api-key", repr(request))
        self.assertEqual(
            set(request),
            {
                "model",
                "instructions",
                "input",
                "reasoning",
                "text",
                "max_output_tokens",
                "store",
                "timeout",
            },
        )

        instructions = request["instructions"]
        self.assertIsInstance(instructions, str)
        assert isinstance(instructions, str)
        for canonical_key in (
            "youtube",
            "google",
            "github",
            "chrome",
            "vscode",
            "calculator",
            "steam",
            "voicemeeter",
            "league_of_legends",
            "god_of_war_ragnarok",
        ):
            self.assertIn(canonical_key, instructions)
        self.assertIn("pausar", instructions)
        self.assertIn("reanudar", instructions)
        self.assertIn("Nunca conviertas un control", instructions)
        self.assertIn("abrir Spotify", instructions)
        self.assertIn("web o navegador", instructions)
        self.assertNotIn("https://", instructions)

    def test_extracts_numeric_usage_and_estimates_current_model_cost(self) -> None:
        usage = types.SimpleNamespace(
            input_tokens=1_000,
            output_tokens=100,
            total_tokens=1_100,
            input_tokens_details=types.SimpleNamespace(
                cached_tokens=200,
                cache_write_tokens=100,
            ),
        )
        provider, _ = provider_with_response(
            {"schema_version": 1, "intent": "UNSUPPORTED", "target": None},
            usage=usage,
        )

        result = provider.propose("una orden libre")

        self.assertEqual(
            result.usage,
            ProposalUsage(
                input_tokens=1_000,
                output_tokens=100,
                total_tokens=1_100,
                cached_input_tokens=200,
                cache_write_tokens=100,
            ),
        )
        self.assertAlmostEqual(result.estimated_cost_usd, 0.000289)

    def test_ignores_invalid_usage_without_rejecting_valid_payload(self) -> None:
        invalid_usage = types.SimpleNamespace(
            input_tokens="private-text",
            output_tokens=5,
            total_tokens=5,
            input_tokens_details=None,
        )
        payload = {
            "schema_version": 1,
            "intent": "OPEN_URL",
            "target": "youtube",
        }
        provider, _ = provider_with_response(payload, usage=invalid_usage)

        result = provider.propose("una orden libre")

        self.assertEqual(result.payload, payload)
        self.assertIsNone(result.usage)
        self.assertIsNone(result.estimated_cost_usd)

    def test_uses_a_strict_closed_schema(self) -> None:
        provider, responses = provider_with_response(
            {"schema_version": 1, "intent": "UNSUPPORTED", "target": None}
        )

        provider.propose("una orden libre")

        text_config = responses.calls[0]["text"]
        self.assertIsInstance(text_config, dict)
        assert isinstance(text_config, dict)
        output_format = text_config["format"]
        self.assertIsInstance(output_format, dict)
        assert isinstance(output_format, dict)
        self.assertEqual(output_format["type"], "json_schema")
        self.assertIs(output_format["strict"], True)
        schema = output_format["schema"]
        self.assertIsInstance(schema, dict)
        assert isinstance(schema, dict)
        self.assertEqual(
            schema["required"],
            ["schema_version", "intent", "target"],
        )
        self.assertIs(schema["additionalProperties"], False)
        properties = schema["properties"]
        self.assertIsInstance(properties, dict)
        assert isinstance(properties, dict)
        self.assertEqual(properties["schema_version"]["enum"], [1])
        self.assertEqual(
            properties["intent"]["enum"],
            [
                "OPEN_URL",
                "OPEN_APPLICATION",
                "OPEN_APPROVED_TARGET",
                "PLAY_YOUTUBE",
                "STOP_YOUTUBE",
                "RESUME_YOUTUBE",
                "PLAY_SPOTIFY_TRACK",
                "PLAY_SPOTIFY_PLAYLIST",
                "SEARCH_SPOTIFY_TRACK",
                "PAUSE_SPOTIFY",
                "RESUME_SPOTIFY",
                "NEXT_SPOTIFY",
                "PREVIOUS_SPOTIFY",
                "SET_SPOTIFY_VOLUME",
                "SET_OUTPUT_VOLUME",
                "UNSUPPORTED",
            ],
        )
        target_variants = properties["target"]["anyOf"]
        self.assertEqual(target_variants[0], {"type": "string"})
        self.assertEqual(target_variants[1], {"type": "null"})
        self.assertEqual(
            target_variants[2]["required"],
            ["device", "percent"],
        )
        self.assertIs(target_variants[2]["additionalProperties"], False)

    def test_rejects_invalid_command_without_calling_provider(self) -> None:
        provider, responses = provider_with_response(
            {"schema_version": 1, "intent": "UNSUPPORTED", "target": None}
        )

        for command in ("   ", None, "x" * (MAX_COMMAND_CHARACTERS + 1)):
            with self.subTest(command=command):
                with self.assertRaises(ProposalProviderError):
                    provider.propose(command)

        self.assertEqual(responses.calls, [])

    def test_wraps_transport_failures_without_leaking_details(self) -> None:
        secret = "test-secret-from-transport"
        errors = (
            TimeoutError(secret),
            ConnectionError(secret),
            RuntimeError(secret),
        )

        for error in errors:
            with self.subTest(error=type(error).__name__):
                responses = FakeResponsesResource(error=error)
                provider = OpenAIProposalProvider(
                    enabled_config(),
                    FakeClient(responses),
                )

                with self.assertRaises(ProposalProviderError) as context:
                    provider.propose("una orden libre")

                self.assertNotIn(secret, str(context.exception))
                self.assertEqual(len(responses.calls), 1)

    def test_rejects_non_completed_responses(self) -> None:
        usage = types.SimpleNamespace(
            input_tokens=20,
            output_tokens=4,
            total_tokens=24,
            input_tokens_details=None,
        )
        for status in ("failed", "incomplete", None):
            with self.subTest(status=status):
                provider, _ = provider_with_response(
                    {
                        "schema_version": 1,
                        "intent": "UNSUPPORTED",
                        "target": None,
                    },
                    status=status,
                    usage=usage,
                )

                with self.assertRaises(ProposalProviderError) as context:
                    provider.propose("una orden libre")

                telemetry = context.exception.provider_result
                self.assertIsNotNone(telemetry)
                assert telemetry is not None
                self.assertEqual(telemetry.payload, None)
                self.assertEqual(
                    telemetry.usage,
                    ProposalUsage(20, 4, 24),
                )
                self.assertAlmostEqual(
                    telemetry.estimated_cost_usd,
                    0.0000088,
                )

    def test_rejects_empty_or_invalid_json(self) -> None:
        invalid_outputs = (None, "", "   ", "not-json", "NaN")

        for output_text in invalid_outputs:
            with self.subTest(output_text=output_text):
                responses = FakeResponsesResource(FakeResponse(output_text))
                provider = OpenAIProposalProvider(
                    enabled_config(),
                    FakeClient(responses),
                )

                with self.assertRaises(ProposalProviderError):
                    provider.propose("una orden libre")

    def test_unknown_schema_version_fails_safely_in_domain(self) -> None:
        provider, responses = provider_with_response(
            {"schema_version": 2, "intent": "OPEN_URL", "target": "youtube"}
        )
        interpreter = HybridInterpreter(
            provider,
            deterministic_parser=lambda command: None,
        )

        action = interpreter.interpret("poneme youtube")

        self.assertIsNone(action)
        self.assertEqual(len(responses.calls), 1)

    def test_valid_proposal_still_uses_local_action_builder(self) -> None:
        provider, _ = provider_with_response(
            {
                "schema_version": 1,
                "intent": "OPEN_APPLICATION",
                "target": "calculator",
            }
        )
        interpreter = HybridInterpreter(
            provider,
            deterministic_parser=lambda command: None,
        )

        action = interpreter.interpret("quiero usar la calculadora")

        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual(action.tool_name, "open_application")
        self.assertEqual(action.arguments, {"name": "calculator"})

    def test_rejects_disabled_configuration(self) -> None:
        with self.assertRaises(ProviderConfigurationError):
            OpenAIProposalProvider(ProviderConfig(enabled=False))

    def test_default_client_disables_sdk_retries(self) -> None:
        captured: dict[str, object] = {}
        responses = FakeResponsesResource(
            FakeResponse(
                json.dumps(
                    {
                        "schema_version": 1,
                        "intent": "UNSUPPORTED",
                        "target": None,
                    }
                )
            )
        )

        def fake_openai(**kwargs: object) -> FakeClient:
            captured.update(kwargs)
            return FakeClient(responses)

        fake_module = types.ModuleType("openai")
        fake_module.OpenAI = fake_openai
        with patch.dict(sys.modules, {"openai": fake_module}):
            provider = OpenAIProposalProvider(enabled_config())
            self.assertEqual(captured, {})
            provider.propose("una orden libre")

        self.assertEqual(
            captured,
            {
                "api_key": "test-api-key",
                "timeout": 2.5,
                "max_retries": 0,
            },
        )

    def test_default_client_failure_does_not_leak_details(self) -> None:
        secret = "test-secret-from-client-construction"

        def failing_openai(**kwargs: object) -> FakeClient:
            raise RuntimeError(secret)

        fake_module = types.ModuleType("openai")
        fake_module.OpenAI = failing_openai
        with patch.dict(sys.modules, {"openai": fake_module}):
            provider = OpenAIProposalProvider(enabled_config())
            with self.assertRaises(ProposalProviderError) as context:
                provider.propose("una orden libre")

        self.assertNotIn(secret, str(context.exception))


if __name__ == "__main__":
    unittest.main()
