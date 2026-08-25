import io
import logging
import unittest

from desktop_agent.cli import process_plan_command
from desktop_agent.executor import ActionExecutor
from desktop_agent.interpretation import (
    InterpretationPath,
    ProposalProviderError,
    ProposalProviderResult,
)
from desktop_agent.models import Action, Intent, RiskLevel, ToolResult
from desktop_agent.plans import (
    CancellationToken,
    PlanInterpretationStatus,
    PlanLimits,
    PlanStatus,
    PlanStep,
    PlanStepState,
    PlanValidationError,
    TaskPlan,
    TaskPlanExecutor,
    TaskPlanValidator,
    TaskPlanner,
    parse_plan_command,
    validate_plan_proposal,
)


class FakeProvider:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.commands: list[str] = []

    def propose(self, command: str) -> ProposalProviderResult:
        self.commands.append(command)
        return ProposalProviderResult(self.payload)


class FailingProvider:
    def propose(self, command: str) -> ProposalProviderResult:
        raise ProposalProviderError("simulated")


class MutableClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def safe_action(tool_name: str = "open_url") -> Action:
    if tool_name == "open_url":
        return Action(
            Intent.OPEN_URL,
            "open_url",
            {"url": "https://www.youtube.com/"},
            RiskLevel.SAFE,
            False,
        )
    return Action(
        Intent.OPEN_APPLICATION,
        "open_application",
        {"name": "calculator"},
        RiskLevel.SAFE,
        False,
    )


def task_plan(*actions: Action) -> TaskPlan:
    return TaskPlan(
        "plan-test",
        tuple(
            PlanStep(f"step-{index}", action)
            for index, action in enumerate(actions, start=1)
        ),
    )


class PlanProposalTests(unittest.TestCase):
    def test_builds_all_supported_step_types_with_local_actions(self) -> None:
        plan = validate_plan_proposal(
            {
                "schema_version": 1,
                "steps": [
                    {"intent": "OPEN_URL", "target": "github"},
                    {
                        "intent": "OPEN_APPLICATION",
                        "target": "calculator",
                    },
                    {"intent": "PLAY_YOUTUBE", "target": "  lofi   hip hop "},
                    {"intent": "STOP_YOUTUBE", "target": None},
                ],
            },
            plan_id_factory=lambda: "plan-fixed",
        )

        self.assertEqual(plan.plan_id, "plan-fixed")
        self.assertEqual(
            [step.action.tool_name for step in plan.steps],
            ["open_url", "open_application", "play_youtube", "stop_youtube"],
        )
        self.assertEqual(plan.steps[2].action.arguments["query"], "lofi hip hop")

    def test_rejects_extra_fields_unknown_targets_and_invalid_counts(self) -> None:
        invalid = [
            {"schema_version": 1, "steps": [], "extra": True},
            {
                "schema_version": 1,
                "steps": [
                    {"intent": "OPEN_URL", "target": "unknown"},
                    {"intent": "OPEN_URL", "target": "github"},
                ],
            },
            {
                "schema_version": 1,
                "steps": [
                    {"intent": "OPEN_URL", "target": "github"},
                ],
            },
            {
                "schema_version": 1,
                "steps": [
                    {"intent": "STOP_YOUTUBE", "target": "youtube"},
                    {"intent": "OPEN_URL", "target": "github"},
                ],
            },
        ]

        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(PlanValidationError):
                    validate_plan_proposal(payload)

    def test_parses_explicit_multistep_phrasings_as_one_plan(self) -> None:
        plan = parse_plan_command(
            "abrir youtube y después abrir calculadora; detener youtube",
            plan_id_factory=lambda: "plan-local",
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.plan_id, "plan-local")
        self.assertEqual(len(plan.steps), 3)
        self.assertEqual(
            [step.action.tool_name for step in plan.steps],
            ["open_url", "open_application", "stop_youtube"],
        )

    def test_invalid_or_single_action_is_not_a_deterministic_plan(self) -> None:
        self.assertIsNone(parse_plan_command("abrir youtube"))
        self.assertIsNone(
            parse_plan_command("abrir youtube y después borrar todo")
        )


class TaskPlannerTests(unittest.TestCase):
    def test_deterministic_plan_bypasses_provider(self) -> None:
        provider = FakeProvider({})
        planner = TaskPlanner(
            provider,
            plan_id_factory=lambda: "plan-local",
        )

        result = planner.interpret("abrir youtube luego abrir github")

        self.assertIs(result.status, PlanInterpretationStatus.SUCCESS)
        self.assertIs(result.path, InterpretationPath.DETERMINISTIC)
        self.assertEqual(provider.commands, [])

    def test_validates_provider_plan_before_returning_it(self) -> None:
        provider = FakeProvider(
            {
                "schema_version": 1,
                "steps": [
                    {"intent": "OPEN_URL", "target": "youtube"},
                    {"intent": "OPEN_URL", "target": "github"},
                ],
            }
        )
        planner = TaskPlanner(
            provider,
            provider_name="fake",
            provider_model="fake-model",
            plan_id_factory=lambda: "plan-provider",
        )

        result = planner.interpret("una tarea libre")

        self.assertIs(result.status, PlanInterpretationStatus.SUCCESS)
        self.assertIs(result.path, InterpretationPath.EXTERNAL)
        self.assertEqual(result.plan.plan_id, "plan-provider")
        self.assertEqual(provider.commands, ["una tarea libre"])

    def test_invalid_or_failed_provider_never_returns_a_plan(self) -> None:
        invalid = TaskPlanner(FakeProvider({"schema_version": 1, "steps": []}))
        failed = TaskPlanner(FailingProvider())

        invalid_result = invalid.interpret("libre")
        failed_result = failed.interpret("libre")

        self.assertIs(
            invalid_result.status, PlanInterpretationStatus.INVALID_PROPOSAL
        )
        self.assertIsNone(invalid_result.plan)
        self.assertIs(
            failed_result.status, PlanInterpretationStatus.PROVIDER_ERROR
        )
        self.assertIsNone(failed_result.plan)


class TaskPlanExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.log_output = io.StringIO()
        self.logger = logging.Logger(self.id(), logging.INFO)
        self.logger.addHandler(logging.StreamHandler(self.log_output))

    def test_validates_every_step_before_first_effect(self) -> None:
        effects: list[str] = []
        executor = ActionExecutor(
            {"open_url": lambda url: effects.append(url) or ToolResult(True, "ok")},
            self.logger,
        )
        invalid_second = Action(
            Intent.OPEN_URL,
            "open_url",
            {"url": "https://example.invalid/"},
            RiskLevel.SAFE,
            False,
        )
        plan = task_plan(safe_action(), invalid_second)

        with self.assertRaises(PlanValidationError):
            TaskPlanExecutor(executor, self.logger).execute(plan)

        self.assertEqual(effects, [])

    def test_rejects_duplicate_steps_mismatched_intent_and_unregistered_tool(
        self,
    ) -> None:
        executor = ActionExecutor(
            {"open_url": lambda url: ToolResult(True, "ok")},
            self.logger,
        )
        validator = TaskPlanValidator(executor)
        duplicate = TaskPlan(
            "plan",
            (
                PlanStep("same", safe_action()),
                PlanStep("same", safe_action()),
            ),
        )
        mismatched = task_plan(
            safe_action(),
            Action(
                Intent.OPEN_APPLICATION,
                "open_url",
                {"url": "https://www.github.com/"},
                RiskLevel.SAFE,
                False,
            ),
        )
        unregistered = task_plan(safe_action(), safe_action("open_application"))

        for plan in (duplicate, mismatched, unregistered):
            with self.subTest(plan=plan):
                with self.assertRaises(PlanValidationError):
                    validator.validate(plan)

    def test_too_many_steps_are_rejected_before_any_effect(self) -> None:
        effects: list[str] = []
        executor = ActionExecutor(
            {
                "open_url": lambda url: (
                    effects.append(url) or ToolResult(True, "ok")
                )
            },
            self.logger,
        )
        plan = task_plan(*(safe_action() for _ in range(6)))

        with self.assertRaises(PlanValidationError):
            TaskPlanExecutor(executor, self.logger).execute(plan)

        self.assertEqual(effects, [])
        self.assertIn("status=REJECTED", self.log_output.getvalue())

    def test_executes_valid_plan_in_order_with_structured_results(self) -> None:
        effects: list[str] = []
        executor = ActionExecutor(
            {
                "open_url": lambda url: (
                    effects.append("url") or ToolResult(True, "web abierta")
                ),
                "open_application": lambda name: (
                    effects.append("app") or ToolResult(True, "app abierta")
                ),
            },
            self.logger,
        )

        result = TaskPlanExecutor(executor, self.logger).execute(
            task_plan(safe_action(), safe_action("open_application"))
        )

        self.assertIs(result.status, PlanStatus.SUCCEEDED)
        self.assertEqual(effects, ["url", "app"])
        self.assertEqual(
            [step.state for step in result.step_results],
            [PlanStepState.SUCCEEDED, PlanStepState.SUCCEEDED],
        )

    def test_failure_stops_without_retrying_and_skips_remaining(self) -> None:
        calls = 0

        def fail(url: str) -> ToolResult:
            nonlocal calls
            calls += 1
            return ToolResult(False, "fallo esperado")

        executor = ActionExecutor({"open_url": fail}, self.logger)
        result = TaskPlanExecutor(executor, self.logger).execute(
            task_plan(safe_action(), safe_action(), safe_action())
        )

        self.assertIs(result.status, PlanStatus.FAILED)
        self.assertEqual(calls, 1)
        self.assertEqual(
            [step.state for step in result.step_results],
            [PlanStepState.FAILED, PlanStepState.SKIPPED, PlanStepState.SKIPPED],
        )

    def test_cancellation_is_honored_between_steps(self) -> None:
        token = CancellationToken()
        calls = 0

        def first(url: str) -> ToolResult:
            nonlocal calls
            calls += 1
            token.cancel()
            return ToolResult(True, "primero")

        executor = ActionExecutor({"open_url": first}, self.logger)
        result = TaskPlanExecutor(executor, self.logger).execute(
            task_plan(safe_action(), safe_action()), token
        )

        self.assertIs(result.status, PlanStatus.CANCELLED)
        self.assertEqual(calls, 1)
        self.assertEqual(
            [step.state for step in result.step_results],
            [PlanStepState.SUCCEEDED, PlanStepState.CANCELLED],
        )

    def test_timeout_prevents_the_next_step(self) -> None:
        clock = MutableClock()
        calls = 0

        def slow(url: str) -> ToolResult:
            nonlocal calls
            calls += 1
            clock.advance(2.0)
            return ToolResult(True, "lento")

        executor = ActionExecutor({"open_url": slow}, self.logger)
        result = TaskPlanExecutor(
            executor,
            self.logger,
            limits=PlanLimits(max_duration_seconds=1.0),
            clock=clock,
        ).execute(task_plan(safe_action(), safe_action()))

        self.assertIs(result.status, PlanStatus.TIMED_OUT)
        self.assertEqual(calls, 1)
        self.assertIs(result.step_results[1].state, PlanStepState.SKIPPED)

    def test_logs_tools_and_states_without_arguments(self) -> None:
        query = "consulta privada de prueba"
        play = Action(
            Intent.BROWSER_NAVIGATION,
            "play_youtube",
            {"query": query},
            RiskLevel.SAFE,
            False,
        )
        stop = Action(
            Intent.BROWSER_NAVIGATION,
            "stop_youtube",
            {},
            RiskLevel.SAFE,
            False,
        )
        executor = ActionExecutor(
            {
                "play_youtube": lambda query: ToolResult(True, "reproduciendo"),
                "stop_youtube": lambda: ToolResult(True, "detenido"),
            },
            self.logger,
        )

        TaskPlanExecutor(executor, self.logger).execute(task_plan(play, stop))

        log = self.log_output.getvalue()
        self.assertIn("tool=play_youtube", log)
        self.assertIn("status=SUCCEEDED", log)
        self.assertNotIn(query, log)

    def test_cli_demo_executes_two_web_steps_locally(self) -> None:
        opened: list[str] = []
        executor = ActionExecutor(
            {
                "open_url": lambda url: (
                    opened.append(url) or ToolResult(True, "sitio abierto")
                )
            },
            self.logger,
        )
        output: list[str] = []

        success = process_plan_command(
            "abrir youtube luego abrir github",
            TaskPlanExecutor(executor, self.logger),
            TaskPlanner(plan_id_factory=lambda: "demo"),
            self.logger,
            output.append,
        )

        self.assertTrue(success)
        self.assertEqual(
            opened,
            ["https://www.youtube.com/", "https://github.com/"],
        )
        self.assertEqual(output[-1], "Plan finalizado: succeeded.")


if __name__ == "__main__":
    unittest.main()
