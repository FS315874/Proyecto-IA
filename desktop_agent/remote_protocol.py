import base64
import binascii
import hashlib
import json
import re
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


REMOTE_SCHEMA_VERSION = 1
MAX_REMOTE_COMMAND_LENGTH = 500
MAX_PLAINTEXT_BYTES = 8192
MAX_CIPHERTEXT_BYTES = 16384
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_SENSITIVE_COMMAND = re.compile(
    r"password|passwd|contrase(?:n|ñ)a|credential|api[ _-]?key|"
    r"access[ _-]?token|refresh[ _-]?token|bearer|private[ _-]?key|"
    r"\btoken\b|secreto|secret",
    re.IGNORECASE,
)
_URL_CREDENTIAL = re.compile(r"https?://[^\s/@:]+:[^\s/@]+@", re.IGNORECASE)


class RemoteProtocolError(RuntimeError):
    """Un mensaje remoto no cumple autenticidad o esquema."""


class RemoteDirection(str, Enum):
    CLIENT_TO_AGENT = "client_to_agent"
    AGENT_TO_CLIENT = "agent_to_client"


class RemoteMessageType(str, Enum):
    COMMAND = "command"
    CANCEL = "cancel"
    CONFIRMATION = "confirmation"
    STATUS = "status"
    RESPONSE = "response"


class RemoteResponseStatus(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    STATUS = "status"
    ERROR = "error"


def _safe_id(value: object, label: str) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise RemoteProtocolError(f"{label} no es un identificador seguro.")
    return value


def validate_remote_command(value: object) -> str:
    if not isinstance(value, str):
        raise RemoteProtocolError("El comando remoto no es texto.")
    normalized = " ".join(value.strip().split())
    if not normalized or len(normalized) > MAX_REMOTE_COMMAND_LENGTH:
        raise RemoteProtocolError("El comando remoto está vacío o es demasiado largo.")
    if _SENSITIVE_COMMAND.search(normalized) or _URL_CREDENTIAL.search(normalized):
        raise RemoteProtocolError("El comando remoto parece contener un secreto.")
    return normalized


@dataclass(frozen=True)
class CommandPayload:
    command: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "command", validate_remote_command(self.command))


@dataclass(frozen=True)
class CancelPayload:
    task_id: str

    def __post_init__(self) -> None:
        _safe_id(self.task_id, "task_id")


@dataclass(frozen=True)
class ConfirmationPayload:
    task_id: str
    confirmation_request_id: str
    challenge: str
    subject_fingerprint: str
    approved: bool

    def __post_init__(self) -> None:
        _safe_id(self.task_id, "task_id")
        _safe_id(self.confirmation_request_id, "confirmation_request_id")
        _safe_id(self.challenge, "challenge")
        if not isinstance(self.subject_fingerprint, str) or _SHA256.fullmatch(
            self.subject_fingerprint
        ) is None:
            raise RemoteProtocolError("El fingerprint remoto no es válido.")
        if type(self.approved) is not bool:
            raise RemoteProtocolError("La confirmación remota debe ser explícita.")


@dataclass(frozen=True)
class StatusPayload:
    pass


@dataclass(frozen=True)
class RemoteTaskSummary:
    task_id: str
    state: str
    tool_name: str | None

    def __post_init__(self) -> None:
        _safe_id(self.task_id, "task_id")
        _safe_id(self.state, "task_state")
        if self.tool_name is not None:
            _safe_id(self.tool_name, "tool_name")


@dataclass(frozen=True)
class RemoteConfirmationSummary:
    task_id: str
    confirmation_request_id: str
    challenge: str
    subject_fingerprint: str
    effect: str
    risk_level: str
    destination: str | None

    def __post_init__(self) -> None:
        _safe_id(self.task_id, "task_id")
        _safe_id(self.confirmation_request_id, "confirmation_request_id")
        _safe_id(self.challenge, "challenge")
        if not isinstance(self.subject_fingerprint, str) or _SHA256.fullmatch(
            self.subject_fingerprint
        ) is None:
            raise RemoteProtocolError("El fingerprint remoto no es válido.")
        _safe_id(self.effect, "effect")
        _safe_id(self.risk_level, "risk_level")
        if self.destination is not None and (
            not isinstance(self.destination, str)
            or not self.destination
            or len(self.destination) > 2048
        ):
            raise RemoteProtocolError("El destino remoto no es válido.")


@dataclass(frozen=True)
class ResponsePayload:
    status: RemoteResponseStatus
    service_state: str
    reason: str
    task_id: str | None = None
    tasks: tuple[RemoteTaskSummary, ...] = ()
    pending_confirmation: RemoteConfirmationSummary | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, RemoteResponseStatus):
            raise RemoteProtocolError("La respuesta remota no tiene estado válido.")
        _safe_id(self.service_state, "service_state")
        _safe_id(self.reason, "reason")
        if self.task_id is not None:
            _safe_id(self.task_id, "task_id")
        if not isinstance(self.tasks, (tuple, list)) or len(self.tasks) > 20:
            raise RemoteProtocolError("El resumen remoto contiene demasiadas tareas.")
        tasks = tuple(self.tasks)
        if any(not isinstance(item, RemoteTaskSummary) for item in tasks):
            raise RemoteProtocolError("El resumen remoto contiene una tarea inválida.")
        if len({item.task_id for item in tasks}) != len(tasks):
            raise RemoteProtocolError("El resumen remoto contiene tareas duplicadas.")
        if self.pending_confirmation is not None and not isinstance(
            self.pending_confirmation,
            RemoteConfirmationSummary,
        ):
            raise RemoteProtocolError("La confirmación remota no es válida.")
        object.__setattr__(self, "tasks", tasks)


RemotePayload = (
    CommandPayload
    | CancelPayload
    | ConfirmationPayload
    | StatusPayload
    | ResponsePayload
)


@dataclass(frozen=True)
class RemoteMessage:
    message_type: RemoteMessageType
    request_id: str
    payload: RemotePayload

    def __post_init__(self) -> None:
        if not isinstance(self.message_type, RemoteMessageType):
            raise RemoteProtocolError("El mensaje remoto no tiene tipo válido.")
        _safe_id(self.request_id, "request_id")
        expected = {
            RemoteMessageType.COMMAND: CommandPayload,
            RemoteMessageType.CANCEL: CancelPayload,
            RemoteMessageType.CONFIRMATION: ConfirmationPayload,
            RemoteMessageType.STATUS: StatusPayload,
            RemoteMessageType.RESPONSE: ResponsePayload,
        }[self.message_type]
        if not isinstance(self.payload, expected):
            raise RemoteProtocolError("El payload remoto no coincide con su tipo.")


@dataclass(frozen=True)
class EncryptedEnvelope:
    device_id: str
    message_id: str
    sequence: int
    issued_at: int
    nonce: str
    ciphertext: str
    schema_version: int = REMOTE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != REMOTE_SCHEMA_VERSION:
            raise RemoteProtocolError("El sobre remoto usa otro esquema.")
        _safe_id(self.device_id, "device_id")
        _safe_id(self.message_id, "message_id")
        if type(self.sequence) is not int or not 1 <= self.sequence < 2**63:
            raise RemoteProtocolError("La secuencia remota no es válida.")
        if type(self.issued_at) is not int or self.issued_at < 0:
            raise RemoteProtocolError("La fecha remota no es válida.")
        nonce = _decode_base64(self.nonce, "nonce", 12)
        ciphertext = _decode_base64(
            self.ciphertext,
            "ciphertext",
            None,
        )
        if len(ciphertext) > MAX_CIPHERTEXT_BYTES:
            raise RemoteProtocolError("El ciphertext remoto es demasiado grande.")
        if len(nonce) != 12:
            raise RemoteProtocolError("El nonce remoto no tiene doce bytes.")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "device_id": self.device_id,
            "message_id": self.message_id,
            "sequence": self.sequence,
            "issued_at": self.issued_at,
            "nonce": self.nonce,
            "ciphertext": self.ciphertext,
        }

    @classmethod
    def from_dict(cls, value: object) -> "EncryptedEnvelope":
        fields = {
            "schema_version",
            "device_id",
            "message_id",
            "sequence",
            "issued_at",
            "nonce",
            "ciphertext",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise RemoteProtocolError("El sobre remoto no cumple el esquema.")
        try:
            return cls(
                device_id=value["device_id"],
                message_id=value["message_id"],
                sequence=value["sequence"],
                issued_at=value["issued_at"],
                nonce=value["nonce"],
                ciphertext=value["ciphertext"],
                schema_version=value["schema_version"],
            )
        except TypeError as error:
            raise RemoteProtocolError("El sobre remoto no es válido.") from error


NonceFactory = Callable[[int], bytes]
MessageIdFactory = Callable[[], str]
EpochClock = Callable[[], float]


class RemoteCodec:
    def __init__(
        self,
        direction: RemoteDirection,
        nonce_factory: NonceFactory = secrets.token_bytes,
        message_id_factory: MessageIdFactory | None = None,
        clock: EpochClock = time.time,
    ) -> None:
        if not isinstance(direction, RemoteDirection):
            raise TypeError("La dirección criptográfica no es válida.")
        self._direction = direction
        self._nonce_factory = nonce_factory
        self._message_id_factory = message_id_factory or (
            lambda: f"msg-{uuid.uuid4().hex}"
        )
        self._clock = clock

    def encrypt(
        self,
        device_id: str,
        secret: bytes,
        sequence: int,
        message: RemoteMessage,
    ) -> EncryptedEnvelope:
        _safe_id(device_id, "device_id")
        _validate_secret(secret)
        if type(sequence) is not int or not 1 <= sequence < 2**63:
            raise RemoteProtocolError("La secuencia remota no es válida.")
        if not isinstance(message, RemoteMessage):
            raise RemoteProtocolError("El mensaje remoto no cumple el contrato.")
        message_id = _safe_id(self._message_id_factory(), "message_id")
        issued_at = int(self._clock())
        if issued_at < 0:
            raise RemoteProtocolError("La fecha remota no es válida.")
        nonce = self._nonce_factory(12)
        if not isinstance(nonce, bytes) or len(nonce) != 12:
            raise RemoteProtocolError("El generador no produjo un nonce válido.")
        plaintext = _canonical_json(_message_to_data(message))
        if len(plaintext) > MAX_PLAINTEXT_BYTES:
            raise RemoteProtocolError("El mensaje remoto es demasiado grande.")
        header = _header_data(
            device_id,
            message_id,
            sequence,
            issued_at,
            self._direction,
        )
        ciphertext = AESGCM(
            _derive_key(device_id, secret, self._direction)
        ).encrypt(nonce, plaintext, _canonical_json(header))
        return EncryptedEnvelope(
            device_id,
            message_id,
            sequence,
            issued_at,
            _encode_base64(nonce),
            _encode_base64(ciphertext),
        )

    def decrypt(
        self,
        envelope: EncryptedEnvelope,
        secret: bytes,
    ) -> RemoteMessage:
        if not isinstance(envelope, EncryptedEnvelope):
            raise RemoteProtocolError("El sobre remoto no cumple el contrato.")
        _validate_secret(secret)
        nonce = _decode_base64(envelope.nonce, "nonce", 12)
        ciphertext = _decode_base64(
            envelope.ciphertext,
            "ciphertext",
            None,
        )
        header = _header_data(
            envelope.device_id,
            envelope.message_id,
            envelope.sequence,
            envelope.issued_at,
            self._direction,
        )
        try:
            plaintext = AESGCM(
                _derive_key(envelope.device_id, secret, self._direction)
            ).decrypt(nonce, ciphertext, _canonical_json(header))
        except InvalidTag as error:
            raise RemoteProtocolError("El sobre remoto no es auténtico.") from error
        if len(plaintext) > MAX_PLAINTEXT_BYTES:
            raise RemoteProtocolError("El mensaje remoto es demasiado grande.")
        try:
            value = json.loads(plaintext.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise RemoteProtocolError("El mensaje remoto no es JSON válido.") from error
        return _message_from_data(value)


def _message_to_data(message: RemoteMessage) -> dict[str, object]:
    payload = message.payload
    if isinstance(payload, CommandPayload):
        data: dict[str, object] = {"command": payload.command}
    elif isinstance(payload, CancelPayload):
        data = {"task_id": payload.task_id}
    elif isinstance(payload, ConfirmationPayload):
        data = {
            "task_id": payload.task_id,
            "confirmation_request_id": payload.confirmation_request_id,
            "challenge": payload.challenge,
            "subject_fingerprint": payload.subject_fingerprint,
            "approved": payload.approved,
        }
    elif isinstance(payload, StatusPayload):
        data = {}
    elif isinstance(payload, ResponsePayload):
        data = {
            "status": payload.status.value,
            "service_state": payload.service_state,
            "reason": payload.reason,
            "task_id": payload.task_id,
            "tasks": [
                {
                    "task_id": task.task_id,
                    "state": task.state,
                    "tool_name": task.tool_name,
                }
                for task in payload.tasks
            ],
            "pending_confirmation": (
                None
                if payload.pending_confirmation is None
                else {
                    "task_id": payload.pending_confirmation.task_id,
                    "confirmation_request_id": (
                        payload.pending_confirmation.confirmation_request_id
                    ),
                    "challenge": payload.pending_confirmation.challenge,
                    "subject_fingerprint": (
                        payload.pending_confirmation.subject_fingerprint
                    ),
                    "effect": payload.pending_confirmation.effect,
                    "risk_level": payload.pending_confirmation.risk_level,
                    "destination": payload.pending_confirmation.destination,
                }
            ),
        }
    else:
        raise RemoteProtocolError("El payload remoto no es serializable.")
    return {
        "schema_version": REMOTE_SCHEMA_VERSION,
        "message_type": message.message_type.value,
        "request_id": message.request_id,
        "payload": data,
    }


def _message_from_data(value: object) -> RemoteMessage:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "message_type",
        "request_id",
        "payload",
    }:
        raise RemoteProtocolError("El mensaje remoto no cumple el esquema.")
    if value["schema_version"] != REMOTE_SCHEMA_VERSION:
        raise RemoteProtocolError("El mensaje remoto usa otro esquema.")
    try:
        message_type = RemoteMessageType(value["message_type"])
    except (TypeError, ValueError) as error:
        raise RemoteProtocolError("El mensaje remoto usa un tipo inválido.") from error
    payload = value["payload"]
    if not isinstance(payload, dict):
        raise RemoteProtocolError("El payload remoto no es un objeto.")
    parsed = _payload_from_data(message_type, payload)
    return RemoteMessage(message_type, value["request_id"], parsed)


def _payload_from_data(
    message_type: RemoteMessageType,
    payload: dict[str, object],
) -> RemotePayload:
    try:
        if message_type is RemoteMessageType.COMMAND:
            _require_fields(payload, {"command"})
            return CommandPayload(payload["command"])
        if message_type is RemoteMessageType.CANCEL:
            _require_fields(payload, {"task_id"})
            return CancelPayload(payload["task_id"])
        if message_type is RemoteMessageType.CONFIRMATION:
            _require_fields(
                payload,
                {
                    "task_id",
                    "confirmation_request_id",
                    "challenge",
                    "subject_fingerprint",
                    "approved",
                },
            )
            return ConfirmationPayload(
                payload["task_id"],
                payload["confirmation_request_id"],
                payload["challenge"],
                payload["subject_fingerprint"],
                payload["approved"],
            )
        if message_type is RemoteMessageType.STATUS:
            _require_fields(payload, set())
            return StatusPayload()
        _require_fields(
            payload,
            {
                "status",
                "service_state",
                "reason",
                "task_id",
                "tasks",
                "pending_confirmation",
            },
        )
        tasks = payload["tasks"]
        if not isinstance(tasks, list):
            raise RemoteProtocolError("Las tareas remotas no son una lista.")
        parsed_tasks = tuple(_task_from_data(item) for item in tasks)
        pending_value = payload["pending_confirmation"]
        pending = (
            None
            if pending_value is None
            else _confirmation_summary_from_data(pending_value)
        )
        return ResponsePayload(
            RemoteResponseStatus(payload["status"]),
            payload["service_state"],
            payload["reason"],
            payload["task_id"],
            parsed_tasks,
            pending,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RemoteProtocolError("El payload remoto es inválido.") from error


def _task_from_data(value: object) -> RemoteTaskSummary:
    if not isinstance(value, dict):
        raise RemoteProtocolError("Una tarea remota no es válida.")
    _require_fields(value, {"task_id", "state", "tool_name"})
    return RemoteTaskSummary(value["task_id"], value["state"], value["tool_name"])


def _confirmation_summary_from_data(
    value: object,
) -> RemoteConfirmationSummary:
    fields = {
        "task_id",
        "confirmation_request_id",
        "challenge",
        "subject_fingerprint",
        "effect",
        "risk_level",
        "destination",
    }
    if not isinstance(value, dict):
        raise RemoteProtocolError("La confirmación remota no es válida.")
    _require_fields(value, fields)
    return RemoteConfirmationSummary(
        value["task_id"],
        value["confirmation_request_id"],
        value["challenge"],
        value["subject_fingerprint"],
        value["effect"],
        value["risk_level"],
        value["destination"],
    )


def _require_fields(value: dict[str, object], fields: set[str]) -> None:
    if set(value) != fields:
        raise RemoteProtocolError("El payload remoto contiene campos inesperados.")


def _derive_key(
    device_id: str,
    secret: bytes,
    direction: RemoteDirection,
) -> bytes:
    salt = hashlib.sha256(f"desktop-agent:{device_id}".encode()).digest()
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=f"desktop-agent-remote-v1:{direction.value}".encode(),
    ).derive(secret)


def _header_data(
    device_id: str,
    message_id: str,
    sequence: int,
    issued_at: int,
    direction: RemoteDirection,
) -> dict[str, object]:
    return {
        "schema_version": REMOTE_SCHEMA_VERSION,
        "device_id": device_id,
        "message_id": message_id,
        "sequence": sequence,
        "issued_at": issued_at,
        "direction": direction.value,
    }


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _validate_secret(secret: object) -> bytes:
    if not isinstance(secret, bytes) or len(secret) != 32:
        raise RemoteProtocolError("El secreto de dispositivo no es válido.")
    return secret


def _encode_base64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _decode_base64(
    value: object,
    label: str,
    expected_length: int | None,
) -> bytes:
    if not isinstance(value, str) or len(value) > 32768:
        raise RemoteProtocolError(f"{label} no es base64 válido.")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise RemoteProtocolError(f"{label} no es base64 válido.") from error
    if expected_length is not None and len(decoded) != expected_length:
        raise RemoteProtocolError(f"{label} no tiene el tamaño esperado.")
    return decoded
