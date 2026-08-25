import hashlib
import json
import logging
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from desktop_agent.models import Action, Intent, RiskLevel


_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


class PolicyError(RuntimeError):
    """La política o autorización no cumple el contrato local."""


class Effect(str, Enum):
    OBSERVE_LOCAL_STATE = "observe_local_state"
    OPEN_KNOWN_RESOURCE = "open_known_resource"
    CONTROL_MEDIA = "control_media"
    TYPE_NON_SENSITIVE = "type_non_sensitive"
    MODIFY_LOCAL_DATA = "modify_local_data"
    DELETE_RECOVERABLE_DATA = "delete_recoverable_data"
    SEND_EXTERNAL_DATA = "send_external_data"
    INSTALL_SOFTWARE = "install_software"
    HANDLE_CREDENTIALS = "handle_credentials"
    ALTER_SECURITY = "alter_security"
    EXECUTE_ARBITRARY_CODE = "execute_arbitrary_code"
    IRREVERSIBLE_DELETION = "irreversible_deletion"
    FINANCIAL_TRANSACTION = "financial_transaction"


class PolicyDisposition(str, Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    BLOCK = "block"


class PreparationStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    BLOCKED = "blocked"


class ConfirmationChannel(str, Enum):
    LOCAL = "local"
    REMOTE = "remote"


class AuthorizationStatus(str, Enum):
    AUTHORIZED = "authorized"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    INVALID = "invalid"
    ALREADY_USED = "already_used"


class _RequestState(str, Enum):
    PENDING = "pending"
    AUTHORIZED = "authorized"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    INVALID = "invalid"
    CONSUMED = "consumed"


class _GrantState(str, Enum):
    ACTIVE = "active"
    CONSUMED = "consumed"
    REVOKED = "revoked"
    EXPIRED = "expired"


_EFFECT_POLICY: dict[Effect, tuple[PolicyDisposition, RiskLevel]] = {
    Effect.OBSERVE_LOCAL_STATE: (PolicyDisposition.ALLOW, RiskLevel.SAFE),
    Effect.OPEN_KNOWN_RESOURCE: (PolicyDisposition.ALLOW, RiskLevel.SAFE),
    Effect.CONTROL_MEDIA: (PolicyDisposition.ALLOW, RiskLevel.SAFE),
    Effect.TYPE_NON_SENSITIVE: (PolicyDisposition.CONFIRM, RiskLevel.CAUTION),
    Effect.MODIFY_LOCAL_DATA: (PolicyDisposition.CONFIRM, RiskLevel.CAUTION),
    Effect.DELETE_RECOVERABLE_DATA: (
        PolicyDisposition.CONFIRM,
        RiskLevel.CAUTION,
    ),
    Effect.SEND_EXTERNAL_DATA: (
        PolicyDisposition.CONFIRM,
        RiskLevel.DANGEROUS,
    ),
    Effect.INSTALL_SOFTWARE: (PolicyDisposition.CONFIRM, RiskLevel.DANGEROUS),
    Effect.HANDLE_CREDENTIALS: (PolicyDisposition.BLOCK, RiskLevel.DANGEROUS),
    Effect.ALTER_SECURITY: (PolicyDisposition.BLOCK, RiskLevel.DANGEROUS),
    Effect.EXECUTE_ARBITRARY_CODE: (
        PolicyDisposition.BLOCK,
        RiskLevel.DANGEROUS,
    ),
    Effect.IRREVERSIBLE_DELETION: (
        PolicyDisposition.BLOCK,
        RiskLevel.DANGEROUS,
    ),
    Effect.FINANCIAL_TRANSACTION: (
        PolicyDisposition.BLOCK,
        RiskLevel.DANGEROUS,
    ),
}


def _safe_id(value: object, label: str) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise PolicyError(f"{label} no es un identificador seguro.")
    return value


def _safe_id_tuple(
    values: object,
    label: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise PolicyError(f"{label} no es una colección válida.")
    normalized = tuple(_safe_id(item, label) for item in values)
    if not normalized and not allow_empty:
        raise PolicyError(f"{label} no puede quedar vacío.")
    if len(set(normalized)) != len(normalized):
        raise PolicyError(f"{label} contiene duplicados.")
    return normalized


@dataclass(frozen=True)
class CapabilityRule:
    capability_id: str
    effect: Effect
    allowed_intents: tuple[Intent, ...]
    allowed_tools: tuple[str, ...]
    allowed_arguments: tuple[str, ...]
    destination_argument: str | None

    def __post_init__(self) -> None:
        _safe_id(self.capability_id, "capability_id")
        if not isinstance(self.effect, Effect):
            raise PolicyError("La regla no tiene un efecto válido.")
        if not isinstance(self.allowed_intents, (tuple, list)):
            raise PolicyError("Los intents permitidos no son válidos.")
        intents = tuple(self.allowed_intents)
        if not intents or any(not isinstance(item, Intent) for item in intents):
            raise PolicyError("Los intents permitidos no son válidos.")
        if len(set(intents)) != len(intents):
            raise PolicyError("Los intents permitidos contienen duplicados.")
        tools = _safe_id_tuple(self.allowed_tools, "allowed_tools")
        arguments = _safe_id_tuple(
            self.allowed_arguments,
            "allowed_arguments",
            allow_empty=True,
        )
        if self.destination_argument is not None:
            _safe_id(self.destination_argument, "destination_argument")
            if self.destination_argument not in arguments:
                raise PolicyError(
                    "El destino debe pertenecer a los argumentos permitidos."
                )
        object.__setattr__(self, "allowed_intents", intents)
        object.__setattr__(self, "allowed_tools", tools)
        object.__setattr__(self, "allowed_arguments", arguments)


@dataclass(frozen=True)
class PolicySubject:
    action_id: str
    capability_id: str
    action: Action

    def __post_init__(self) -> None:
        _safe_id(self.action_id, "action_id")
        _safe_id(self.capability_id, "capability_id")
        if not isinstance(self.action, Action):
            raise PolicyError("La acción no cumple el contrato local.")


@dataclass(frozen=True)
class PolicyAssessment:
    action_id: str
    capability_id: str
    effect: Effect
    disposition: PolicyDisposition
    risk_level: RiskLevel
    tool_name: str
    subject_fingerprint: str
    action_fingerprint: str
    destination: str | None
    argument_names: tuple[str, ...]


class PolicyRegistry:
    """Clasifica por efecto mediante reglas locales, no por texto del modelo."""

    def __init__(self, rules: tuple[CapabilityRule, ...]) -> None:
        if not isinstance(rules, tuple) or not rules:
            raise PolicyError("La política debe declarar reglas.")
        if any(not isinstance(rule, CapabilityRule) for rule in rules):
            raise PolicyError("La política contiene una regla inválida.")
        if len({rule.capability_id for rule in rules}) != len(rules):
            raise PolicyError("La política contiene capacidades duplicadas.")
        self._rules = {rule.capability_id: rule for rule in rules}

    def assess(self, subject: PolicySubject) -> PolicyAssessment:
        if not isinstance(subject, PolicySubject):
            raise PolicyError("La solicitud de política no es válida.")
        rule = self._rules.get(subject.capability_id)
        if rule is None:
            raise PolicyError("La capacidad no está registrada.")
        action = subject.action
        if action.intent not in rule.allowed_intents:
            raise PolicyError("La capacidad no permite ese intent.")
        if action.tool_name not in rule.allowed_tools:
            raise PolicyError("La capacidad no permite esa herramienta.")
        arguments = _validate_arguments(action)
        if set(arguments) != set(rule.allowed_arguments):
            raise PolicyError("Los argumentos no coinciden con la capacidad.")
        disposition, expected_risk = _EFFECT_POLICY[rule.effect]
        expected_confirmation = disposition is not PolicyDisposition.ALLOW
        if (
            action.risk_level is not expected_risk
            or action.requires_confirmation is not expected_confirmation
        ):
            raise PolicyError("La metadata de seguridad no coincide con el efecto.")
        destination: str | None = None
        if rule.destination_argument is not None:
            destination = arguments.get(rule.destination_argument)
            if not destination:
                raise PolicyError("La acción no declara el destino requerido.")
        action_fingerprint = _action_fingerprint(action)
        subject_fingerprint = _digest(
            {
                "action_id": subject.action_id,
                "capability_id": rule.capability_id,
                "effect": rule.effect.value,
                "action_fingerprint": action_fingerprint,
                "destination": destination,
            }
        )
        return PolicyAssessment(
            subject.action_id,
            rule.capability_id,
            rule.effect,
            disposition,
            expected_risk,
            action.tool_name,
            subject_fingerprint,
            action_fingerprint,
            destination,
            tuple(sorted(arguments)),
        )


@dataclass(frozen=True)
class PolicyLimits:
    confirmation_timeout_seconds: float = 30.0
    max_records: int = 1000

    def __post_init__(self) -> None:
        timeout = self.confirmation_timeout_seconds
        if type(timeout) not in (int, float) or not 1 <= float(timeout) <= 300:
            raise PolicyError("El timeout de confirmación no es válido.")
        if type(self.max_records) is not int or not 1 <= self.max_records <= 10000:
            raise PolicyError("El límite de confirmaciones no es válido.")
        object.__setattr__(self, "confirmation_timeout_seconds", float(timeout))


@dataclass(frozen=True)
class ConfirmationRequest:
    request_id: str
    challenge: str
    action_id: str
    capability_id: str
    effect: Effect
    risk_level: RiskLevel
    tool_name: str
    subject_fingerprint: str
    destination: str | None
    argument_names: tuple[str, ...]
    allowed_channels: tuple[ConfirmationChannel, ...]
    expires_at: float


@dataclass(frozen=True)
class PermissionPreparation:
    status: PreparationStatus
    assessment: PolicyAssessment
    request: ConfirmationRequest | None


@dataclass(frozen=True)
class ConfirmationDecision:
    request_id: str
    challenge: str
    subject_fingerprint: str
    approved: bool
    channel: ConfirmationChannel

    def __post_init__(self) -> None:
        _safe_id(self.request_id, "request_id")
        _safe_id(self.challenge, "challenge")
        if not isinstance(self.subject_fingerprint, str) or _SHA256.fullmatch(
            self.subject_fingerprint
        ) is None:
            raise PolicyError("El fingerprint de confirmación no es válido.")
        if type(self.approved) is not bool:
            raise PolicyError("La decisión debe ser explícita.")
        if not isinstance(self.channel, ConfirmationChannel):
            raise PolicyError("El canal de confirmación no es válido.")


@dataclass(frozen=True)
class AuthorizationToken:
    token_id: str
    request_id: str

    def __post_init__(self) -> None:
        _safe_id(self.token_id, "token_id")
        _safe_id(self.request_id, "request_id")


@dataclass(frozen=True)
class AuthorizationResult:
    status: AuthorizationStatus
    token: AuthorizationToken | None
    reason: str


@dataclass
class _RequestRecord:
    request: ConfirmationRequest
    action_fingerprint: str
    state: _RequestState
    token_id: str | None = None
    prepared_at: float = 0.0


@dataclass
class _GrantRecord:
    request_id: str
    action_fingerprint: str
    expires_at: float
    state: _GrantState


IdFactory = Callable[[], str]
Clock = Callable[[], float]


class PermissionBroker:
    """Emite y consume una autorización exacta, de un uso y con vencimiento."""

    def __init__(
        self,
        registry: PolicyRegistry,
        logger: logging.Logger,
        limits: PolicyLimits = PolicyLimits(),
        clock: Clock = time.monotonic,
        request_id_factory: IdFactory | None = None,
        challenge_factory: IdFactory | None = None,
        token_factory: IdFactory | None = None,
    ) -> None:
        if not isinstance(registry, PolicyRegistry):
            raise TypeError("El broker requiere una política local.")
        self._registry = registry
        self._logger = logger
        self._limits = limits
        self._clock = clock
        self._request_id_factory = request_id_factory or (
            lambda: f"request-{secrets.token_hex(16)}"
        )
        self._challenge_factory = challenge_factory or (
            lambda: f"challenge-{secrets.token_hex(24)}"
        )
        self._token_factory = token_factory or (
            lambda: f"grant-{secrets.token_hex(24)}"
        )
        self._requests: dict[str, _RequestRecord] = {}
        self._grants: dict[str, _GrantRecord] = {}
        self._lock = threading.Lock()

    def prepare(
        self,
        subject: PolicySubject,
        allowed_channels: tuple[ConfirmationChannel, ...] = (
            ConfirmationChannel.LOCAL,
        ),
    ) -> PermissionPreparation:
        started = self._clock()
        assessment = self._registry.assess(subject)
        channels = self._validate_channels(allowed_channels)
        if assessment.disposition is PolicyDisposition.ALLOW:
            self._log(assessment, PreparationStatus.NOT_REQUIRED.value, started)
            return PermissionPreparation(
                PreparationStatus.NOT_REQUIRED,
                assessment,
                None,
            )
        if assessment.disposition is PolicyDisposition.BLOCK:
            self._log(assessment, PreparationStatus.BLOCKED.value, started)
            return PermissionPreparation(
                PreparationStatus.BLOCKED,
                assessment,
                None,
            )
        with self._lock:
            if len(self._requests) >= self._limits.max_records:
                raise PolicyError("Se alcanzó el límite de confirmaciones.")
            request_id = _safe_id(self._request_id_factory(), "request_id")
            challenge = _safe_id(self._challenge_factory(), "challenge")
            if request_id in self._requests:
                raise PolicyError("No se pudo crear una confirmación única.")
            request = ConfirmationRequest(
                request_id,
                challenge,
                assessment.action_id,
                assessment.capability_id,
                assessment.effect,
                assessment.risk_level,
                assessment.tool_name,
                assessment.subject_fingerprint,
                assessment.destination,
                assessment.argument_names,
                channels,
                self._clock() + self._limits.confirmation_timeout_seconds,
            )
            self._requests[request_id] = _RequestRecord(
                request,
                assessment.action_fingerprint,
                _RequestState.PENDING,
                prepared_at=started,
            )
        self._log(assessment, PreparationStatus.PENDING.value, started)
        return PermissionPreparation(
            PreparationStatus.PENDING,
            assessment,
            request,
        )

    def authorize(
        self,
        subject: PolicySubject,
        decision: ConfirmationDecision,
    ) -> AuthorizationResult:
        started = self._clock()
        assessment = self._registry.assess(subject)
        if assessment.disposition is not PolicyDisposition.CONFIRM:
            raise PolicyError("La acción no admite confirmación.")
        with self._lock:
            record = self._requests.get(decision.request_id)
            if record is None:
                result = AuthorizationResult(
                    AuthorizationStatus.INVALID,
                    None,
                    "unknown_request",
                )
            else:
                result = self._authorize_locked(record, assessment, decision)
        self._log(assessment, result.status.value, started, decision.channel)
        return result

    def cancel(self, request_id: str) -> AuthorizationStatus:
        _safe_id(request_id, "request_id")
        with self._lock:
            record = self._requests.get(request_id)
            if record is None:
                return AuthorizationStatus.INVALID
            if record.state is _RequestState.CONSUMED:
                return AuthorizationStatus.ALREADY_USED
            terminal_status = {
                _RequestState.CANCELLED: AuthorizationStatus.CANCELLED,
                _RequestState.REJECTED: AuthorizationStatus.REJECTED,
                _RequestState.EXPIRED: AuthorizationStatus.EXPIRED,
                _RequestState.INVALID: AuthorizationStatus.INVALID,
            }.get(record.state)
            if terminal_status is not None:
                return terminal_status
            if self._clock() >= record.request.expires_at:
                if record.token_id is not None:
                    grant = self._grants.get(record.token_id)
                    if grant is not None and grant.state is _GrantState.ACTIVE:
                        grant.state = _GrantState.EXPIRED
                record.state = _RequestState.EXPIRED
                self._log_request(record, AuthorizationStatus.EXPIRED.value)
                return AuthorizationStatus.EXPIRED
            if record.token_id is not None:
                grant = self._grants.get(record.token_id)
                if grant is not None and grant.state is _GrantState.ACTIVE:
                    grant.state = _GrantState.REVOKED
            record.state = _RequestState.CANCELLED
            self._log_request(
                record,
                AuthorizationStatus.CANCELLED.value,
            )
            return AuthorizationStatus.CANCELLED

    def validate(self, action: Action, authorization: object) -> None:
        self._check_grant(action, authorization, consume=False)

    def consume(self, action: Action, authorization: object) -> None:
        self._check_grant(action, authorization, consume=True)

    def _authorize_locked(
        self,
        record: _RequestRecord,
        assessment: PolicyAssessment,
        decision: ConfirmationDecision,
    ) -> AuthorizationResult:
        if record.state is _RequestState.CONSUMED:
            return AuthorizationResult(
                AuthorizationStatus.ALREADY_USED,
                None,
                "already_consumed",
            )
        if record.state is not _RequestState.PENDING:
            return AuthorizationResult(
                _status_for_request_state(record.state),
                None,
                record.state.value,
            )
        if self._clock() >= record.request.expires_at:
            record.state = _RequestState.EXPIRED
            return AuthorizationResult(
                AuthorizationStatus.EXPIRED,
                None,
                "confirmation_expired",
            )
        matches = (
            decision.challenge == record.request.challenge
            and decision.subject_fingerprint
            == record.request.subject_fingerprint
            and assessment.subject_fingerprint
            == record.request.subject_fingerprint
            and assessment.action_fingerprint == record.action_fingerprint
            and decision.channel in record.request.allowed_channels
        )
        if not matches:
            record.state = _RequestState.INVALID
            return AuthorizationResult(
                AuthorizationStatus.INVALID,
                None,
                "confirmation_mismatch",
            )
        if not decision.approved:
            record.state = _RequestState.REJECTED
            return AuthorizationResult(
                AuthorizationStatus.REJECTED,
                None,
                "user_rejected",
            )
        token_id = _safe_id(self._token_factory(), "token_id")
        if token_id in self._grants:
            record.state = _RequestState.INVALID
            raise PolicyError("No se pudo crear una autorización única.")
        token = AuthorizationToken(token_id, record.request.request_id)
        self._grants[token_id] = _GrantRecord(
            record.request.request_id,
            assessment.action_fingerprint,
            record.request.expires_at,
            _GrantState.ACTIVE,
        )
        record.token_id = token_id
        record.state = _RequestState.AUTHORIZED
        return AuthorizationResult(
            AuthorizationStatus.AUTHORIZED,
            token,
            "user_approved",
        )

    def _check_grant(
        self,
        action: Action,
        authorization: object,
        *,
        consume: bool,
    ) -> None:
        if not isinstance(authorization, AuthorizationToken):
            raise PolicyError("La autorización no cumple el contrato.")
        fingerprint = _action_fingerprint(action)
        with self._lock:
            grant = self._grants.get(authorization.token_id)
            if (
                grant is None
                or grant.request_id != authorization.request_id
                or grant.action_fingerprint != fingerprint
                or grant.state is not _GrantState.ACTIVE
            ):
                raise PolicyError("La autorización no está activa para esa acción.")
            if self._clock() >= grant.expires_at:
                grant.state = _GrantState.EXPIRED
                request = self._requests[grant.request_id]
                request.state = _RequestState.EXPIRED
                raise PolicyError("La autorización venció.")
            if consume:
                grant.state = _GrantState.CONSUMED
                request = self._requests[grant.request_id]
                request.state = _RequestState.CONSUMED
                self._log_request(request, "consumed")

    @staticmethod
    def _validate_channels(
        channels: object,
    ) -> tuple[ConfirmationChannel, ...]:
        if not isinstance(channels, (tuple, list)):
            raise PolicyError("Los canales de confirmación no son válidos.")
        normalized = tuple(channels)
        if not normalized or any(
            not isinstance(channel, ConfirmationChannel)
            for channel in normalized
        ):
            raise PolicyError("Los canales de confirmación no son válidos.")
        if len(set(normalized)) != len(normalized):
            raise PolicyError("Los canales de confirmación contienen duplicados.")
        return normalized

    def _log(
        self,
        assessment: PolicyAssessment,
        status: str,
        started: float,
        channel: ConfirmationChannel | None = None,
    ) -> None:
        duration_ms = max(0.0, (self._clock() - started) * 1000)
        self._logger.info(
            "Permission: action=%s capability=%s effect=%s tool=%s "
            "status=%s channel=%s duration_ms=%.3f",
            assessment.action_id,
            assessment.capability_id,
            assessment.effect.value,
            assessment.tool_name,
            status,
            channel.value if channel else "none",
            duration_ms,
        )

    def _log_request(self, record: _RequestRecord, status: str) -> None:
        duration_ms = max(
            0.0,
            (self._clock() - record.prepared_at) * 1000,
        )
        request = record.request
        self._logger.info(
            "Permission: action=%s capability=%s effect=%s tool=%s "
            "status=%s channel=none duration_ms=%.3f",
            request.action_id,
            request.capability_id,
            request.effect.value,
            request.tool_name,
            status,
            duration_ms,
        )


def _validate_arguments(action: Action) -> dict[str, str]:
    arguments = dict(action.arguments)
    if len(arguments) > 20:
        raise PolicyError("La acción tiene demasiados argumentos.")
    for key, value in arguments.items():
        _safe_id(key, "argument_name")
        if not isinstance(value, str) or not value or len(value) > 4096:
            raise PolicyError("Un argumento de la acción no es válido.")
    return arguments


def _action_fingerprint(action: Action) -> str:
    arguments = _validate_arguments(action)
    return _digest(
        {
            "intent": action.intent.value,
            "tool_name": action.tool_name,
            "arguments": arguments,
            "risk_level": action.risk_level.value,
            "requires_confirmation": action.requires_confirmation,
        }
    )


def _digest(value: dict[str, object]) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _status_for_request_state(state: _RequestState) -> AuthorizationStatus:
    mapping = {
        _RequestState.AUTHORIZED: AuthorizationStatus.ALREADY_USED,
        _RequestState.REJECTED: AuthorizationStatus.REJECTED,
        _RequestState.EXPIRED: AuthorizationStatus.EXPIRED,
        _RequestState.CANCELLED: AuthorizationStatus.CANCELLED,
        _RequestState.INVALID: AuthorizationStatus.INVALID,
        _RequestState.CONSUMED: AuthorizationStatus.ALREADY_USED,
    }
    if state is _RequestState.PENDING:
        raise RuntimeError("El estado pendiente debe resolverse antes.")
    return mapping[state]
