import json
import unittest
from types import SimpleNamespace

from desktop_agent.interpretation import ProposalProviderError
from desktop_agent.openai_plan_provider import (
    MAX_PLAN_OUTPUT_TOKENS,
    OpenAIPlanProvider,
)
from desktop_agent.provider_config import ProviderConfig


class FakeResponses:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self.response


class FakeClient:
    def __init__(self, response: object) -> None:
        self.responses = FakeResponses(response)


def response(output_text: str, status: str = "completed") -> object:
    return SimpleNamespace(status=status, output_text=output_text, usage=None)


class OpenAIPlanProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = ProviderConfig(
            enabled=True,
            timeout_seconds=5,
            monthly_budget_usd=1,
            _api_key="test-api-key",
        )

    def test_sends_one_minimal_strict_plan_request(self) -> None:
        payload = {
            "schema_version": 1,
            "steps": [
                {"intent": "OPEN_URL", "target": "youtube"},
                {"intent": "OPEN_URL", "target": "github"},
            ],
        }
        client = FakeClient(response(json.dumps(payload)))

        result = OpenAIPlanProvider(self.config, client).propose("abrí dos sitios")

        self.assertEqual(result.payload, payload)
        self.assertEqual(len(client.responses.calls), 1)
        call = client.responses.calls[0]
        self.assertEqual(call["model"], "gpt-5.6-luna")
        self.assertEqual(call["input"], "abrí dos sitios")
        self.assertEqual(call["reasoning"], {"effort": "none"})
        self.assertEqual(call["max_output_tokens"], MAX_PLAN_OUTPUT_TOKENS)
        self.assertIs(call["store"], False)
        self.assertEqual(call["timeout"], 5.0)
        schema = call["text"]["format"]["schema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["steps"]["maxItems"], 5)
        self.assertFalse(
            schema["properties"]["steps"]["items"]["additionalProperties"]
        )
        self.assertIn(
            "RESUME_YOUTUBE",
            schema["properties"]["steps"]["items"]["properties"]["intent"][
                "enum"
            ],
        )
        self.assertIn(
            "SET_OUTPUT_VOLUME",
            schema["properties"]["steps"]["items"]["properties"]["intent"][
                "enum"
            ],
        )
        target_schema = schema["properties"]["steps"]["items"]["properties"]["target"]
        self.assertEqual(target_schema["anyOf"][2]["required"], ["device", "percent"])
        self.assertIn("video actual", call["instructions"])
        self.assertIn("Abrir Spotify", call["instructions"])
        self.assertIn("web o navegador", call["instructions"])
        self.assertNotIn("test-api-key", repr(call))

    def test_rejects_invalid_json_empty_input_and_incomplete_response(self) -> None:
        invalid_json = OpenAIPlanProvider(
            self.config, FakeClient(response("not-json"))
        )
        incomplete = OpenAIPlanProvider(
            self.config, FakeClient(response("{}", status="failed"))
        )
        empty = OpenAIPlanProvider(self.config, FakeClient(response("{}")))

        with self.assertRaises(ProposalProviderError):
            invalid_json.propose("una tarea")
        with self.assertRaises(ProposalProviderError):
            incomplete.propose("una tarea")
        with self.assertRaises(ProposalProviderError):
            empty.propose("   ")


if __name__ == "__main__":
    unittest.main()
