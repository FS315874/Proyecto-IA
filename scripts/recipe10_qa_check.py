import logging
import tempfile
from pathlib import Path

from desktop_agent.recipes import (
    ApplicationFingerprint,
    Recipe,
    RecipeActionResult,
    RecipeApproval,
    RecipeCatalog,
    RecipeRunStatus,
    RecipeRunner,
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


class QaExecutor:
    def __init__(self) -> None:
        self.actions: list[str] = []

    def execute(
        self,
        action: SemanticAction,
        parameters: dict[str, str],
    ) -> RecipeActionResult:
        self.actions.append(action.step_id)
        if action.step_id == "submit":
            return RecipeActionResult(False, RecoveryCause.TRANSIENT_UI)
        return RecipeActionResult(True)


class QaVerifier:
    def verify(
        self,
        verification: StepVerification,
    ) -> RecipeVerificationResult:
        return RecipeVerificationResult(True)


def qa_step(
    step_id: str,
    tool_name: str,
    operation: str,
    parameters: tuple[str, ...] = (),
) -> RecipeStep:
    return RecipeStep(
        SemanticAction(
            step_id,
            tool_name,
            operation,
            f"target.{step_id}",
            parameters,
        ),
        StepVerification(
            VerificationPredicate.STATE_EQUALS,
            f"state.{step_id}",
        ),
    )


def main() -> int:
    application = ApplicationFingerprint("qa.clients", "1.0", "a" * 64)
    recipe = Recipe(
        "qa-create-client",
        1,
        application,
        "create-client",
        ("window.ready", "user.confirmed"),
        (
            qa_step("fill", "qa_form", "set_field", ("client_name",)),
            qa_step("submit", "qa_submit", "activate"),
        ),
        (
            RecoveryRule(
                "submit",
                RecoveryCause.TRANSIENT_UI,
                qa_step("submit_keyboard", "qa_keyboard", "activate"),
            ),
        ),
    )
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "recipes.json"
        catalog = RecipeCatalog(RecipeStore(path))
        catalog.propose(recipe)
        approved = catalog.approve(
            RecipeApproval(recipe.recipe_id, recipe.revision, True),
            RecipeValidation(
                recipe.recipe_id,
                recipe.revision,
                application,
                ("fill", "submit"),
                True,
                "evidence-qa-001",
                "2026-08-25T12:00:00-03:00",
            ),
        )
        executor = QaExecutor()
        run = RecipeRunner(
            executor,
            QaVerifier(),
            catalog,
            logging.getLogger("recipe-qa"),
        ).run(
            approved,
            application,
            {"client_name": "Cliente Ficticio Privado"},
            ("window.ready", "user.confirmed"),
            ("qa_form", "qa_submit", "qa_keyboard"),
        )
        persisted = path.read_text(encoding="utf-8")
        if (
            run.status is not RecipeRunStatus.SUCCEEDED
            or executor.actions != ["fill", "submit", "submit_keyboard"]
            or "Cliente Ficticio Privado" in persisted
        ):
            print("RECIPE_QA_FAILED")
            return 1

    print(
        "RECIPE_QA_OK: receta aprobada con evidencia, recuperacion declarada "
        "y exito observable; parametros privados no persistidos."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
