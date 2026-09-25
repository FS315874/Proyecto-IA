import io
import logging
import threading
import unittest

from desktop_agent.executor import ActionExecutionError, ActionExecutor
from desktop_agent.models import Action, Intent, RiskLevel, ToolResult
from desktop_agent.permissions import (
    AuthorizationStatus,
    CapabilityRule,
    ConfirmationChannel,
    ConfirmationDecision,
    Effect,
    PermissionBroker,
    PolicyDisposition,
    PolicyError,
    PolicyLimits,
    PolicyRegistry,
    PolicySubject,
    PreparationStatus,
)


class MutableClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class SequenceFactory:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.count = 0

    def __call__(self) -> str:
        self.count += 1
        return f"{self.prefix}-{self.count}"


def action_for(
    capability: str,
    *,
    target: str = "record-qa-001",
    content: str = "Ficticio privado",
) -> Action:
    definitions = {
        "safe-open": (
            Intent.OPEN_URL,
            "qa_open",
            {"target": target},
            RiskLevel.SAFE,
            False,
        ),
        "type-text": (
            Intent.DESKTOP_INPUT,
            "qa_shared_tool",
            {"target": target, "content": content},
            RiskLevel.CAUTION,
            True,
        ),
        "modify-record": (
            Intent.MODIFY_LOCAL_DATA,
            "qa_shared_tool",
            {"target": target, "content": content},
            RiskLevel.CAUTION,
            True,
        ),
        "send-data": (
            Intent.SEND_EXTERNAL_DATA,
            "qa_send",
            {"target": target, "content": content},
            RiskLevel.DANGEROUS,
            True,
        ),
        "blocked-shell": (
            Intent.SYSTEM_CHANGE,
            "qa_shell",
            {"target": target, "content": content},
            RiskLevel.DANGEROUS,
            True,
        ),
    }
    intent, tool, arguments, risk, confirmation = definitions[capability]
    return Action(intent, tool, arguments, risk, confirmation)


def subject_for(
    capability: str,
    *,
    action_id: str = "action-qa-001",
    target: str = "record-qa-001",
    content: str = "Ficticio privado",
) -> PolicySubject:
    return PolicySubject(
        action_id,
        capability,
        action_for(capability, target=target, content=content),
    )


def registry() -> PolicyRegistry:
    shared_args = ("target", "content")
    return PolicyRegistry(
        (
            CapabilityRule(
                "safe-open",
                Effect.OPEN_KNOWN_RESOURCE,
                (Intent.OPEN_URL,),
                ("qa_open",),
                ("target",),
                "target",
            ),
            CapabilityRule(
                "type-text",
                Effect.TYPE_NON_SENSITIVE,
                (Intent.DESKTOP_INPUT,),
                ("qa_shared_tool",),
                shared_args,
                "target",
            ),
            CapabilityRule(
                "modify-record",
                Effect.MODIFY_LOCAL_DATA,
                (Intent.MODIFY_LOCAL_DATA,),
                ("qa_shared_tool",),
                shared_args,
                "target",
            ),
            CapabilityRule(
                "send-data",
                Effect.SEND_EXTERNAL_DATA,
                (Intent.SEND_EXTERNAL_DATA,),
                ("qa_send",),
                shared_args,
                "target",
            ),
            CapabilityRule(
                "blocked-shell",
                Effect.EXECUTE_ARBITRARY_CODE,
                (Intent.SYSTEM_CHANGE,),
                ("qa_shell",),
                shared_args,
                "target",
            ),
        )
    )


class PermissionTestSupport:
    def setUp(self) -> None:
        self.clock = MutableClock()
        self.stream = io.StringIO()
        self.logger = logging.Logger("permission-test")
        self.logger.addHandler(logging.StreamHandler(self.stream))
        self.requests = SequenceFactory("request")
        self.challenges = SequenceFactory("challenge")
        self.tokens = SequenceFactory("grant")
        self.broker = PermissionBroker(
            registry(),
            self.logger,
            PolicyLimits(5, 100),
            self.clock,
            self.requests,
            self.challenges,
            self.tokens,
        )

    def prepare(
        self,
        subject: PolicySubject,
        channels: tuple[ConfirmationChannel, ...] = (
            ConfirmationChannel.LOCAL,
        ),
    ):
        preparation = self.broker.prepare(subject, channels)
        self.assertEqual(preparation.status, PreparationStatus.PENDING)
        self.assertIsNotNone(preparation.request)
        return preparation

    @staticmethod
    def decision(
        preparation,
        *,
        approved: bool = True,
        channel: ConfirmationChannel = ConfirmationChannel.LOCAL,
        challenge: str | None = None,
    ) -> ConfirmationDecision:
        request = preparation.request
        assert request is not None
        return ConfirmationDecision(
            request.request_id,
            challenge or request.challenge,
            request.subject_fingerprint,
            approved,
            channel,
        )

    def authorize(self, subject: PolicySubject):
        preparation = self.prepare(subject)
        result = self.broker.authorize(
            subject,
            self.decision(preparation),
        )
        self.assertEqual(result.status, AuthorizationStatus.AUTHORIZED)
        self.assertIsNotNone(result.token)
        return preparation, result.token


class PolicyRegistryTests(unittest.TestCase):
    def test_taxonomy_is_derived_from_effect_not_shared_tool_name(self) -> None:
        policy = registry()
        typed = policy.assess(subject_for("type-text"))
        modified = policy.assess(subject_for("modify-record"))
        sent = policy.assess(subject_for("send-data"))

        self.assertEqual(typed.effect, Effect.TYPE_NON_SENSITIVE)
        self.assertEqual(modified.effect, Effect.MODIFY_LOCAL_DATA)
        self.assertEqual(typed.disposition, PolicyDisposition.CONFIRM)
        self.assertEqual(sent.risk_level, RiskLevel.DANGEROUS)

    def test_safe_and_always_blocked_effects_are_explicit(self) -> None:
        policy = registry()

        self.assertEqual(
            policy.assess(subject_for("safe-open")).disposition,
            PolicyDisposition.ALLOW,
        )
        blocked = policy.assess(subject_for("blocked-shell"))
        self.assertEqual(blocked.disposition, PolicyDisposition.BLOCK)
        self.assertEqual(blocked.risk_level, RiskLevel.DANGEROUS)

    def test_every_never_allowed_effect_is_blocked(self) -> None:
        blocked_effects = (
            Effect.HANDLE_CREDENTIALS,
            Effect.ALTER_SECURITY,
            Effect.EXECUTE_ARBITRARY_CODE,
            Effect.IRREVERSIBLE_DELETION,
            Effect.FINANCIAL_TRANSACTION,
        )
        action = Action(
            Intent.SYSTEM_CHANGE,
            "qa_blocked",
            {"target": "fictitious-target"},
            RiskLevel.DANGEROUS,
            True,
        )

        for index, effect in enumerate(blocked_effects):
            with self.subTest(effect=effect):
                policy = PolicyRegistry(
                    (
                        CapabilityRule(
                            f"blocked-{index}",
                            effect,
                            (Intent.SYSTEM_CHANGE,),
                            ("qa_blocked",),
                            ("target",),
                            "target",
                        ),
                    )
                )
                assessment = policy.assess(
                    PolicySubject(
                        f"action-blocked-{index}",
                        f"blocked-{index}",
                        action,
                    )
                )
                self.assertEqual(
                    assessment.disposition,
                    PolicyDisposition.BLOCK,
                )

    def test_rejects_metadata_tool_intent_or_arguments_outside_rule(self) -> None:
        policy = registry()
        valid = action_for("type-text")
        invalid_actions = (
            Action(
                valid.intent,
                valid.tool_name,
                valid.arguments,
                RiskLevel.SAFE,
                False,
            ),
            Action(
                Intent.OPEN_URL,
                valid.tool_name,
                valid.arguments,
                valid.risk_level,
                True,
            ),
            Action(
                valid.intent,
                "other_tool",
                valid.arguments,
                valid.risk_level,
                True,
            ),
            Action(
                valid.intent,
                valid.tool_name,
                {"target": "record-qa-001", "extra": "value"},
                valid.risk_level,
                True,
            ),
        )

        for index, action in enumerate(invalid_actions):
            with self.subTest(index=index), self.assertRaises(PolicyError):
                policy.assess(PolicySubject(f"action-{index}", "type-text", action))

    def test_unregistered_capability_fails_closed(self) -> None:
        with self.assertRaises(PolicyError):
            registry().assess(
                PolicySubject(
                    "action-unknown",
                    "unknown-capability",
                    action_for("type-text"),
                )
            )


class PermissionBrokerTests(PermissionTestSupport, unittest.TestCase):
    def test_safe_action_and_blocked_effect_have_distinct_results(self) -> None:
        safe = self.broker.prepare(subject_for("safe-open"))
        blocked_subject = subject_for("blocked-shell")
        blocked = self.broker.prepare(blocked_subject)

        self.assertEqual(safe.status, PreparationStatus.NOT_REQUIRED)
        self.assertIsNone(safe.request)
        self.assertEqual(blocked.status, PreparationStatus.BLOCKED)
        self.assertIsNone(blocked.request)
        executor = ActionExecutor(
            {"qa_shell": lambda **_: ToolResult(True, "unexpected")},
            self.logger,
            self.broker,
        )
        with self.assertRaises(ActionExecutionError):
            executor.execute(blocked_subject.action)

    def test_request_exposes_exact_target_but_logs_no_values_or_secrets(self) -> None:
        private = "PRIVATE-CONTENT-984"
        target = "destination-qa-123"
        prepared = self.prepare(
            subject_for("type-text", target=target, content=private)
        )
        request = prepared.request
        assert request is not None

        self.assertEqual(request.destination, target)
        self.assertEqual(request.argument_names, ("content", "target"))
        logs = self.stream.getvalue()
        self.assertNotIn(private, logs)
        self.assertNotIn(target, logs)
        self.assertNotIn(request.challenge, logs)

    def test_approved_caution_and_dangerous_actions_execute_once(self) -> None:
        for capability in ("type-text", "send-data"):
            with self.subTest(capability=capability):
                subject = subject_for(capability, action_id=f"action-{capability}")
                _, token = self.authorize(subject)
                calls: list[dict[str, str]] = []

                def tool(**arguments: str) -> ToolResult:
                    calls.append(arguments)
                    return ToolResult(True, "ok")

                executor = ActionExecutor(
                    {subject.action.tool_name: tool},
                    self.logger,
                    self.broker,
                )
                result = executor.execute(subject.action, token)

                self.assertTrue(result.success)
                self.assertEqual(len(calls), 1)

    def test_rejection_cancellation_and_expiry_never_issue_token(self) -> None:
        rejected_subject = subject_for("type-text", action_id="action-rejected")
        rejected_preparation = self.prepare(rejected_subject)
        rejected = self.broker.authorize(
            rejected_subject,
            self.decision(rejected_preparation, approved=False),
        )
        self.assertEqual(rejected.status, AuthorizationStatus.REJECTED)
        self.assertIsNone(rejected.token)

        cancelled_subject = subject_for("type-text", action_id="action-cancelled")
        cancelled_preparation = self.prepare(cancelled_subject)
        request = cancelled_preparation.request
        assert request is not None
        self.assertEqual(
            self.broker.cancel(request.request_id),
            AuthorizationStatus.CANCELLED,
        )
        cancelled = self.broker.authorize(
            cancelled_subject,
            self.decision(cancelled_preparation),
        )
        self.assertEqual(cancelled.status, AuthorizationStatus.CANCELLED)

        expired_subject = subject_for("type-text", action_id="action-expired")
        expired_preparation = self.prepare(expired_subject)
        self.clock.advance(5)
        expired = self.broker.authorize(
            expired_subject,
            self.decision(expired_preparation),
        )
        self.assertEqual(expired.status, AuthorizationStatus.EXPIRED)

    def test_token_expires_or_can_be_cancelled_before_execution(self) -> None:
        expiring = subject_for("type-text", action_id="action-token-expired")
        _, expired_token = self.authorize(expiring)
        self.clock.advance(5)
        executor = ActionExecutor(
            {expiring.action.tool_name: lambda **_: ToolResult(True, "ok")},
            self.logger,
            self.broker,
        )
        with self.assertRaises(ActionExecutionError):
            executor.execute(expiring.action, expired_token)

        active = subject_for("type-text", action_id="action-token-cancelled")
        preparation, active_token = self.authorize(active)
        request = preparation.request
        assert request is not None
        self.assertEqual(
            self.broker.cancel(request.request_id),
            AuthorizationStatus.CANCELLED,
        )
        with self.assertRaises(ActionExecutionError):
            executor.execute(active.action, active_token)

    def test_token_is_bound_to_action_arguments_and_destination(self) -> None:
        subject = subject_for("type-text")
        _, token = self.authorize(subject)
        changed = action_for(
            "type-text",
            target="other-destination",
            content="Other content",
        )
        executor = ActionExecutor(
            {subject.action.tool_name: lambda **_: ToolResult(True, "ok")},
            self.logger,
            self.broker,
        )

        with self.assertRaises(ActionExecutionError):
            executor.execute(changed, token)

        result = executor.execute(subject.action, token)
        self.assertTrue(result.success)

    def test_token_cannot_be_reused(self) -> None:
        subject = subject_for("type-text")
        _, token = self.authorize(subject)
        calls: list[bool] = []
        executor = ActionExecutor(
            {
                subject.action.tool_name: lambda **_: (
                    calls.append(True) or ToolResult(True, "ok")
                )
            },
            self.logger,
            self.broker,
        )

        executor.execute(subject.action, token)
        with self.assertRaises(ActionExecutionError):
            executor.execute(subject.action, token)

        self.assertEqual(calls, [True])

    def test_racing_execution_consumes_token_before_single_effect(self) -> None:
        subject = subject_for("type-text", action_id="action-execute-race")
        _, token = self.authorize(subject)
        calls: list[bool] = []
        barrier = threading.Barrier(3)
        results: list[str] = []

        def tool(**_: str) -> ToolResult:
            calls.append(True)
            return ToolResult(True, "ok")

        executor = ActionExecutor(
            {subject.action.tool_name: tool},
            self.logger,
            self.broker,
        )

        def execute() -> None:
            barrier.wait()
            try:
                executor.execute(subject.action, token)
            except ActionExecutionError:
                results.append("rejected")
            else:
                results.append("executed")

        threads = [threading.Thread(target=execute) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        self.assertEqual(sorted(results), ["executed", "rejected"])
        self.assertEqual(calls, [True])

    def test_stale_subject_or_wrong_challenge_consumes_request_as_invalid(self) -> None:
        stale_subject = subject_for("type-text", action_id="action-stale")
        stale_preparation = self.prepare(stale_subject)
        changed_subject = subject_for(
            "type-text",
            action_id="action-stale",
            content="changed-after-prompt",
        )
        stale = self.broker.authorize(
            changed_subject,
            self.decision(stale_preparation),
        )
        repeated = self.broker.authorize(
            stale_subject,
            self.decision(stale_preparation),
        )
        self.assertEqual(stale.status, AuthorizationStatus.INVALID)
        self.assertEqual(repeated.status, AuthorizationStatus.INVALID)

        wrong_subject = subject_for("type-text", action_id="action-wrong")
        wrong_preparation = self.prepare(wrong_subject)
        wrong = self.broker.authorize(
            wrong_subject,
            self.decision(wrong_preparation, challenge="challenge-wrong"),
        )
        self.assertEqual(wrong.status, AuthorizationStatus.INVALID)

    def test_remote_channel_requires_explicit_enablement(self) -> None:
        local_only = subject_for("type-text", action_id="action-local-only")
        local_preparation = self.prepare(local_only)
        remote_denied = self.broker.authorize(
            local_only,
            self.decision(
                local_preparation,
                channel=ConfirmationChannel.REMOTE,
            ),
        )
        self.assertEqual(remote_denied.status, AuthorizationStatus.INVALID)

        remote = subject_for("type-text", action_id="action-remote")
        remote_preparation = self.prepare(
            remote,
            (ConfirmationChannel.LOCAL, ConfirmationChannel.REMOTE),
        )
        remote_allowed = self.broker.authorize(
            remote,
            self.decision(
                remote_preparation,
                channel=ConfirmationChannel.REMOTE,
            ),
        )
        self.assertEqual(remote_allowed.status, AuthorizationStatus.AUTHORIZED)

    def test_racing_confirmations_issue_only_one_token(self) -> None:
        subject = subject_for("type-text", action_id="action-race")
        preparation = self.prepare(subject)
        decision = self.decision(preparation)
        barrier = threading.Barrier(3)
        results = []

        def authorize() -> None:
            barrier.wait()
            results.append(self.broker.authorize(subject, decision))

        threads = [threading.Thread(target=authorize) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        self.assertEqual(
            sorted(result.status.value for result in results),
            ["already_used", "authorized"],
        )
        self.assertEqual(sum(result.token is not None for result in results), 1)

    def test_tool_failure_is_redacted_and_consumes_authorization(self) -> None:
        private = "PRIVATE-TOOL-ERROR-449"
        subject = subject_for("type-text")
        _, token = self.authorize(subject)

        def failing_tool(**_: str) -> ToolResult:
            raise RuntimeError(private)

        executor = ActionExecutor(
            {subject.action.tool_name: failing_tool},
            self.logger,
            self.broker,
        )
        with self.assertRaises(ActionExecutionError) as context:
            executor.execute(subject.action, token)
        with self.assertRaises(ActionExecutionError):
            executor.execute(subject.action, token)

        self.assertNotIn(private, str(context.exception))
        self.assertNotIn(private, self.stream.getvalue())


if __name__ == "__main__":
    unittest.main()
