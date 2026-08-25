"""Demostración de v0.9 sobre un alta de cliente totalmente ficticia en memoria."""

import logging

from desktop_agent.agent_loop import (
    ActionOutcomeStatus,
    BoundedAgentLoop,
    DecisionKind,
    EvaluationKind,
    LoopActionOutcome,
    LoopCause,
    LoopDecision,
    LoopEvaluation,
    LoopObservation,
    LoopState,
    LoopTask,
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
        return LoopObservation(
            f"observation-{self.app.observation_count}",
            f"state-{self.app.state}",
        )


class QaDecisionMaker:
    def decide(
        self,
        task: LoopTask,
        observed: LoopObservation,
    ) -> LoopDecision:
        if observed.state_token == "state-empty":
            return LoopDecision(
                "decision-fill",
                DecisionKind.ACTION,
                observed.state_token,
                "qa_fill_form",
                "fill-fictitious-form",
            )
        if observed.state_token == "state-filled":
            return LoopDecision(
                "decision-submit",
                DecisionKind.ACTION,
                observed.state_token,
                "qa_submit",
                "submit-fictitious-form",
            )
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


def main() -> int:
    app = QaApplication()
    task = LoopTask(
        "task-qa-demo",
        "qa-create-client",
        ("qa_fill_form", "qa_submit"),
    )
    result = BoundedAgentLoop(
        QaObserver(app),
        QaDecisionMaker(),
        QaActor(app),
        QaEvaluator(app),
        logging.getLogger("loop08-qa"),
    ).run(task)
    if (
        result.status is not LoopState.SUCCEEDED
        or result.observation_count != 2
        or result.action_count != 2
        or app.state != "submitted"
    ):
        print("LOOP_QA_FAILED")
        return 1
    print(
        "LOOP_QA_OK: 2 observaciones, 2 acciones permitidas, "
        "exito observable; datos ficticios y sin efectos externos."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
