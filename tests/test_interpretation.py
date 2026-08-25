import unittest

from desktop_agent.interpretation import (
    ActionProposal,
    HybridInterpreter,
    InterpretationPath,
    InterpretationStatus,
    ProposalIntent,
    ProposalProviderError,
    ProposalProviderResult,
    ProposalUsage,
    ProposalValidationError,
    build_action_from_proposal,
    validate_proposal,
)
from desktop_agent.models import Intent, RiskLevel


class FakeProposalProvider:
    def __init__(
        self,
        response: object,
        usage: ProposalUsage | None = None,
        estimated_cost_usd: float | None = None,
    ) -> None:
        self.response = response
        self.usage = usage
        self.estimated_cost_usd = estimated_cost_usd
        self.commands: list[str] = []

    def propose(self, command: str) -> ProposalProviderResult:
        self.commands.append(command)
        return ProposalProviderResult(
            self.response,
            self.usage,
            self.estimated_cost_usd,
        )


class FailingProposalProvider:
    def __init__(
        self,
        provider_result: ProposalProviderResult | None = None,
    ) -> None:
        self.provider_result = provider_result

    def propose(self, command: str) -> ProposalProviderResult:
        raise ProposalProviderError(
            "simulated provider failure",
            self.provider_result,
        )


class ValidateProposalTests(unittest.TestCase):
    def test_accepts_each_contract_intent(self) -> None:
        cases = (
            ("OPEN_URL", "youtube", ProposalIntent.OPEN_URL),
            ("OPEN_APPLICATION", "vscode", ProposalIntent.OPEN_APPLICATION),
            ("UNSUPPORTED", None, ProposalIntent.UNSUPPORTED),
        )

        for raw_intent, target, expected_intent in cases:
            with self.subTest(intent=raw_intent):
                proposal = validate_proposal(
                    {
                        "schema_version": 1,
                        "intent": raw_intent,
                        "target": target,
                    }
                )

                self.assertEqual(proposal.schema_version, 1)
                self.assertIs(proposal.intent, expected_intent)
                self.assertEqual(proposal.target, target)

    def test_rejects_invalid_object_shape(self) -> None:
        invalid_payloads = (
            [],
            {"schema_version": 1, "intent": "OPEN_URL"},
            {
                "schema_version": 1,
                "intent": "OPEN_URL",
                "target": "youtube",
                "tool_name": "open_url",
            },
            [
                {
                    "schema_version": 1,
                    "intent": "OPEN_URL",
                    "target": "youtube",
                }
            ],
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ProposalValidationError):
                    validate_proposal(payload)

    def test_rejects_unknown_or_mistyped_values(self) -> None:
        invalid_payloads = (
            {"schema_version": 2, "intent": "OPEN_URL", "target": "youtube"},
            {"schema_version": True, "intent": "OPEN_URL", "target": "youtube"},
            {"schema_version": 1, "intent": "DELETE_FILE", "target": "notes"},
            {"schema_version": 1, "intent": 7, "target": "youtube"},
            {"schema_version": 1, "intent": "OPEN_URL", "target": 7},
            {"schema_version": 1, "intent": "OPEN_URL", "target": " youtube"},
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ProposalValidationError):
                    validate_proposal(payload)

    def test_rejects_incoherent_target(self) -> None:
        invalid_payloads = (
            {"schema_version": 1, "intent": "OPEN_URL", "target": None},
            {"schema_version": 1, "intent": "UNSUPPORTED", "target": "youtube"},
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ProposalValidationError):
                    validate_proposal(payload)


class BuildActionFromProposalTests(unittest.TestCase):
    def test_builds_url_action_using_only_local_data(self) -> None:
        action = build_action_from_proposal(
            ActionProposal(1, ProposalIntent.OPEN_URL, "youtube")
        )

        self.assertIsNotNone(action)
        assert action is not None
        self.assertIs(action.intent, Intent.OPEN_URL)
        self.assertEqual(action.tool_name, "open_url")
        self.assertEqual(action.arguments, {"url": "https://www.youtube.com/"})
        self.assertIs(action.risk_level, RiskLevel.SAFE)
        self.assertFalse(action.requires_confirmation)

    def test_builds_application_action_using_only_local_data(self) -> None:
        action = build_action_from_proposal(
            ActionProposal(1, ProposalIntent.OPEN_APPLICATION, "vscode")
        )

        self.assertIsNotNone(action)
        assert action is not None
        self.assertIs(action.intent, Intent.OPEN_APPLICATION)
        self.assertEqual(action.tool_name, "open_application")
        self.assertEqual(action.arguments, {"name": "vscode"})
        self.assertIs(action.risk_level, RiskLevel.SAFE)
        self.assertFalse(action.requires_confirmation)

    def test_rejects_destinations_outside_the_matching_catalog(self) -> None:
        invalid_proposals = (
            ActionProposal(1, ProposalIntent.OPEN_URL, "vscode"),
            ActionProposal(1, ProposalIntent.OPEN_APPLICATION, "youtube"),
            ActionProposal(1, ProposalIntent.OPEN_APPLICATION, "powershell"),
        )

        for proposal in invalid_proposals:
            with self.subTest(proposal=proposal):
                with self.assertRaises(ProposalValidationError):
                    build_action_from_proposal(proposal)

    def test_revalidates_manually_constructed_proposals(self) -> None:
        invalid_proposals = (
            ActionProposal(2, ProposalIntent.OPEN_URL, "youtube"),
            ActionProposal(True, ProposalIntent.OPEN_URL, "youtube"),
            ActionProposal(1, ProposalIntent.OPEN_URL, " youtube"),
        )

        for proposal in invalid_proposals:
            with self.subTest(proposal=proposal):
                with self.assertRaises(ProposalValidationError):
                    build_action_from_proposal(proposal)

    def test_unsupported_proposal_does_not_build_an_action(self) -> None:
        action = build_action_from_proposal(
            ActionProposal(1, ProposalIntent.UNSUPPORTED, None)
        )

        self.assertIsNone(action)


class HybridInterpreterTests(unittest.TestCase):
    def test_prioritizes_deterministic_parser_without_calling_provider(self) -> None:
        provider = FakeProposalProvider(
            {"schema_version": 1, "intent": "UNSUPPORTED", "target": None}
        )
        clock_values = iter((10.0, 10.012))
        interpreter = HybridInterpreter(
            provider,
            clock=lambda: next(clock_values),
            provider_name="fake",
            provider_model="fake-model",
        )

        result = interpreter.interpret_detailed("abrir youtube")

        self.assertIsNotNone(result.action)
        assert result.action is not None
        self.assertEqual(
            result.action.arguments,
            {"url": "https://www.youtube.com/"},
        )
        self.assertEqual(provider.commands, [])
        self.assertIs(result.path, InterpretationPath.DETERMINISTIC)
        self.assertIs(result.status, InterpretationStatus.SUCCESS)
        self.assertAlmostEqual(result.duration_ms, 12.0)
        self.assertTrue(result.provider_configured)
        self.assertEqual(result.provider_name, "fake")
        self.assertEqual(result.provider_model, "fake-model")
        self.assertIsNone(result.usage)

    def test_builds_action_from_fake_provider_fallback(self) -> None:
        usage = ProposalUsage(
            input_tokens=25,
            output_tokens=5,
            total_tokens=30,
        )
        provider = FakeProposalProvider(
            {
                "schema_version": 1,
                "intent": "OPEN_APPLICATION",
                "target": "calculator",
            },
            usage=usage,
            estimated_cost_usd=0.000011,
        )
        clock_values = iter((20.0, 20.025))
        interpreter = HybridInterpreter(
            provider,
            clock=lambda: next(clock_values),
            provider_name="fake",
            provider_model="fake-model",
        )

        result = interpreter.interpret_detailed("quiero usar la calculadora")

        self.assertIsNotNone(result.action)
        assert result.action is not None
        self.assertEqual(result.action.arguments, {"name": "calculator"})
        self.assertEqual(provider.commands, ["quiero usar la calculadora"])
        self.assertIs(result.path, InterpretationPath.EXTERNAL)
        self.assertIs(result.status, InterpretationStatus.SUCCESS)
        self.assertAlmostEqual(result.duration_ms, 25.0)
        self.assertIs(result.usage, usage)
        self.assertEqual(result.estimated_cost_usd, 0.000011)

    def test_returns_none_when_provider_is_disabled(self) -> None:
        clock_values = iter((30.0, 30.001))
        interpreter = HybridInterpreter(clock=lambda: next(clock_values))

        result = interpreter.interpret_detailed("quiero usar la calculadora")

        self.assertIsNone(result.action)
        self.assertIs(result.path, InterpretationPath.DETERMINISTIC)
        self.assertIs(result.status, InterpretationStatus.UNSUPPORTED)
        self.assertAlmostEqual(result.duration_ms, 1.0)
        self.assertFalse(result.provider_configured)

    def test_returns_none_for_invalid_or_unsupported_provider_output(self) -> None:
        responses = (
            (
                {"schema_version": 1, "intent": "UNSUPPORTED", "target": None},
                InterpretationStatus.UNSUPPORTED,
            ),
            (
                {
                    "schema_version": 1,
                    "intent": "OPEN_URL",
                    "target": "unknown",
                },
                InterpretationStatus.INVALID_PROPOSAL,
            ),
            (
                {
                    "schema_version": 1,
                    "intent": "OPEN_URL",
                    "target": "youtube",
                    "tool_name": "open_url",
                },
                InterpretationStatus.INVALID_PROPOSAL,
            ),
            (
                [
                    {
                        "schema_version": 1,
                        "intent": "OPEN_URL",
                        "target": "youtube",
                    }
                ],
                InterpretationStatus.INVALID_PROPOSAL,
            ),
        )

        for response, expected_status in responses:
            with self.subTest(response=response):
                interpreter = HybridInterpreter(FakeProposalProvider(response))
                result = interpreter.interpret_detailed("un comando libre")

                self.assertIsNone(result.action)
                self.assertIs(result.path, InterpretationPath.EXTERNAL)
                self.assertIs(result.status, expected_status)

    def test_returns_none_when_provider_reports_a_failure(self) -> None:
        usage = ProposalUsage(
            input_tokens=10,
            output_tokens=2,
            total_tokens=12,
        )
        clock_values = iter((40.0, 40.003))
        interpreter = HybridInterpreter(
            FailingProposalProvider(
                ProposalProviderResult(
                    payload=None,
                    usage=usage,
                    estimated_cost_usd=0.0000044,
                )
            ),
            clock=lambda: next(clock_values),
        )

        result = interpreter.interpret_detailed("un comando libre")

        self.assertIsNone(result.action)
        self.assertIs(result.path, InterpretationPath.EXTERNAL)
        self.assertIs(result.status, InterpretationStatus.PROVIDER_ERROR)
        self.assertAlmostEqual(result.duration_ms, 3.0)
        self.assertIs(result.usage, usage)
        self.assertEqual(result.estimated_cost_usd, 0.0000044)


if __name__ == "__main__":
    unittest.main()
