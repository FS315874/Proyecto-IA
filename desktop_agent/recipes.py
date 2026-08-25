import json
import logging
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable


RECIPE_SCHEMA_VERSION = 1
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_SENSITIVE_NAME = re.compile(
    r"password|passwd|secret|token|credential|cookie|authorization|api[_-]?key",
    re.IGNORECASE,
)
_COORDINATE_OPERATION = re.compile(
    r"(^|[._:-])(coordinate|coordinates|mouse_xy|screen_xy|pixel)([._:-]|$)",
    re.IGNORECASE,
)


class RecipeError(RuntimeError):
    """La receta o su almacenamiento no cumplen el contrato seguro."""


class RecipeStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    STALE = "stale"
    DISABLED = "disabled"


class VerificationPredicate(str, Enum):
    ELEMENT_PRESENT = "element_present"
    STATE_EQUALS = "state_equals"
    VALUE_PRESENT = "value_present"
    PLAYBACK_ACTIVE = "playback_active"


class RecoveryCause(str, Enum):
    TRANSIENT_UI = "transient_ui"
    TIMEOUT = "timeout"


class InvalidationCause(str, Enum):
    APPLICATION_CHANGED = "application_changed"
    SELECTOR_CHANGED = "selector_changed"
    CONTRACT_CHANGED = "contract_changed"


class RecipeRunStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STALE = "stale"
    REJECTED = "rejected"


def _safe_id(value: object, label: str) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise RecipeError(f"{label} no es un identificador seguro.")
    return value


def _safe_ids(values: object, label: str, *, allow_empty: bool) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise RecipeError(f"{label} no es una colección válida.")
    normalized = tuple(_safe_id(value, label) for value in values)
    if not allow_empty and not normalized:
        raise RecipeError(f"{label} no puede quedar vacío.")
    if len(set(normalized)) != len(normalized):
        raise RecipeError(f"{label} contiene duplicados.")
    return normalized


@dataclass(frozen=True)
class ApplicationFingerprint:
    application_id: str
    application_version: str
    contract_hash: str

    def __post_init__(self) -> None:
        _safe_id(self.application_id, "application_id")
        _safe_id(self.application_version, "application_version")
        if not isinstance(self.contract_hash, str) or not _SHA256.fullmatch(
            self.contract_hash
        ):
            raise RecipeError("contract_hash debe ser un SHA-256 hexadecimal.")


@dataclass(frozen=True)
class StepVerification:
    predicate: VerificationPredicate
    evidence_key: str

    def __post_init__(self) -> None:
        if not isinstance(self.predicate, VerificationPredicate):
            raise RecipeError("La verificación no tiene un predicado válido.")
        _safe_id(self.evidence_key, "evidence_key")


@dataclass(frozen=True)
class SemanticAction:
    step_id: str
    tool_name: str
    operation: str
    target_key: str
    required_parameters: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _safe_id(self.step_id, "step_id")
        _safe_id(self.tool_name, "tool_name")
        operation = _safe_id(self.operation, "operation")
        _safe_id(self.target_key, "target_key")
        parameters = _safe_ids(
            self.required_parameters,
            "required_parameters",
            allow_empty=True,
        )
        if _COORDINATE_OPERATION.search(operation):
            raise RecipeError("Una receta no puede usar coordenadas como operación.")
        if any(_SENSITIVE_NAME.search(name) for name in parameters):
            raise RecipeError("Una receta no puede declarar parámetros sensibles.")
        object.__setattr__(self, "required_parameters", parameters)


@dataclass(frozen=True)
class RecipeStep:
    action: SemanticAction
    verification: StepVerification

    def __post_init__(self) -> None:
        if not isinstance(self.action, SemanticAction):
            raise RecipeError("El paso no tiene una acción semántica válida.")
        if not isinstance(self.verification, StepVerification):
            raise RecipeError("El paso no tiene una verificación válida.")


@dataclass(frozen=True)
class RecoveryRule:
    primary_step_id: str
    cause: RecoveryCause
    alternative: RecipeStep

    def __post_init__(self) -> None:
        _safe_id(self.primary_step_id, "primary_step_id")
        if not isinstance(self.cause, RecoveryCause):
            raise RecipeError("La recuperación no tiene una causa válida.")
        if not isinstance(self.alternative, RecipeStep):
            raise RecipeError("La recuperación no tiene una alternativa válida.")
        if self.alternative.action.step_id == self.primary_step_id:
            raise RecipeError("La alternativa debe ser un paso diferente.")


@dataclass(frozen=True)
class Recipe:
    recipe_id: str
    revision: int
    application: ApplicationFingerprint
    goal_key: str
    preconditions: tuple[str, ...]
    steps: tuple[RecipeStep, ...]
    recovery_rules: tuple[RecoveryRule, ...] = ()
    status: RecipeStatus = RecipeStatus.PROPOSED
    validated_at: str | None = None
    validation_evidence_id: str | None = None

    def __post_init__(self) -> None:
        _safe_id(self.recipe_id, "recipe_id")
        if type(self.revision) is not int or self.revision < 1:
            raise RecipeError("La revisión de receta no es válida.")
        if not isinstance(self.application, ApplicationFingerprint):
            raise RecipeError("La receta no tiene una huella de aplicación válida.")
        _safe_id(self.goal_key, "goal_key")
        preconditions = _safe_ids(
            self.preconditions,
            "preconditions",
            allow_empty=True,
        )
        if not isinstance(self.steps, (tuple, list)) or not 1 <= len(self.steps) <= 20:
            raise RecipeError("La receta debe tener entre 1 y 20 pasos.")
        steps = tuple(self.steps)
        if any(not isinstance(step, RecipeStep) for step in steps):
            raise RecipeError("La receta contiene un paso inválido.")
        step_ids = tuple(step.action.step_id for step in steps)
        if len(set(step_ids)) != len(step_ids):
            raise RecipeError("La receta contiene pasos duplicados.")
        if not isinstance(self.recovery_rules, (tuple, list)):
            raise RecipeError("Las recuperaciones no son válidas.")
        rules = tuple(self.recovery_rules)
        if len(rules) > len(steps):
            raise RecipeError("La receta tiene demasiadas recuperaciones.")
        if any(not isinstance(rule, RecoveryRule) for rule in rules):
            raise RecipeError("La receta contiene una recuperación inválida.")
        recovery_keys: set[tuple[str, RecoveryCause]] = set()
        alternative_ids: set[str] = set()
        for rule in rules:
            if rule.primary_step_id not in step_ids:
                raise RecipeError("La recuperación no referencia un paso principal.")
            key = (rule.primary_step_id, rule.cause)
            if key in recovery_keys:
                raise RecipeError("La receta duplica una recuperación.")
            if rule.alternative.action.step_id in set(step_ids) | alternative_ids:
                raise RecipeError("La alternativa duplica un identificador de paso.")
            recovery_keys.add(key)
            alternative_ids.add(rule.alternative.action.step_id)
        if not isinstance(self.status, RecipeStatus):
            raise RecipeError("La receta no tiene un estado válido.")
        has_validation = (
            self.validated_at is not None
            or self.validation_evidence_id is not None
        )
        if self.status is RecipeStatus.PROPOSED and has_validation:
            raise RecipeError("Una propuesta no puede declararse validada.")
        if self.status is not RecipeStatus.PROPOSED:
            _validate_timestamp(self.validated_at)
            _safe_id(self.validation_evidence_id, "validation_evidence_id")
        object.__setattr__(self, "preconditions", preconditions)
        object.__setattr__(self, "steps", steps)
        object.__setattr__(self, "recovery_rules", rules)


def _validate_timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise RecipeError("La fecha de validación no es válida.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RecipeError("La fecha de validación no es válida.") from error
    if parsed.tzinfo is None:
        raise RecipeError("La fecha de validación debe incluir zona horaria.")
    return value


@dataclass(frozen=True)
class RecipeValidation:
    recipe_id: str
    revision: int
    application: ApplicationFingerprint
    executed_step_ids: tuple[str, ...]
    observable_success: bool
    evidence_id: str
    validated_at: str

    def __post_init__(self) -> None:
        _safe_id(self.recipe_id, "recipe_id")
        if type(self.revision) is not int or self.revision < 1:
            raise RecipeError("La revisión validada no es válida.")
        if not isinstance(self.application, ApplicationFingerprint):
            raise RecipeError("La validación no tiene una aplicación válida.")
        step_ids = _safe_ids(
            self.executed_step_ids,
            "executed_step_ids",
            allow_empty=False,
        )
        if type(self.observable_success) is not bool:
            raise RecipeError("El éxito observable debe ser booleano.")
        _safe_id(self.evidence_id, "evidence_id")
        _validate_timestamp(self.validated_at)
        object.__setattr__(self, "executed_step_ids", step_ids)


@dataclass(frozen=True)
class RecipeApproval:
    recipe_id: str
    revision: int
    approved: bool

    def __post_init__(self) -> None:
        _safe_id(self.recipe_id, "recipe_id")
        if type(self.revision) is not int or self.revision < 1:
            raise RecipeError("La revisión aprobada no es válida.")
        if type(self.approved) is not bool:
            raise RecipeError("La aprobación debe ser explícita.")


class RecipeStore:
    """Persistencia JSON estricta; nunca recibe parámetros de ejecución."""

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path) or not path.is_absolute():
            raise RecipeError("La ruta de recetas debe ser absoluta.")
        self._path = path

    def load(self) -> tuple[Recipe, ...]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ()
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RecipeError("No se pudo leer el almacén de recetas.") from error
        if (
            not isinstance(raw, dict)
            or set(raw) != {"schema_version", "recipes"}
            or raw["schema_version"] != RECIPE_SCHEMA_VERSION
            or not isinstance(raw["recipes"], list)
        ):
            raise RecipeError("El almacén de recetas es inválido.")
        recipes = tuple(_recipe_from_data(item) for item in raw["recipes"])
        keys = [(recipe.recipe_id, recipe.revision) for recipe in recipes]
        if len(set(keys)) != len(keys):
            raise RecipeError("El almacén de recetas contiene duplicados.")
        return recipes

    def save(self, recipes: tuple[Recipe, ...]) -> None:
        if not isinstance(recipes, tuple) or any(
            not isinstance(recipe, Recipe) for recipe in recipes
        ):
            raise RecipeError("No se puede persistir una colección inválida.")
        payload = {
            "schema_version": RECIPE_SCHEMA_VERSION,
            "recipes": [_recipe_to_data(recipe) for recipe in recipes],
        }
        temporary = self._path.with_name(f"{self._path.name}.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with temporary.open("w", encoding="utf-8", newline="\n") as file:
                json.dump(payload, file, indent=2, sort_keys=True)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self._path)
        except OSError as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise RecipeError("No se pudo guardar el almacén de recetas.") from error


class RecipeCatalog:
    def __init__(self, store: RecipeStore) -> None:
        if not isinstance(store, RecipeStore):
            raise TypeError("El catálogo requiere un almacén de recetas.")
        self._store = store

    def propose(self, recipe: Recipe) -> None:
        if recipe.status is not RecipeStatus.PROPOSED:
            raise RecipeError("Solo se puede registrar una propuesta.")
        recipes = list(self._store.load())
        if any(
            item.recipe_id == recipe.recipe_id and item.revision == recipe.revision
            for item in recipes
        ):
            raise RecipeError("La receta ya existe.")
        recipes.append(recipe)
        self._store.save(tuple(recipes))

    def approve(
        self,
        approval: RecipeApproval,
        validation: RecipeValidation,
    ) -> Recipe:
        if not approval.approved:
            raise RecipeError("La receta no fue aprobada.")
        recipes = list(self._store.load())
        index, recipe = self._find(recipes, approval.recipe_id, approval.revision)
        if recipe.status is not RecipeStatus.PROPOSED:
            raise RecipeError("La receta ya no está propuesta.")
        expected_steps = tuple(step.action.step_id for step in recipe.steps)
        if (
            validation.recipe_id != recipe.recipe_id
            or validation.revision != recipe.revision
            or validation.application != recipe.application
            or validation.executed_step_ids != expected_steps
            or not validation.observable_success
        ):
            raise RecipeError("La validación observable no coincide con la receta.")
        approved = replace(
            recipe,
            status=RecipeStatus.APPROVED,
            validated_at=validation.validated_at,
            validation_evidence_id=validation.evidence_id,
        )
        recipes[index] = approved
        self._store.save(tuple(recipes))
        return approved

    def reusable(
        self,
        application: ApplicationFingerprint,
        goal_key: str,
    ) -> Recipe | None:
        _safe_id(goal_key, "goal_key")
        recipes = list(self._store.load())
        candidates = [
            (index, recipe)
            for index, recipe in enumerate(recipes)
            if recipe.application.application_id == application.application_id
            and recipe.goal_key == goal_key
            and recipe.status is RecipeStatus.APPROVED
        ]
        exact = [item for item in candidates if item[1].application == application]
        if exact:
            return max(exact, key=lambda item: item[1].revision)[1]
        changed = False
        for index, candidate in candidates:
            recipes[index] = replace(candidate, status=RecipeStatus.STALE)
            changed = True
        if changed:
            self._store.save(tuple(recipes))
        return None

    def invalidate(
        self,
        recipe_id: str,
        revision: int,
        cause: InvalidationCause,
    ) -> Recipe:
        if not isinstance(cause, InvalidationCause):
            raise RecipeError("La causa de invalidación no es válida.")
        recipes = list(self._store.load())
        index, recipe = self._find(recipes, recipe_id, revision)
        stale = replace(recipe, status=RecipeStatus.STALE)
        recipes[index] = stale
        self._store.save(tuple(recipes))
        return stale

    def disable(self, recipe_id: str, revision: int) -> Recipe:
        recipes = list(self._store.load())
        index, recipe = self._find(recipes, recipe_id, revision)
        if recipe.status is RecipeStatus.PROPOSED:
            raise RecipeError("Una propuesta debe rechazarse, no deshabilitarse.")
        disabled = replace(recipe, status=RecipeStatus.DISABLED)
        recipes[index] = disabled
        self._store.save(tuple(recipes))
        return disabled

    @staticmethod
    def _find(
        recipes: list[Recipe],
        recipe_id: str,
        revision: int,
    ) -> tuple[int, Recipe]:
        _safe_id(recipe_id, "recipe_id")
        if type(revision) is not int or revision < 1:
            raise RecipeError("La revisión no es válida.")
        matches = [
            (index, recipe)
            for index, recipe in enumerate(recipes)
            if recipe.recipe_id == recipe_id and recipe.revision == revision
        ]
        if len(matches) != 1:
            raise RecipeError("La receta no existe o no es única.")
        return matches[0]


@dataclass(frozen=True)
class RecipeActionResult:
    delivered: bool
    cause: RecoveryCause | InvalidationCause | None = None

    def __post_init__(self) -> None:
        if type(self.delivered) is not bool:
            raise RecipeError("El resultado de acción no es válido.")
        if self.delivered and self.cause is not None:
            raise RecipeError("Una acción entregada no admite causa.")
        if not self.delivered and not isinstance(
            self.cause,
            (RecoveryCause, InvalidationCause),
        ):
            raise RecipeError("Una acción fallida requiere una causa válida.")


@dataclass(frozen=True)
class RecipeVerificationResult:
    success: bool
    cause: RecoveryCause | InvalidationCause | None = None

    def __post_init__(self) -> None:
        if type(self.success) is not bool:
            raise RecipeError("El resultado de verificación no es válido.")
        if self.success and self.cause is not None:
            raise RecipeError("Una verificación exitosa no admite causa.")
        if not self.success and not isinstance(
            self.cause,
            (RecoveryCause, InvalidationCause),
        ):
            raise RecipeError("Una verificación fallida requiere una causa válida.")


@runtime_checkable
class RecipeActionExecutor(Protocol):
    def execute(
        self,
        action: SemanticAction,
        parameters: Mapping[str, str],
    ) -> RecipeActionResult: ...


@runtime_checkable
class RecipeStepVerifier(Protocol):
    def verify(
        self,
        verification: StepVerification,
    ) -> RecipeVerificationResult: ...


@dataclass(frozen=True)
class RecipeStepEvidence:
    step_id: str
    tool_name: str
    recovered: bool
    success: bool
    cause: RecoveryCause | InvalidationCause | None
    duration_ms: float


@dataclass(frozen=True)
class RecipeRun:
    recipe_id: str
    revision: int
    status: RecipeRunStatus
    steps: tuple[RecipeStepEvidence, ...]
    duration_ms: float
    reason: str


class RecipeRunner:
    """Ejecuta sólo recetas aprobadas y una alternativa declarada por fallo."""

    def __init__(
        self,
        executor: RecipeActionExecutor,
        verifier: RecipeStepVerifier,
        catalog: RecipeCatalog,
        logger: logging.Logger,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(executor, RecipeActionExecutor):
            raise TypeError("El ejecutor de recetas no cumple su contrato.")
        if not isinstance(verifier, RecipeStepVerifier):
            raise TypeError("El verificador de recetas no cumple su contrato.")
        if not isinstance(catalog, RecipeCatalog):
            raise TypeError("El runner requiere un catálogo de recetas.")
        self._executor = executor
        self._verifier = verifier
        self._catalog = catalog
        self._logger = logger
        self._clock = clock

    def run(
        self,
        recipe: Recipe,
        application: ApplicationFingerprint,
        parameters: Mapping[str, str],
        satisfied_preconditions: tuple[str, ...],
        allowed_tools: tuple[str, ...],
    ) -> RecipeRun:
        started = self._clock()
        if recipe.status is not RecipeStatus.APPROVED:
            return self._finish(
                recipe,
                RecipeRunStatus.REJECTED,
                (),
                started,
                "not_approved",
            )
        if recipe.application.application_id != application.application_id:
            return self._finish(
                recipe,
                RecipeRunStatus.REJECTED,
                (),
                started,
                "wrong_application",
            )
        if recipe.application != application:
            self._catalog.reusable(application, recipe.goal_key)
            return self._finish(
                recipe,
                RecipeRunStatus.STALE,
                (),
                started,
                "application_changed",
            )
        try:
            tools = _safe_ids(
                allowed_tools,
                "allowed_tools",
                allow_empty=False,
            )
        except RecipeError:
            return self._finish(
                recipe,
                RecipeRunStatus.REJECTED,
                (),
                started,
                "invalid_allowlist",
            )
        registered = self._catalog.reusable(application, recipe.goal_key)
        if registered != recipe:
            return self._finish(
                recipe,
                RecipeRunStatus.REJECTED,
                (),
                started,
                "not_registered",
            )
        try:
            preconditions = _safe_ids(
                satisfied_preconditions,
                "satisfied_preconditions",
                allow_empty=True,
            )
        except RecipeError:
            return self._finish(
                recipe,
                RecipeRunStatus.REJECTED,
                (),
                started,
                "invalid_preconditions",
            )
        if set(preconditions) != set(recipe.preconditions):
            return self._finish(
                recipe,
                RecipeRunStatus.REJECTED,
                (),
                started,
                "preconditions_not_satisfied",
            )
        declared_tools = {
            step.action.tool_name for step in recipe.steps
        } | {
            rule.alternative.action.tool_name
            for rule in recipe.recovery_rules
        }
        if not declared_tools.issubset(tools):
            return self._finish(
                recipe,
                RecipeRunStatus.REJECTED,
                (),
                started,
                "tool_not_allowed",
            )
        try:
            runtime_parameters = self._validate_parameters(recipe, parameters)
        except RecipeError:
            return self._finish(
                recipe,
                RecipeRunStatus.REJECTED,
                (),
                started,
                "invalid_parameters",
            )
        evidence: list[RecipeStepEvidence] = []
        recovery_map = {
            (rule.primary_step_id, rule.cause): rule
            for rule in recipe.recovery_rules
        }
        for step in recipe.steps:
            result, cause, elapsed = self._execute_step(step, runtime_parameters)
            evidence.append(
                RecipeStepEvidence(
                    step.action.step_id,
                    step.action.tool_name,
                    False,
                    result,
                    cause,
                    elapsed,
                )
            )
            if result:
                continue
            if isinstance(cause, InvalidationCause):
                self._catalog.invalidate(recipe.recipe_id, recipe.revision, cause)
                return self._finish(
                    recipe,
                    RecipeRunStatus.STALE,
                    tuple(evidence),
                    started,
                    cause.value,
                )
            assert isinstance(cause, RecoveryCause)
            recovery = recovery_map.get((step.action.step_id, cause))
            if recovery is None:
                return self._finish(
                    recipe,
                    RecipeRunStatus.FAILED,
                    tuple(evidence),
                    started,
                    "no_approved_recovery",
                )
            recovered, recovery_cause, recovery_elapsed = self._execute_step(
                recovery.alternative,
                runtime_parameters,
            )
            evidence.append(
                RecipeStepEvidence(
                    recovery.alternative.action.step_id,
                    recovery.alternative.action.tool_name,
                    True,
                    recovered,
                    recovery_cause,
                    recovery_elapsed,
                )
            )
            if not recovered:
                if isinstance(recovery_cause, InvalidationCause):
                    self._catalog.invalidate(
                        recipe.recipe_id,
                        recipe.revision,
                        recovery_cause,
                    )
                    status = RecipeRunStatus.STALE
                    reason = recovery_cause.value
                else:
                    status = RecipeRunStatus.FAILED
                    reason = "approved_recovery_failed"
                return self._finish(
                    recipe,
                    status,
                    tuple(evidence),
                    started,
                    reason,
                )
        return self._finish(
            recipe,
            RecipeRunStatus.SUCCEEDED,
            tuple(evidence),
            started,
            "observable_success",
        )

    def _execute_step(
        self,
        step: RecipeStep,
        parameters: Mapping[str, str],
    ) -> tuple[bool, RecoveryCause | InvalidationCause | None, float]:
        started = self._clock()
        try:
            minimal_parameters = {
                name: parameters[name]
                for name in step.action.required_parameters
            }
            action_result = self._executor.execute(
                step.action,
                minimal_parameters,
            )
            if not isinstance(action_result, RecipeActionResult):
                return False, InvalidationCause.CONTRACT_CHANGED, self._elapsed(started)
            if not action_result.delivered:
                return False, action_result.cause, self._elapsed(started)
            verification = self._verifier.verify(step.verification)
            if not isinstance(verification, RecipeVerificationResult):
                return False, InvalidationCause.CONTRACT_CHANGED, self._elapsed(started)
            return verification.success, verification.cause, self._elapsed(started)
        except Exception:
            return False, InvalidationCause.CONTRACT_CHANGED, self._elapsed(started)

    @staticmethod
    def _validate_parameters(
        recipe: Recipe,
        parameters: Mapping[str, str],
    ) -> dict[str, str]:
        if not isinstance(parameters, Mapping):
            raise RecipeError("Los parámetros de ejecución no son válidos.")
        required = {
            name
            for step in recipe.steps
            for name in step.action.required_parameters
        }
        required.update(
            name
            for rule in recipe.recovery_rules
            for name in rule.alternative.action.required_parameters
        )
        if set(parameters) != required:
            raise RecipeError("Los parámetros no coinciden con la receta.")
        normalized: dict[str, str] = {}
        for name, value in parameters.items():
            _safe_id(name, "parameter_name")
            if _SENSITIVE_NAME.search(name):
                raise RecipeError("Los parámetros sensibles no están permitidos.")
            if not isinstance(value, str) or not value or len(value) > 500:
                raise RecipeError("Un valor de ejecución no es válido.")
            normalized[name] = value
        return normalized

    def _finish(
        self,
        recipe: Recipe,
        status: RecipeRunStatus,
        steps: tuple[RecipeStepEvidence, ...],
        started: float,
        reason: str,
    ) -> RecipeRun:
        duration = self._elapsed(started)
        self._logger.info(
            "Recipe: recipe=%s revision=%d status=%s steps=%d "
            "duration_ms=%.3f reason=%s",
            recipe.recipe_id,
            recipe.revision,
            status.value.upper(),
            len(steps),
            duration,
            reason,
        )
        return RecipeRun(
            recipe.recipe_id,
            recipe.revision,
            status,
            steps,
            duration,
            reason,
        )

    def _elapsed(self, started: float) -> float:
        return max(0.0, (self._clock() - started) * 1000)


def _recipe_to_data(recipe: Recipe) -> dict[str, object]:
    return {
        "recipe_id": recipe.recipe_id,
        "revision": recipe.revision,
        "application": {
            "application_id": recipe.application.application_id,
            "application_version": recipe.application.application_version,
            "contract_hash": recipe.application.contract_hash,
        },
        "goal_key": recipe.goal_key,
        "preconditions": list(recipe.preconditions),
        "steps": [_step_to_data(step) for step in recipe.steps],
        "recovery_rules": [
            {
                "primary_step_id": rule.primary_step_id,
                "cause": rule.cause.value,
                "alternative": _step_to_data(rule.alternative),
            }
            for rule in recipe.recovery_rules
        ],
        "status": recipe.status.value,
        "validated_at": recipe.validated_at,
        "validation_evidence_id": recipe.validation_evidence_id,
    }


def _step_to_data(step: RecipeStep) -> dict[str, object]:
    return {
        "action": {
            "step_id": step.action.step_id,
            "tool_name": step.action.tool_name,
            "operation": step.action.operation,
            "target_key": step.action.target_key,
            "required_parameters": list(step.action.required_parameters),
        },
        "verification": {
            "predicate": step.verification.predicate.value,
            "evidence_key": step.verification.evidence_key,
        },
    }


def _recipe_from_data(value: object) -> Recipe:
    fields = {
        "recipe_id",
        "revision",
        "application",
        "goal_key",
        "preconditions",
        "steps",
        "recovery_rules",
        "status",
        "validated_at",
        "validation_evidence_id",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise RecipeError("Una receta persistida es inválida.")
    application = value["application"]
    if not isinstance(application, dict) or set(application) != {
        "application_id",
        "application_version",
        "contract_hash",
    }:
        raise RecipeError("La aplicación persistida es inválida.")
    raw_steps = value["steps"]
    raw_rules = value["recovery_rules"]
    if not isinstance(raw_steps, list) or not isinstance(raw_rules, list):
        raise RecipeError("Los pasos persistidos son inválidos.")
    try:
        return Recipe(
            recipe_id=value["recipe_id"],
            revision=value["revision"],
            application=ApplicationFingerprint(**application),
            goal_key=value["goal_key"],
            preconditions=value["preconditions"],
            steps=tuple(_step_from_data(item) for item in raw_steps),
            recovery_rules=tuple(
                _recovery_from_data(item) for item in raw_rules
            ),
            status=RecipeStatus(value["status"]),
            validated_at=value["validated_at"],
            validation_evidence_id=value["validation_evidence_id"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RecipeError("Una receta persistida es inválida.") from error


def _step_from_data(value: object) -> RecipeStep:
    if not isinstance(value, dict) or set(value) != {"action", "verification"}:
        raise RecipeError("Un paso persistido es inválido.")
    action = value["action"]
    verification = value["verification"]
    if not isinstance(action, dict) or set(action) != {
        "step_id",
        "tool_name",
        "operation",
        "target_key",
        "required_parameters",
    }:
        raise RecipeError("Una acción persistida es inválida.")
    if not isinstance(verification, dict) or set(verification) != {
        "predicate",
        "evidence_key",
    }:
        raise RecipeError("Una verificación persistida es inválida.")
    try:
        return RecipeStep(
            SemanticAction(**action),
            StepVerification(
                VerificationPredicate(verification["predicate"]),
                verification["evidence_key"],
            ),
        )
    except (TypeError, ValueError) as error:
        raise RecipeError("Un paso persistido es inválido.") from error


def _recovery_from_data(value: object) -> RecoveryRule:
    if not isinstance(value, dict) or set(value) != {
        "primary_step_id",
        "cause",
        "alternative",
    }:
        raise RecipeError("Una recuperación persistida es inválida.")
    try:
        return RecoveryRule(
            value["primary_step_id"],
            RecoveryCause(value["cause"]),
            _step_from_data(value["alternative"]),
        )
    except (TypeError, ValueError) as error:
        raise RecipeError("Una recuperación persistida es inválida.") from error
