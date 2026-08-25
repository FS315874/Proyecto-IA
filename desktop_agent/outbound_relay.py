import http.client
import json
import logging
import re
import ssl
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from desktop_agent.remote_protocol import EncryptedEnvelope, RemoteProtocolError


MAX_RELAY_RESPONSE_BYTES = 32768
_HOST = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9.-]+$")
_RELAY_ID = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


class RelayError(RuntimeError):
    """El relay saliente no cumplió el contrato seguro."""


class RelayState(str, Enum):
    OFFLINE = "offline"
    CONNECTING = "connecting"
    ONLINE = "online"
    ERROR = "error"
    STOPPED = "stopped"


@dataclass(frozen=True)
class RelayEndpoint:
    host: str
    relay_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.host, str) or _HOST.fullmatch(self.host) is None:
            raise RelayError("El host del relay no es válido.")
        if not isinstance(self.relay_id, str) or _RELAY_ID.fullmatch(
            self.relay_id
        ) is None:
            raise RelayError("El identificador público del relay no es válido.")


ConnectionFactory = Callable[..., http.client.HTTPSConnection]


@runtime_checkable
class RelayTransport(Protocol):
    def receive(self) -> EncryptedEnvelope | None: ...

    def send(self, envelope: EncryptedEnvelope) -> None: ...


@runtime_checkable
class IncomingGateway(Protocol):
    def handle(self, envelope: EncryptedEnvelope) -> EncryptedEnvelope | None: ...


class HttpsRelayTransport:
    """Polling HTTPS saliente; el relay solo transporta sobres cifrados."""

    def __init__(
        self,
        endpoint: RelayEndpoint,
        timeout_seconds: float = 15.0,
        connection_factory: ConnectionFactory = http.client.HTTPSConnection,
    ) -> None:
        if not isinstance(endpoint, RelayEndpoint):
            raise TypeError("El endpoint del relay no es válido.")
        if type(timeout_seconds) not in (int, float) or not 1 <= timeout_seconds <= 60:
            raise ValueError("El timeout del relay no es válido.")
        self._endpoint = endpoint
        self._timeout = float(timeout_seconds)
        self._connection_factory = connection_factory

    def receive(self) -> EncryptedEnvelope | None:
        value = self._request("GET", self._path("poll"), None)
        if value is None:
            return None
        try:
            return EncryptedEnvelope.from_dict(value)
        except RemoteProtocolError as error:
            raise RelayError("El relay devolvió un sobre inválido.") from error

    def send(self, envelope: EncryptedEnvelope) -> None:
        if not isinstance(envelope, EncryptedEnvelope):
            raise TypeError("El sobre de salida no es válido.")
        self._request("POST", self._path("messages"), envelope.to_dict())

    def _path(self, operation: str) -> str:
        return f"/v1/relay/{self._endpoint.relay_id}/{operation}"

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None,
    ) -> object:
        context = ssl.create_default_context()
        connection = self._connection_factory(
            self._endpoint.host,
            443,
            timeout=self._timeout,
            context=context,
        )
        encoded = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Accept": "application/json"}
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        try:
            connection.request(method, path, body=encoded, headers=headers)
            response = connection.getresponse()
            raw = response.read(MAX_RELAY_RESPONSE_BYTES + 1)
        except (OSError, http.client.HTTPException) as error:
            raise RelayError("No se pudo comunicar con el relay.") from error
        finally:
            connection.close()
        if len(raw) > MAX_RELAY_RESPONSE_BYTES:
            raise RelayError("La respuesta del relay es demasiado grande.")
        if response.status == 204:
            return None
        if not 200 <= response.status < 300:
            raise RelayError("El relay rechazó la operación.")
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise RelayError("La respuesta del relay no es JSON válido.") from error


class OutboundRelayClient:
    """Worker manual y cancelable; nunca abre un puerto de escucha local."""

    def __init__(
        self,
        transport: RelayTransport,
        gateway: IncomingGateway,
        logger: logging.Logger,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        if not isinstance(transport, RelayTransport):
            raise TypeError("El cliente requiere un transporte HTTPS.")
        if not isinstance(gateway, IncomingGateway):
            raise TypeError("El cliente requiere un gateway remoto.")
        if not 0.05 <= poll_interval_seconds <= 60:
            raise ValueError("El intervalo del relay no es válido.")
        self._transport = transport
        self._gateway = gateway
        self._logger = logger
        self._interval = poll_interval_seconds
        self._state = RelayState.OFFLINE
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> RelayState:
        with self._lock:
            return self._state

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RelayError("El relay ya está activo.")
            self._stop.clear()
            self._state = RelayState.CONNECTING
            self._thread = threading.Thread(
                target=self._run,
                name="desktop-agent-relay",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> bool:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        stopped = thread is None or not thread.is_alive()
        if stopped:
            self._set_state(RelayState.STOPPED)
        return stopped

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                envelope = self._transport.receive()
                self._set_state(RelayState.ONLINE)
                if envelope is not None:
                    response = self._gateway.handle(envelope)
                    if response is not None:
                        self._transport.send(response)
            except RelayError:
                self._set_state(RelayState.ERROR)
                self._logger.warning("Remote relay: status=ERROR reason=transport")
            self._stop.wait(self._interval)

    def _set_state(self, state: RelayState) -> None:
        with self._lock:
            self._state = state
