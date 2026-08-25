import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from desktop_agent.remote_protocol import EncryptedEnvelope, RemoteProtocolError
from desktop_agent.windows_secrets import SecretProtector, WindowsDpapiProtector


DEVICE_SCHEMA_VERSION = 1
PAIRING_LIFETIME_SECONDS = 300
MAX_DEVICES = 10
MAX_RECENT_MESSAGES = 64
MAX_CLOCK_SKEW_SECONDS = 60
_PAIRING_CONTEXT = b"desktop-agent-pairing-v1:"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")


class DeviceRegistryError(RuntimeError):
    """El registro local rechazó una operación de dispositivo."""


class DeviceStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(frozen=True)
class PairingBundle:
    device_id: str
    secret: bytes
    expires_at: int

    def proof(self) -> str:
        return pairing_proof(self.device_id, self.secret)


@dataclass(frozen=True)
class DeviceSummary:
    device_id: str
    label: str
    status: DeviceStatus
    created_at: int
    paired_at: int | None
    last_seen_at: int | None


Clock = Callable[[], float]
SecretFactory = Callable[[int], bytes]
IdFactory = Callable[[], str]


def pairing_proof(device_id: str, secret: bytes) -> str:
    if not isinstance(secret, bytes) or len(secret) != 32:
        raise DeviceRegistryError("El secreto de emparejamiento no es válido.")
    return hmac.new(
        secret,
        _PAIRING_CONTEXT + device_id.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def default_registry_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / "DesktopAgent" / "devices.json"


class DeviceRegistry:
    def __init__(
        self,
        path: Path | None = None,
        protector: SecretProtector | None = None,
        clock: Clock = time.time,
        secret_factory: SecretFactory = secrets.token_bytes,
        id_factory: IdFactory | None = None,
    ) -> None:
        self._path = path or default_registry_path()
        self._protector = protector or WindowsDpapiProtector()
        self._clock = clock
        self._secret_factory = secret_factory
        self._id_factory = id_factory or (lambda: f"device-{uuid.uuid4().hex}")
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    def begin_pairing(self, label: str) -> PairingBundle:
        clean_label = " ".join(label.strip().split()) if isinstance(label, str) else ""
        if not clean_label or len(clean_label) > 80:
            raise DeviceRegistryError("La etiqueta del dispositivo no es válida.")
        secret = self._secret_factory(32)
        if not isinstance(secret, bytes) or len(secret) != 32:
            raise DeviceRegistryError("No se pudo generar un secreto seguro.")
        device_id = self._id_factory()
        if not isinstance(device_id, str) or _SAFE_ID.fullmatch(device_id) is None:
            raise DeviceRegistryError("No se pudo crear un dispositivo seguro.")
        now = int(self._clock())
        protected = self._protector.protect(secret)
        with self._lock:
            state = self._load()
            current = [
                item for item in state["devices"]
                if item["status"] != DeviceStatus.REVOKED.value
            ]
            if len(current) >= MAX_DEVICES:
                raise DeviceRegistryError("Se alcanzó el límite de dispositivos.")
            if any(item["device_id"] == device_id for item in state["devices"]):
                raise DeviceRegistryError("No se pudo crear un dispositivo único.")
            state["devices"].append(
                {
                    "device_id": device_id,
                    "label": clean_label,
                    "status": DeviceStatus.PENDING.value,
                    "created_at": now,
                    "expires_at": now + PAIRING_LIFETIME_SECONDS,
                    "paired_at": None,
                    "last_seen_at": None,
                    "protected_secret": base64.b64encode(protected).decode("ascii"),
                    "last_inbound_sequence": 0,
                    "next_outbound_sequence": 1,
                    "recent_message_ids": [],
                }
            )
            self._save(state)
        return PairingBundle(device_id, secret, now + PAIRING_LIFETIME_SECONDS)

    def complete_pairing(
        self,
        device_id: str,
        proof: str,
        *,
        approved: bool,
    ) -> DeviceSummary:
        if approved is not True:
            raise DeviceRegistryError("El emparejamiento requiere aprobación local.")
        now = int(self._clock())
        with self._lock:
            state = self._load()
            device = self._find(state, device_id)
            if device["status"] != DeviceStatus.PENDING.value:
                raise DeviceRegistryError("El dispositivo no está pendiente.")
            if now >= device["expires_at"]:
                device["status"] = DeviceStatus.REVOKED.value
                device["protected_secret"] = None
                self._save(state)
                raise DeviceRegistryError("El emparejamiento expiró.")
            secret = self._unprotect(device)
            expected = pairing_proof(device_id, secret)
            if not isinstance(proof, str) or not hmac.compare_digest(proof, expected):
                raise DeviceRegistryError("La prueba de emparejamiento no coincide.")
            device["status"] = DeviceStatus.ACTIVE.value
            device["paired_at"] = now
            self._save(state)
            return self._summary(device)

    def active_secret(self, device_id: str) -> bytes:
        with self._lock:
            device = self._find(self._load(), device_id)
            if device["status"] != DeviceStatus.ACTIVE.value:
                raise DeviceRegistryError("El dispositivo no está activo.")
            return self._unprotect(device)

    def claim_inbound(self, envelope: EncryptedEnvelope) -> None:
        if not isinstance(envelope, EncryptedEnvelope):
            raise DeviceRegistryError("El sobre remoto no es válido.")
        now = int(self._clock())
        if abs(now - envelope.issued_at) > MAX_CLOCK_SKEW_SECONDS:
            raise DeviceRegistryError("El mensaje remoto está fuera de tiempo.")
        with self._lock:
            state = self._load()
            device = self._find(state, envelope.device_id)
            if device["status"] != DeviceStatus.ACTIVE.value:
                raise DeviceRegistryError("El dispositivo no está activo.")
            if envelope.sequence <= device["last_inbound_sequence"]:
                raise DeviceRegistryError("La secuencia remota ya fue usada.")
            recent = device["recent_message_ids"]
            if envelope.message_id in recent:
                raise DeviceRegistryError("El mensaje remoto ya fue usado.")
            device["last_inbound_sequence"] = envelope.sequence
            device["last_seen_at"] = now
            device["recent_message_ids"] = (
                recent + [envelope.message_id]
            )[-MAX_RECENT_MESSAGES:]
            self._save(state)

    def reserve_outbound_sequence(self, device_id: str) -> int:
        with self._lock:
            state = self._load()
            device = self._find(state, device_id)
            if device["status"] != DeviceStatus.ACTIVE.value:
                raise DeviceRegistryError("El dispositivo no está activo.")
            sequence = device["next_outbound_sequence"]
            if sequence >= 2**63 - 1:
                raise DeviceRegistryError("La secuencia remota se agotó.")
            device["next_outbound_sequence"] = sequence + 1
            self._save(state)
            return sequence

    def revoke(self, device_id: str) -> DeviceSummary:
        with self._lock:
            state = self._load()
            device = self._find(state, device_id)
            device["status"] = DeviceStatus.REVOKED.value
            device["protected_secret"] = None
            device["recent_message_ids"] = []
            self._save(state)
            return self._summary(device)

    def list_devices(self) -> tuple[DeviceSummary, ...]:
        with self._lock:
            state = self._load()
            return tuple(self._summary(item) for item in state["devices"])

    def _load(self) -> dict[str, object]:
        if not self._path.exists():
            return {"schema_version": DEVICE_SCHEMA_VERSION, "devices": []}
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise DeviceRegistryError(
                "El registro de dispositivos está dañado."
            ) from error
        self._validate_state(value)
        return value

    def _save(self, state: dict[str, object]) -> None:
        self._validate_state(state)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        try:
            temporary.write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self._path)
        except OSError as error:
            raise DeviceRegistryError("No se pudo guardar el registro.") from error

    @staticmethod
    def _find(state: dict[str, object], device_id: str) -> dict[str, object]:
        if not isinstance(device_id, str):
            raise DeviceRegistryError("El dispositivo no es válido.")
        for item in state["devices"]:
            if item["device_id"] == device_id:
                return item
        raise DeviceRegistryError("El dispositivo no existe.")

    def _unprotect(self, device: dict[str, object]) -> bytes:
        value = device.get("protected_secret")
        if not isinstance(value, str):
            raise DeviceRegistryError("El dispositivo no conserva un secreto activo.")
        try:
            protected = base64.b64decode(value, validate=True)
            secret = self._protector.unprotect(protected)
        except (binascii.Error, ValueError) as error:
            raise DeviceRegistryError("El secreto protegido no es válido.") from error
        if len(secret) != 32:
            raise DeviceRegistryError("El secreto recuperado no es válido.")
        return secret

    @staticmethod
    def _summary(device: dict[str, object]) -> DeviceSummary:
        return DeviceSummary(
            device["device_id"],
            device["label"],
            DeviceStatus(device["status"]),
            device["created_at"],
            device["paired_at"],
            device["last_seen_at"],
        )

    @staticmethod
    def _validate_state(value: object) -> None:
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "devices",
        }:
            raise DeviceRegistryError("El registro no cumple el esquema.")
        if value["schema_version"] != DEVICE_SCHEMA_VERSION:
            raise DeviceRegistryError("El registro usa otro esquema.")
        devices = value["devices"]
        if not isinstance(devices, list) or len(devices) > MAX_DEVICES * 4:
            raise DeviceRegistryError("La lista de dispositivos no es válida.")
        required = {
            "device_id", "label", "status", "created_at", "expires_at",
            "paired_at", "last_seen_at", "protected_secret",
            "last_inbound_sequence", "next_outbound_sequence",
            "recent_message_ids",
        }
        seen: set[str] = set()
        for item in devices:
            if not isinstance(item, dict) or set(item) != required:
                raise DeviceRegistryError("Un dispositivo no cumple el esquema.")
            try:
                status = DeviceStatus(item["status"])
            except (TypeError, ValueError) as error:
                raise DeviceRegistryError(
                    "Un dispositivo tiene estado inválido."
                ) from error
            identifier = item["device_id"]
            if (
                not isinstance(identifier, str)
                or _SAFE_ID.fullmatch(identifier) is None
                or identifier in seen
            ):
                raise DeviceRegistryError("Un dispositivo tiene ID inválido.")
            seen.add(identifier)
            label = item["label"]
            if not isinstance(label, str) or not label or len(label) > 80:
                raise DeviceRegistryError("Un dispositivo tiene etiqueta inválida.")
            for key in ("created_at", "expires_at"):
                if type(item[key]) is not int or item[key] < 0:
                    raise DeviceRegistryError("Un dispositivo tiene fecha inválida.")
            for key in ("paired_at", "last_seen_at"):
                if item[key] is not None and (
                    type(item[key]) is not int or item[key] < 0
                ):
                    raise DeviceRegistryError("Un dispositivo tiene fecha inválida.")
            inbound = item["last_inbound_sequence"]
            outbound = item["next_outbound_sequence"]
            if type(inbound) is not int or not 0 <= inbound < 2**63:
                raise DeviceRegistryError("Un dispositivo tiene secuencia inválida.")
            if type(outbound) is not int or not 1 <= outbound < 2**63:
                raise DeviceRegistryError("Un dispositivo tiene secuencia inválida.")
            if status is not DeviceStatus.REVOKED and not isinstance(
                item["protected_secret"], str
            ):
                raise DeviceRegistryError("Falta un secreto protegido.")
            recent = item["recent_message_ids"]
            if (
                not isinstance(recent, list)
                or len(recent) > MAX_RECENT_MESSAGES
                or any(
                    not isinstance(message_id, str)
                    or _SAFE_ID.fullmatch(message_id) is None
                    for message_id in recent
                )
            ):
                raise DeviceRegistryError("El historial remoto no es válido.")
            if len(set(recent)) != len(recent):
                raise DeviceRegistryError("El historial remoto no es válido.")
