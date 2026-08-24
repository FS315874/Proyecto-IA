from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from desktop_agent.catalog import SUPPORTED_APPLICATIONS, SUPPORTED_SITES
from desktop_agent.models import Action, Intent, RiskLevel
from desktop_agent.parser import parse_command

PROPOSAL_SCHEMA_VERSION = 1
_PROPOSAL_FIELDS = frozenset({"schema_version", "intent", "target"})


class ProposalIntent(str, Enum):
    """Intents que una fuente no confiable puede proponer."""

    OPEN_URL = "OPEN_URL"
    OPEN_APPLICATION = "OPEN_APPLICATION"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class ActionProposal:
    """Propuesta validada que todavía no tiene permiso de ejecución."""

    schema_version: int
    intent: ProposalIntent
    target: str | None


class ProposalValidationError(ValueError):
    """La propuesta no cumple el contrato o la política local."""


class ProposalProviderError(RuntimeError):
    """Un proveedor no pudo producir una propuesta interpretable."""


class ProposalProvider(Protocol):
    """Límite mínimo que deberá implementar un futuro adaptador externo."""

    def propose(self, command: str) -> object:
        """Devuelve datos no confiables para que el dominio los valide."""


DeterministicParser = Callable[[str], Action | None]


def _validate_proposal_values(
    schema_version: object,
    raw_intent: object,
    target: object,
) -> ActionProposal:
    if (
        type(schema_version) is not int
        or schema_version != PROPOSAL_SCHEMA_VERSION
    ):
        raise ProposalValidationError("La versión del contrato no está soportada.")
    if not isinstance(raw_intent, str):
        raise ProposalValidationError("El intent debe ser texto.")

    try:
        intent = ProposalIntent(raw_intent)
    except ValueError as error:
        raise ProposalValidationError("El intent no está permitido.") from error

    if target is not None and not isinstance(target, str):
        raise ProposalValidationError("El destino debe ser texto o null.")
    if isinstance(target, str) and (not target or target != target.strip()):
        raise ProposalValidationError(
            "El destino debe ser una clave canónica no vacía."
        )
    if intent is ProposalIntent.UNSUPPORTED and target is not None:
        raise ProposalValidationError("UNSUPPORTED requiere un destino null.")
    if intent is not ProposalIntent.UNSUPPORTED and target is None:
        raise ProposalValidationError("Un intent ejecutable requiere un destino.")

    return ActionProposal(
        schema_version=schema_version,
        intent=intent,
        target=target,
    )


def validate_proposal(payload: object) -> ActionProposal:
    """Valida estrictamente datos no confiables contra el contrato v1."""

    if not isinstance(payload, Mapping):
        raise ProposalValidationError("La propuesta debe ser un único objeto.")

    payload_fields = set(payload.keys())
    if payload_fields != _PROPOSAL_FIELDS:
        raise ProposalValidationError(
            "La propuesta debe contener exactamente schema_version, intent y target."
        )

    return _validate_proposal_values(
        schema_version=payload["schema_version"],
        raw_intent=payload["intent"],
        target=payload["target"],
    )


def build_action_from_proposal(proposal: ActionProposal) -> Action | None:
    """Construye una acción solo con catálogo, permisos y argumentos locales."""

    proposal = _validate_proposal_values(
        schema_version=proposal.schema_version,
        raw_intent=proposal.intent,
        target=proposal.target,
    )

    if proposal.intent is ProposalIntent.UNSUPPORTED:
        return None

    target = proposal.target
    assert target is not None

    if proposal.intent is ProposalIntent.OPEN_URL:
        site = SUPPORTED_SITES.get(target)
        if site is None:
            raise ProposalValidationError("El sitio no pertenece al catálogo local.")
        return Action(
            intent=Intent.OPEN_URL,
            tool_name="open_url",
            arguments={"url": site.url},
            risk_level=RiskLevel.SAFE,
            requires_confirmation=False,
        )

    if proposal.intent is ProposalIntent.OPEN_APPLICATION:
        if target not in SUPPORTED_APPLICATIONS:
            raise ProposalValidationError(
                "La aplicación no pertenece al catálogo local."
            )
        return Action(
            intent=Intent.OPEN_APPLICATION,
            tool_name="open_application",
            arguments={"name": target},
            risk_level=RiskLevel.SAFE,
            requires_confirmation=False,
        )

    raise ProposalValidationError("El intent no se puede convertir en una acción.")


class HybridInterpreter:
    """Usa primero el parser determinista y luego un proveedor opcional."""

    def __init__(
        self,
        provider: ProposalProvider | None = None,
        deterministic_parser: DeterministicParser = parse_command,
    ) -> None:
        self._provider = provider
        self._deterministic_parser = deterministic_parser

    def interpret(self, command: str) -> Action | None:
        action = self._deterministic_parser(command)
        if action is not None:
            return action

        if self._provider is None:
            return None

        try:
            payload = self._provider.propose(command)
            proposal = validate_proposal(payload)
            return build_action_from_proposal(proposal)
        except (ProposalProviderError, ProposalValidationError):
            return None
