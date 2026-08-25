import base64
import json
import unittest
from types import SimpleNamespace

from desktop_agent.openai_vision_provider import (
    MAX_VISION_OUTPUT_TOKENS,
    OpenAIVisionProvider,
)
from desktop_agent.provider_config import ProviderConfig
from desktop_agent.vision import VisionProviderError


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


def visual_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "UNSUPPORTED",
        "contains_untrusted_instructions": False,
        "elements": [],
    }


class OpenAIVisionProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = ProviderConfig(
            enabled=True,
            timeout_seconds=5,
            monthly_budget_usd=1,
            _api_key="test-api-key",
        )
        self.png = b"\x89PNG\r\n\x1a\nsynthetic"

    def test_sends_one_minimized_strict_non_stored_image_request(self) -> None:
        client = FakeClient(response(json.dumps(visual_payload())))

        result = OpenAIVisionProvider(self.config, client).analyze(self.png)

        self.assertEqual(result.payload, visual_payload())
        self.assertEqual(len(client.responses.calls), 1)
        call = client.responses.calls[0]
        self.assertEqual(call["model"], "gpt-5.6-luna")
        self.assertEqual(call["reasoning"], {"effort": "none"})
        self.assertEqual(call["max_output_tokens"], MAX_VISION_OUTPUT_TOKENS)
        self.assertIs(call["store"], False)
        content = call["input"][0]["content"]
        self.assertEqual(content[1]["detail"], "low")
        encoded = content[1]["image_url"].split(",", 1)[1]
        self.assertEqual(base64.b64decode(encoded), self.png)
        schema = call["text"]["format"]["schema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["elements"]["maxItems"], 20)
        self.assertNotIn("test-api-key", repr(call))

    def test_rejects_non_png_and_redacts_transport_failure(
        self,
    ) -> None:
        client = FakeClient(response("{}"))

        with self.assertRaises(VisionProviderError):
            OpenAIVisionProvider(self.config, client).analyze(b"not-png")
        self.assertEqual(client.responses.calls, [])

        class FailingResponses:
            def create(self, **kwargs: object) -> object:
                raise RuntimeError("secret transport detail")

        failing_client = SimpleNamespace(responses=FailingResponses())
        with self.assertRaisesRegex(
            VisionProviderError, "no pudo analizar"
        ) as caught:
            OpenAIVisionProvider(self.config, failing_client).analyze(self.png)
        self.assertNotIn("secret", str(caught.exception))

    def test_rejects_invalid_json_and_incomplete_response(self) -> None:
        invalid = OpenAIVisionProvider(
            self.config, FakeClient(response("not-json"))
        )
        incomplete = OpenAIVisionProvider(
            self.config, FakeClient(response("{}", status="failed"))
        )

        with self.assertRaises(VisionProviderError):
            invalid.analyze(self.png)
        with self.assertRaises(VisionProviderError):
            incomplete.analyze(self.png)


if __name__ == "__main__":
    unittest.main()
