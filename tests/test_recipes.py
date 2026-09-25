import io
import json
import logging
import tempfile
import unittest
from pathlib import Path

from desktop_agent.recipes import (
    ApplicationFingerprint,
    InvalidationCause,
    Recipe,
    RecipeActionResult,
    RecipeApproval,
    RecipeCatalog,
    RecipeError,
    RecipeRunStatus,
    RecipeRunner,
    RecipeStatus,
    RecipeStep,
    RecipeStore,
    RecipeValidation,
    RecipeVerificationResult,
    RecoveryCause,
    RecoveryRule,
    SemanticAction,
    StepVerification,
    VerificationPredicate,
)


NOW = "2026-08-25T12:00:00-03:00"


def fingerprint(version: str = "1.0", marker: str = "a") -> ApplicationFingerprint:
    return ApplicationFingerprint("qa.clients", version, marker * 64)


def step(
    step_id: str,
    tool: str = "qa_form",
    operation: str = "set_field",
    parameters: tuple[str, ...] = ("client_name",),
) -> RecipeStep:
    return RecipeStep(
        SemanticAction(
            step_id,
            tool,
            operation,
            f"target.{step_id}",
            parameters,
        ),
        StepVerification(
            VerificationPredicate.STATE_EQUALS,
            f"state.{step_id}",
        ),
    )


def proposed_recipe(*, recovery: bool = False) -> Recipe:
    steps = (
        step("fill"),
        step("submit", "qa_submit", "activate", ()),
    )
    rules: tuple[RecoveryRule, ...] = ()
    if recovery:
        rules = (
            RecoveryRule(
                "submit",
                RecoveryCause.TRANSIENT_UI,
                step("submit_keyboard", "qa_keyboard", "activate", ()),
            ),
        )
    return Recipe(
        "qa-create-client",
        1,
        fingerprint(),
        "create-client",
        ("window.ready", "user.confirmed"),
        steps,
        rules,
    )


def validation(recipe: Recipe, *, success: bool = True) -> RecipeValidation:
    return RecipeValidation(
        recipe.recipe_id,
        recipe.revision,
        recipe.application,
        tuple(item.action.step_id for item in recipe.steps),
        success,
        "evidence-qa-001",
        NOW,
    )


class FakeExecutor:
    def __init__(self, results: list[object]) -> None:
        self.results = results
        self.calls: list[tuple[SemanticAction, dict[str, str]]] = []

    def execute(
        self,
        action: SemanticAction,
        parameters: dict[str, str],
    ) -> RecipeActionResult:
        self.calls.append((action, dict(parameters)))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeVerifier:
    def __init__(self, results: list[object]) -> None:
        self.results = results
        self.calls: list[StepVerification] = []

    def verify(
        self,
        verification: StepVerification,
    ) -> RecipeVerificationResult:
        self.calls.append(verification)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class RecipeTestSupport:
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.path = Path(self.temporary_directory.name) / "recipes.json"
        self.store = RecipeStore(self.path)
        self.catalog = RecipeCatalog(self.store)

    def approve(self, recipe: Recipe | None = None) -> Recipe:
        candidate = recipe or proposed_recipe()
        self.catalog.propose(candidate)
        return self.catalog.approve(
            RecipeApproval(candidate.recipe_id, candidate.revision, True),
            validation(candidate),
        )

    def runner(
        self,
        action_results: list[object],
        verification_results: list[object],
    ) -> tuple[RecipeRunner, FakeExecutor, FakeVerifier, io.StringIO]:
        executor = FakeExecutor(action_results)
        verifier = FakeVerifier(verification_results)
        stream = io.StringIO()
        logger = logging.Logger("recipe-test")
        logger.addHandler(logging.StreamHandler(stream))
        return (
            RecipeRunner(executor, verifier, self.catalog, logger),
            executor,
            verifier,
            stream,
        )


class RecipeContractTests(unittest.TestCase):
    def test_recipe_contains_semantic_actions_and_step_verification(self) -> None:
        recipe = proposed_recipe(recovery=True)

        self.assertEqual(recipe.status, RecipeStatus.PROPOSED)
        self.assertEqual(recipe.steps[0].action.target_key, "target.fill")
        self.assertEqual(
            recipe.steps[0].verification.predicate,
            VerificationPredicate.STATE_EQUALS,
        )
        self.assertEqual(recipe.recovery_rules[0].primary_step_id, "submit")

    def test_rejects_coordinate_operations_and_sensitive_parameter_names(self) -> None:
        with self.assertRaises(RecipeError):
            step("click", operation="mouse_xy")
        with self.assertRaises(RecipeError):
            step("type", parameters=("password",))

    def test_proposal_cannot_claim_validation(self) -> None:
        candidate = proposed_recipe()

        with self.assertRaises(RecipeError):
            Recipe(
                candidate.recipe_id,
                candidate.revision,
                candidate.application,
                candidate.goal_key,
                candidate.preconditions,
                candidate.steps,
                status=RecipeStatus.PROPOSED,
                validated_at=NOW,
                validation_evidence_id="fake-evidence",
            )

    def test_recovery_must_reference_primary_and_unique_alternative(self) -> None:
        with self.assertRaises(RecipeError):
            Recipe(
                "invalid-recovery",
                1,
                fingerprint(),
                "create-client",
                (),
                (step("fill"),),
                (
                    RecoveryRule(
                        "missing",
                        RecoveryCause.TIMEOUT,
                        step("alternative"),
                    ),
                ),
            )


class RecipeCatalogTests(RecipeTestSupport, unittest.TestCase):
    def test_approval_requires_explicit_decision_and_observable_success(self) -> None:
        candidate = proposed_recipe()
        self.catalog.propose(candidate)

        with self.assertRaises(RecipeError):
            self.catalog.approve(
                RecipeApproval(candidate.recipe_id, 1, False),
                validation(candidate),
            )
        with self.assertRaises(RecipeError):
            self.catalog.approve(
                RecipeApproval(candidate.recipe_id, 1, True),
                validation(candidate, success=False),
            )

        self.assertEqual(self.store.load()[0].status, RecipeStatus.PROPOSED)

    def test_approval_requires_exact_steps_revision_and_application(self) -> None:
        candidate = proposed_recipe()
        self.catalog.propose(candidate)
        invalid = RecipeValidation(
            candidate.recipe_id,
            1,
            candidate.application,
            ("fill",),
            True,
            "partial-evidence",
            NOW,
        )

        with self.assertRaises(RecipeError):
            self.catalog.approve(
                RecipeApproval(candidate.recipe_id, 1, True),
                invalid,
            )

    def test_approved_recipe_round_trips_with_validation_evidence(self) -> None:
        approved = self.approve()
        loaded = self.store.load()

        self.assertEqual(loaded, (approved,))
        self.assertEqual(loaded[0].status, RecipeStatus.APPROVED)
        self.assertEqual(loaded[0].validation_evidence_id, "evidence-qa-001")

    def test_corrupt_or_unknown_store_fails_closed(self) -> None:
        self.path.write_text("not-json", encoding="utf-8")
        with self.assertRaises(RecipeError):
            self.store.load()

        self.path.write_text(
            json.dumps({"schema_version": 1, "recipes": [], "extra": True}),
            encoding="utf-8",
        )
        with self.assertRaises(RecipeError):
            self.store.load()

    def test_application_change_marks_approved_candidate_stale(self) -> None:
        self.approve()

        selected = self.catalog.reusable(
            fingerprint(version="2.0", marker="b"),
            "create-client",
        )

        self.assertIsNone(selected)
        self.assertEqual(self.store.load()[0].status, RecipeStatus.STALE)

    def test_only_exact_approved_recipe_is_reusable(self) -> None:
        approved = self.approve()

        self.assertEqual(
            self.catalog.reusable(fingerprint(), "create-client"),
            approved,
        )
        self.assertIsNone(self.catalog.reusable(fingerprint(), "other-goal"))

    def test_disable_prevents_reuse(self) -> None:
        approved = self.approve()
        disabled = self.catalog.disable(approved.recipe_id, approved.revision)

        self.assertEqual(disabled.status, RecipeStatus.DISABLED)
        self.assertIsNone(
            self.catalog.reusable(fingerprint(), "create-client")
        )


class RecipeRunnerTests(RecipeTestSupport, unittest.TestCase):
    def test_executes_approved_recipe_and_verifies_every_step(self) -> None:
        approved = self.approve()
        runner, executor, verifier, stream = self.runner(
            [RecipeActionResult(True), RecipeActionResult(True)],
            [
                RecipeVerificationResult(True),
                RecipeVerificationResult(True),
            ],
        )

        result = runner.run(
            approved,
            fingerprint(),
            {"client_name": "Cliente Ficticio QA"},
            ("window.ready", "user.confirmed"),
            ("qa_form", "qa_submit"),
        )

        self.assertEqual(result.status, RecipeRunStatus.SUCCEEDED)
        self.assertEqual(len(result.steps), 2)
        self.assertEqual(len(executor.calls), 2)
        self.assertEqual(len(verifier.calls), 2)
        self.assertEqual(executor.calls[0][1], {"client_name": "Cliente Ficticio QA"})
        self.assertEqual(executor.calls[1][1], {})
        self.assertNotIn("Cliente Ficticio QA", stream.getvalue())

    def test_runtime_values_are_never_persisted(self) -> None:
        approved = self.approve()
        runner, _, _, _ = self.runner(
            [RecipeActionResult(True), RecipeActionResult(True)],
            [
                RecipeVerificationResult(True),
                RecipeVerificationResult(True),
            ],
        )

        runner.run(
            approved,
            fingerprint(),
            {"client_name": "PRIVATE-RUNTIME-VALUE"},
            ("window.ready", "user.confirmed"),
            ("qa_form", "qa_submit"),
        )

        self.assertNotIn(
            "PRIVATE-RUNTIME-VALUE",
            self.path.read_text(encoding="utf-8"),
        )

    def test_rejects_unapproved_recipe_invalid_parameters_and_tool(self) -> None:
        candidate = proposed_recipe()
        runner, executor, _, _ = self.runner([], [])

        unapproved = runner.run(
            candidate,
            fingerprint(),
            {"client_name": "Ficticio"},
            ("window.ready", "user.confirmed"),
            ("qa_form", "qa_submit"),
        )
        self.assertEqual(unapproved.status, RecipeRunStatus.REJECTED)
        self.assertEqual(executor.calls, [])

        approved = self.approve(candidate)
        invalid_parameters = runner.run(
            approved,
            fingerprint(),
            {},
            ("window.ready", "user.confirmed"),
            ("qa_form", "qa_submit"),
        )
        blocked_tool = runner.run(
            approved,
            fingerprint(),
            {"client_name": "Ficticio"},
            ("window.ready", "user.confirmed"),
            ("qa_form",),
        )
        self.assertEqual(invalid_parameters.status, RecipeRunStatus.REJECTED)
        self.assertEqual(blocked_tool.reason, "tool_not_allowed")
        self.assertEqual(executor.calls, [])

    def test_requires_exact_preconditions_before_any_effect(self) -> None:
        approved = self.approve()
        runner, executor, _, _ = self.runner([], [])

        result = runner.run(
            approved,
            fingerprint(),
            {"client_name": "Ficticio"},
            ("window.ready",),
            ("qa_form", "qa_submit"),
        )

        self.assertEqual(result.status, RecipeRunStatus.REJECTED)
        self.assertEqual(result.reason, "preconditions_not_satisfied")
        self.assertEqual(executor.calls, [])

    def test_invalid_allowlist_is_a_structured_rejection(self) -> None:
        approved = self.approve()
        runner, executor, _, _ = self.runner([], [])

        result = runner.run(
            approved,
            fingerprint(),
            {"client_name": "Ficticio"},
            ("window.ready", "user.confirmed"),
            (),
        )

        self.assertEqual(result.status, RecipeRunStatus.REJECTED)
        self.assertEqual(result.reason, "invalid_allowlist")
        self.assertEqual(executor.calls, [])

    def test_rejects_approved_object_not_registered_in_catalog(self) -> None:
        registered = self.approve()
        forged = Recipe(
            registered.recipe_id,
            2,
            registered.application,
            registered.goal_key,
            registered.preconditions,
            registered.steps,
            status=RecipeStatus.APPROVED,
            validated_at=NOW,
            validation_evidence_id="unregistered-evidence",
        )
        runner, executor, _, _ = self.runner([], [])

        result = runner.run(
            forged,
            fingerprint(),
            {"client_name": "Ficticio"},
            ("window.ready", "user.confirmed"),
            ("qa_form", "qa_submit"),
        )

        self.assertEqual(result.status, RecipeRunStatus.REJECTED)
        self.assertEqual(result.reason, "not_registered")
        self.assertEqual(executor.calls, [])

    def test_uses_only_declared_recovery_once_then_continues(self) -> None:
        approved = self.approve(proposed_recipe(recovery=True))
        runner, executor, _, _ = self.runner(
            [
                RecipeActionResult(True),
                RecipeActionResult(False, RecoveryCause.TRANSIENT_UI),
                RecipeActionResult(True),
            ],
            [RecipeVerificationResult(True), RecipeVerificationResult(True)],
        )

        result = runner.run(
            approved,
            fingerprint(),
            {"client_name": "Ficticio"},
            ("window.ready", "user.confirmed"),
            ("qa_form", "qa_submit", "qa_keyboard"),
        )

        self.assertEqual(result.status, RecipeRunStatus.SUCCEEDED)
        self.assertEqual(
            [call[0].step_id for call in executor.calls],
            ["fill", "submit", "submit_keyboard"],
        )
        self.assertTrue(result.steps[-1].recovered)

    def test_failure_without_approved_recovery_stops(self) -> None:
        approved = self.approve()
        runner, executor, _, _ = self.runner(
            [RecipeActionResult(False, RecoveryCause.TIMEOUT)],
            [],
        )

        result = runner.run(
            approved,
            fingerprint(),
            {"client_name": "Ficticio"},
            ("window.ready", "user.confirmed"),
            ("qa_form", "qa_submit"),
        )

        self.assertEqual(result.status, RecipeRunStatus.FAILED)
        self.assertEqual(result.reason, "no_approved_recovery")
        self.assertEqual(len(executor.calls), 1)

    def test_selector_or_contract_mismatch_invalidates_recipe(self) -> None:
        for cause in (
            InvalidationCause.SELECTOR_CHANGED,
            InvalidationCause.CONTRACT_CHANGED,
        ):
            with self.subTest(cause=cause):
                self.setUp()
                approved = self.approve()
                runner, _, _, _ = self.runner(
                    [RecipeActionResult(False, cause)],
                    [],
                )

                result = runner.run(
                    approved,
                    fingerprint(),
                    {"client_name": "Ficticio"},
                    ("window.ready", "user.confirmed"),
                    ("qa_form", "qa_submit"),
                )

                self.assertEqual(result.status, RecipeRunStatus.STALE)
                self.assertEqual(self.store.load()[0].status, RecipeStatus.STALE)

    def test_unexpected_executor_error_is_redacted_and_invalidates(self) -> None:
        approved = self.approve()
        secret = "private-executor-detail"
        runner, _, _, stream = self.runner([RuntimeError(secret)], [])

        result = runner.run(
            approved,
            fingerprint(),
            {"client_name": "Ficticio"},
            ("window.ready", "user.confirmed"),
            ("qa_form", "qa_submit"),
        )

        self.assertEqual(result.status, RecipeRunStatus.STALE)
        self.assertNotIn(secret, stream.getvalue())


if __name__ == "__main__":
    unittest.main()
