import unittest

from desktop_agent.interpretation import (
    ActionProposal,
    HybridInterpreter,
    ProposalIntent,
    ProposalProviderError,
    ProposalValidationError,
    build_action_from_proposal,
    validate_proposal,
)
from desktop_agent.models import Intent, RiskLevel


class FakeProposalProvider:
    def __init__(self, response: object) -> None:
        self.response = response
        self.commands: list[str] = []

    def propose(self, command: str) -> object:
        self.commands.append(command)
        return self.response


class FailingProposalProvider:
    def propose(self, command: str) -> object:
        raise ProposalProviderError("simulated provider failure")


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
        interpreter = HybridInterpreter(provider)

        action = interpreter.interpret("abrir youtube")

        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual(action.arguments, {"url": "https://www.youtube.com/"})
        self.assertEqual(provider.commands, [])

    def test_builds_action_from_fake_provider_fallback(self) -> None:
        provider = FakeProposalProvider(
            {
                "schema_version": 1,
                "intent": "OPEN_APPLICATION",
                "target": "calculator",
            }
        )
        interpreter = HybridInterpreter(provider)

        action = interpreter.interpret("quiero usar la calculadora")

        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual(action.arguments, {"name": "calculator"})
        self.assertEqual(provider.commands, ["quiero usar la calculadora"])

    def test_returns_none_when_provider_is_disabled(self) -> None:
        interpreter = HybridInterpreter()

        self.assertIsNone(interpreter.interpret("quiero usar la calculadora"))

    def test_returns_none_for_invalid_or_unsupported_provider_output(self) -> None:
        invalid_responses = (
            {"schema_version": 1, "intent": "UNSUPPORTED", "target": None},
            {"schema_version": 1, "intent": "OPEN_URL", "target": "unknown"},
            {
                "schema_version": 1,
                "intent": "OPEN_URL",
                "target": "youtube",
                "tool_name": "open_url",
            },
            [{"schema_version": 1, "intent": "OPEN_URL", "target": "youtube"}],
        )

        for response in invalid_responses:
            with self.subTest(response=response):
                interpreter = HybridInterpreter(FakeProposalProvider(response))
                self.assertIsNone(interpreter.interpret("un comando libre"))

    def test_returns_none_when_provider_reports_a_failure(self) -> None:
        interpreter = HybridInterpreter(FailingProposalProvider())

        self.assertIsNone(interpreter.interpret("un comando libre"))


if __name__ == "__main__":
    unittest.main()
