import io
import logging
import unittest

from desktop_agent.agent_loop import (
    ActionOutcomeStatus,
    BoundedAgentLoop,
    DecisionKind,
    EvaluationKind,
    LoopActionOutcome,
    LoopCancellation,
    LoopCause,
    LoopContractError,
    LoopDecision,
    LoopEvaluation,
    LoopLimits,
    LoopObservation,
    LoopState,
    LoopTask,
)


class MutableClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class ScriptedObserver:
    def __init__(self, observations: list[object]) -> None:
        self.observations = observations
        self.calls = 0
        self.on_call: object = None

    def observe(self, task: LoopTask) -> LoopObservation:
        self.calls += 1
        if callable(self.on_call):
            self.on_call()
        value = self.observations.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class ScriptedDecisionMaker:
    def __init__(self, decisions: list[object]) -> None:
        self.decisions = decisions
        self.calls = 0

    def decide(
        self,
        task: LoopTask,
        observation: LoopObservation,
    ) -> LoopDecision:
        self.calls += 1
        value = self.decisions.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class ScriptedActor:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def act(
        self,
        task: LoopTask,
        decision: LoopDecision,
    ) -> LoopActionOutcome:
        self.calls += 1
        value = self.outcomes.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class ScriptedEvaluator:
    def __init__(self, evaluations: list[object]) -> None:
        self.evaluations = evaluations
        self.calls = 0

    def evaluate(
        self,
        task: LoopTask,
        observation: LoopObservation,
        decision: LoopDecision,
        outcome: LoopActionOutcome,
    ) -> LoopEvaluation:
        self.calls += 1
        value = self.evaluations.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def task() -> LoopTask:
    return LoopTask(
        "task-qa",
        "qa-create-client",
        ("qa_fill_form", "qa_submit", "qa_alternative"),
    )


def observation(index: int, state: str) -> LoopObservation:
    return LoopObservation(f"observation-{index}", f"state-{state}")


def decision(
    index: int,
    state: str,
    tool: str = "qa_fill_form",
    fingerprint: str | None = None,
) -> LoopDecision:
    return LoopDecision(
        f"decision-{index}",
        DecisionKind.ACTION,
        f"state-{state}",
        tool,
        fingerprint or f"action-{index}",
    )


class QaApplication:
    def __init__(self) -> None:
        self.state = "empty"
        self.observation_count = 0


class QaObserver:
    def __init__(self, app: QaApplication) -> None:
        self.app = app

    def observe(self, task: LoopTask) -> LoopObservation:
        self.app.observation_count += 1
        return observation(self.app.observation_count, self.app.state)


class QaDecisionMaker:
    def decide(
        self,
        task: LoopTask,
        observed: LoopObservation,
    ) -> LoopDecision:
        if observed.state_token == "state-empty":
            return decision(1, "empty")
        if observed.state_token == "state-filled":
            return decision(2, "filled", "qa_submit")
        return LoopDecision(
            "decision-abandon",
            DecisionKind.ABANDON,
            observed.state_token,
        )


class QaActor:
    def __init__(self, app: QaApplication) -> None:
        self.app = app

    def act(
        self,
        task: LoopTask,
        selected: LoopDecision,
    ) -> LoopActionOutcome:
        if selected.tool_name == "qa_fill_form" and self.app.state == "empty":
            self.app.state = "filled"
            return LoopActionOutcome(ActionOutcomeStatus.DELIVERED)
        if selected.tool_name == "qa_submit" and self.app.state == "filled":
            self.app.state = "submitted"
            return LoopActionOutcome(ActionOutcomeStatus.DELIVERED)
        return LoopActionOutcome(
            ActionOutcomeStatus.PERMANENT_FAILURE,
            LoopCause.VALIDATION_FAILURE,
        )


class QaEvaluator:
    def __init__(self, app: QaApplication) -> None:
        self.app = app

    def evaluate(
        self,
        task: LoopTask,
        observed: LoopObservation,
        selected: LoopDecision,
        outcome: LoopActionOutcome,
    ) -> LoopEvaluation:
        if self.app.state == "filled":
            return LoopEvaluation(
                EvaluationKind.PROGRESS,
                True,
                "state-filled",
            )
        if self.app.state == "submitted":
            return LoopEvaluation(
                EvaluationKind.SUCCESS,
                True,
                "state-submitted",
            )
        return LoopEvaluation(
            EvaluationKind.ABANDON,
            False,
            observed.state_token,
            LoopCause.VALIDATION_FAILURE,
        )


class AgentLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.log_output = io.StringIO()
        self.logger = logging.Logger(self.id(), logging.INFO)
        self.logger.addHandler(logging.StreamHandler(self.log_output))
        self.delivered = LoopActionOutcome(ActionOutcomeStatus.DELIVERED)

    def runner(
        self,
        observer: object,
        decision_maker: object,
        actor: object,
        evaluator: object,
        **kwargs: object,
    ) -> BoundedAgentLoop:
        return BoundedAgentLoop(
            observer,
            decision_maker,
            actor,
            evaluator,
            self.logger,
            **kwargs,
        )

    def test_controlled_qa_flow_succeeds_with_two_structured_iterations(self) -> None:
        app = QaApplication()

        result = self.runner(
            QaObserver(app),
            QaDecisionMaker(),
            QaActor(app),
            QaEvaluator(app),
        ).run(task())

        self.assertIs(result.status, LoopState.SUCCEEDED)
        self.assertEqual(result.termination_reason, "success_condition")
        self.assertEqual((result.observation_count, result.action_count), (2, 2))
        self.assertEqual(len(result.iterations), 2)
        self.assertEqual(
            [item.tool_name for item in result.iterations],
            ["qa_fill_form", "qa_submit"],
        )
        self.assertEqual(app.state, "submitted")

    def test_rejects_unallowed_tool_before_actor(self) -> None:
        actor = ScriptedActor([self.delivered])
        result = self.runner(
            ScriptedObserver([observation(1, "empty")]),
            ScriptedDecisionMaker(
                [decision(1, "empty", tool="invented_tool")]
            ),
            actor,
            ScriptedEvaluator([]),
        ).run(task())

        self.assertIs(result.status, LoopState.FAILED)
        self.assertEqual(result.termination_reason, "tool_not_allowed")
        self.assertEqual(actor.calls, 0)

    def test_blocks_repeated_action_without_new_state(self) -> None:
        same_decision = decision(
            1,
            "empty",
            fingerprint="same-action",
        )
        result = self.runner(
            ScriptedObserver(
                [observation(1, "empty"), observation(2, "empty")]
            ),
            ScriptedDecisionMaker([same_decision, same_decision]),
            ScriptedActor([self.delivered]),
            ScriptedEvaluator(
                [
                    LoopEvaluation(
                        EvaluationKind.RETRY,
                        False,
                        "state-empty",
                        LoopCause.TRANSIENT_UI,
                    )
                ]
            ),
        ).run(task())

        self.assertIs(result.status, LoopState.AMBIGUOUS)
        self.assertEqual(
            result.termination_reason,
            "repeated_action_without_new_state",
        )
        self.assertEqual(result.action_count, 1)

    def test_only_transient_causes_can_request_retry(self) -> None:
        with self.assertRaises(LoopContractError):
            LoopEvaluation(
                EvaluationKind.RETRY,
                False,
                "state-empty",
                LoopCause.POLICY_BLOCK,
            )
        with self.assertRaises(LoopContractError):
            LoopActionOutcome(
                ActionOutcomeStatus.TRANSIENT_FAILURE,
                LoopCause.ACTION_FAILURE,
            )

    def test_retry_budget_is_bounded_per_cause(self) -> None:
        retry = LoopEvaluation(
            EvaluationKind.RETRY,
            False,
            "state-empty",
            LoopCause.TRANSIENT_TIMEOUT,
        )
        result = self.runner(
            ScriptedObserver(
                [observation(1, "empty"), observation(2, "empty")]
            ),
            ScriptedDecisionMaker(
                [
                    decision(1, "empty", fingerprint="alternative-a"),
                    decision(2, "empty", fingerprint="alternative-b"),
                ]
            ),
            ScriptedActor([self.delivered, self.delivered]),
            ScriptedEvaluator([retry, retry]),
        ).run(task())

        self.assertIs(result.status, LoopState.FAILED)
        self.assertEqual(result.termination_reason, "retry_limit")
        self.assertEqual(result.action_count, 2)

    def test_persistent_ambiguity_terminates_without_actions(self) -> None:
        ambiguous = [
            LoopDecision(
                f"ambiguous-{index}",
                DecisionKind.AMBIGUOUS,
                "state-empty",
            )
            for index in (1, 2)
        ]
        result = self.runner(
            ScriptedObserver(
                [observation(1, "empty"), observation(2, "empty")]
            ),
            ScriptedDecisionMaker(ambiguous),
            ScriptedActor([]),
            ScriptedEvaluator([]),
        ).run(task())

        self.assertIs(result.status, LoopState.AMBIGUOUS)
        self.assertEqual(result.termination_reason, "persistent_ambiguity")
        self.assertEqual(result.action_count, 0)

    def test_observation_limit_bounds_ambiguity_without_actions(self) -> None:
        result = self.runner(
            ScriptedObserver(
                [observation(1, "empty"), observation(2, "empty")]
            ),
            ScriptedDecisionMaker(
                [
                    LoopDecision(
                        f"ambiguous-{index}",
                        DecisionKind.AMBIGUOUS,
                        "state-empty",
                    )
                    for index in (1, 2)
                ]
            ),
            ScriptedActor([]),
            ScriptedEvaluator([]),
            limits=LoopLimits(max_observations=2, max_actions=1, max_ambiguities=3),
        ).run(task())

        self.assertIs(result.status, LoopState.FAILED)
        self.assertEqual(result.termination_reason, "observation_limit")

    def test_action_limit_is_checked_before_next_effect(self) -> None:
        actor = ScriptedActor([self.delivered, self.delivered])
        result = self.runner(
            ScriptedObserver(
                [observation(1, "empty"), observation(2, "filled")]
            ),
            ScriptedDecisionMaker(
                [decision(1, "empty"), decision(2, "filled", "qa_submit")]
            ),
            actor,
            ScriptedEvaluator(
                [
                    LoopEvaluation(
                        EvaluationKind.PROGRESS,
                        True,
                        "state-filled",
                    )
                ]
            ),
            limits=LoopLimits(max_observations=2, max_actions=1),
        ).run(task())

        self.assertEqual(result.termination_reason, "action_limit")
        self.assertEqual(actor.calls, 1)

    def test_time_limit_is_checked_after_each_external_phase(self) -> None:
        clock = MutableClock()
        observer = ScriptedObserver([observation(1, "empty")])
        observer.on_call = lambda: clock.advance(2)
        result = self.runner(
            observer,
            ScriptedDecisionMaker([]),
            ScriptedActor([]),
            ScriptedEvaluator([]),
            limits=LoopLimits(max_duration_seconds=1),
            clock=clock,
        ).run(task())

        self.assertIs(result.status, LoopState.TIMED_OUT)
        self.assertEqual(result.termination_reason, "time_limit")

    def test_cancellation_is_checked_between_phases(self) -> None:
        cancellation = LoopCancellation()
        observer = ScriptedObserver([observation(1, "empty")])
        observer.on_call = cancellation.cancel
        result = self.runner(
            observer,
            ScriptedDecisionMaker([]),
            ScriptedActor([]),
            ScriptedEvaluator([]),
        ).run(task(), cancellation)

        self.assertIs(result.status, LoopState.CANCELLED)
        self.assertEqual(result.action_count, 0)

    def test_incoherent_or_mismatched_state_evidence_fails_closed(self) -> None:
        incoherent = self.runner(
            ScriptedObserver([observation(1, "empty")]),
            ScriptedDecisionMaker([decision(1, "empty")]),
            ScriptedActor([self.delivered]),
            ScriptedEvaluator(
                [
                    LoopEvaluation(
                        EvaluationKind.SUCCESS,
                        False,
                        "state-different",
                    )
                ]
            ),
        ).run(task())
        self.assertEqual(
            incoherent.termination_reason, "incoherent_state_evidence"
        )

        mismatch = self.runner(
            ScriptedObserver(
                [observation(1, "empty"), observation(2, "unexpected")]
            ),
            ScriptedDecisionMaker([decision(1, "empty")]),
            ScriptedActor([self.delivered]),
            ScriptedEvaluator(
                [
                    LoopEvaluation(
                        EvaluationKind.PROGRESS,
                        True,
                        "state-filled",
                    )
                ]
            ),
        ).run(task())
        self.assertEqual(
            mismatch.termination_reason, "state_evidence_mismatch"
        )

    def test_stale_decision_is_rejected_before_action(self) -> None:
        actor = ScriptedActor([self.delivered])
        result = self.runner(
            ScriptedObserver([observation(1, "empty")]),
            ScriptedDecisionMaker([decision(1, "different")]),
            actor,
            ScriptedEvaluator([]),
        ).run(task())

        self.assertEqual(result.termination_reason, "invalid_decision")
        self.assertEqual(actor.calls, 0)

    def test_phase_exceptions_are_redacted_and_fail_closed(self) -> None:
        secret = "private phase detail"
        result = self.runner(
            ScriptedObserver([RuntimeError(secret)]),
            ScriptedDecisionMaker([]),
            ScriptedActor([]),
            ScriptedEvaluator([]),
        ).run(task())

        self.assertEqual(result.termination_reason, "observation_failure")
        self.assertNotIn(secret, self.log_output.getvalue())

    def test_evidence_and_logs_do_not_contain_fictitious_private_values(self) -> None:
        private_value = "Cliente Ficticio Documento 12345678"
        app = QaApplication()
        result = self.runner(
            QaObserver(app),
            QaDecisionMaker(),
            QaActor(app),
            QaEvaluator(app),
        ).run(task())

        self.assertNotIn(private_value, repr(result))
        self.assertNotIn(private_value, self.log_output.getvalue())
        self.assertIn("state=SUCCEEDED", self.log_output.getvalue())

    def test_contracts_reject_unsafe_ids_and_incoherent_values(self) -> None:
        with self.assertRaises(LoopContractError):
            LoopTask("unsafe\nid", "goal", ("tool",))
        with self.assertRaises(LoopContractError):
            LoopLimits(max_actions=4, max_observations=3)
        with self.assertRaises(LoopContractError):
            LoopDecision(
                "decision",
                DecisionKind.AMBIGUOUS,
                "state",
                "tool",
                "action",
            )
        with self.assertRaises(LoopContractError):
            LoopEvaluation(
                EvaluationKind.PROGRESS,
                False,
                "state",
            )


if __name__ == "__main__":
    unittest.main()
