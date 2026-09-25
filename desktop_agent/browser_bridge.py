import base64
import binascii
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
from multiprocessing.connection import Listener
from pathlib import Path
from typing import Protocol, runtime_checkable

from desktop_agent.browser_contract import normalize_search_query
from desktop_agent.browser_preferences import PreferredBrowser
from desktop_agent.windows_secrets import (
    SecretProtectionError,
    SecretProtector,
    WindowsDpapiProtector,
)


BRIDGE_SCHEMA_VERSION = 1
BRIDGE_DESCRIPTOR_SCHEMA_VERSION = 1
MAX_BRIDGE_MESSAGE_BYTES = 32768
DEFAULT_BRIDGE_TIMEOUT_SECONDS = 8.0
EXTENSION_ID = "cdkdbpandminmhcopncaiijfaahcegal"
NATIVE_HOST_NAME = "com.desktop_agent.browser"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
_SAFE_ERROR = re.compile(r"^[a-z0-9_]{1,80}$")


class BrowserBridgeError(RuntimeError):
    """El puente local no pudo autenticar o completar una operación."""


class BrowserKind(str, Enum):
    CHROME = "chrome"
    OPERA_GX = "opera_gx"


class BrowserBridgeState(str, Enum):
    OFFLINE = "offline"
    WAITING = "waiting"
    CONNECTED = "connected"
    ERROR = "error"
    CLOSED = "closed"


class BrowserBridgeOperation(str, Enum):
    OPEN_SITE = "open_site"
    YOUTUBE_SEARCH = "youtube_search"
    YOUTUBE_SELECT_FIRST = "youtube_select_first"
    YOUTUBE_START = "youtube_start"
    YOUTUBE_READ = "youtube_read"
    YOUTUBE_STOP = "youtube_stop"


@dataclass(frozen=True)
class BrowserBridgeSnapshot:
    state: BrowserBridgeState
    browser: BrowserKind | None


@dataclass(frozen=True)
class BrowserBridgeResponse:
    request_id: str
    success: bool
    payload: dict[str, object] | None
    error_code: str | None


@runtime_checkable
class BrowserBridgeClient(Protocol):
    @property
    def snapshot(self) -> BrowserBridgeSnapshot: ...

    def request(
        self,
        operation: BrowserBridgeOperation,
        arguments: dict[str, object],
        expected_browser: BrowserKind,
    ) -> BrowserBridgeResponse: ...

    def open_site(self, site_key: str, browser: PreferredBrowser) -> bool: ...


@runtime_checkable
class ByteConnection(Protocol):
    def send_bytes(self, value: bytes) -> None: ...

    def recv_bytes(self, maxlength: int | None = None) -> bytes: ...

    def close(self) -> None: ...


@runtime_checkable
class PipeListener(Protocol):
    def accept(self) -> ByteConnection: ...

    def close(self) -> None: ...


ListenerFactory = Callable[..., PipeListener]
Clock = Callable[[], float]


def default_bridge_descriptor_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / "DesktopAgent" / "browser_bridge.session"


def browser_kind_for_preference(browser: PreferredBrowser) -> BrowserKind:
    mapping = {
        PreferredBrowser.CHROME: BrowserKind.CHROME,
        PreferredBrowser.OPERA_GX: BrowserKind.OPERA_GX,
    }
    try:
        return mapping[browser]
    except KeyError as error:
        raise BrowserBridgeError(
            "La sesión actual requiere Chrome u Opera GX."
        ) from error


class NativeBrowserBridge:
    """Canal JSON autenticado por named pipe entre agente y host nativo."""

    def __init__(
        self,
        descriptor_path: Path | None = None,
        protector: SecretProtector | None = None,
        listener_factory: ListenerFactory = Listener,
        clock: Clock = time.monotonic,
        timeout_seconds: float = DEFAULT_BRIDGE_TIMEOUT_SECONDS,
    ) -> None:
        if type(timeout_seconds) not in (int, float) or not 0.1 <= float(
            timeout_seconds
        ) <= 30:
            raise ValueError("El timeout del puente no es válido.")
        self._descriptor_path = descriptor_path or default_bridge_descriptor_path()
        self._protector = protector or WindowsDpapiProtector()
        self._listener_factory = listener_factory
        self._clock = clock
        self._timeout = float(timeout_seconds)
        self._listener: PipeListener | None = None
        self._connection: ByteConnection | None = None
        self._state = BrowserBridgeState.OFFLINE
        self._browser: BrowserKind | None = None
        self._responses: dict[str, BrowserBridgeResponse] = {}
        self._pending_requests: set[str] = set()
        self._condition = threading.Condition(threading.RLock())
        self._send_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._closed = threading.Event()
        self._owns_descriptor = False

    @property
    def snapshot(self) -> BrowserBridgeSnapshot:
        with self._condition:
            return BrowserBridgeSnapshot(self._state, self._browser)

    @property
    def descriptor_path(self) -> Path:
        return self._descriptor_path

    def start(self) -> None:
        with self._condition:
            if self._state is not BrowserBridgeState.OFFLINE:
                raise BrowserBridgeError("El puente ya fue iniciado.")
            authkey = secrets.token_bytes(32)
            pipe_name = rf"\\.\pipe\desktop-agent-{uuid.uuid4().hex}"
            listener = self._listener_factory(
                address=pipe_name,
                family="AF_PIPE",
                authkey=authkey,
            )
            self._listener = listener
            try:
                self._write_descriptor(pipe_name, authkey)
                self._owns_descriptor = True
            except Exception:
                self._listener = None
                try:
                    listener.close()
                except Exception:
                    pass
                raise
            self._state = BrowserBridgeState.WAITING
            self._thread = threading.Thread(
                target=self._accept_and_read,
                name="desktop-agent-browser-bridge",
                daemon=True,
            )
            self._thread.start()

    def request(
        self,
        operation: BrowserBridgeOperation,
        arguments: dict[str, object],
        expected_browser: BrowserKind,
    ) -> BrowserBridgeResponse:
        _validate_request(operation, arguments)
        if not isinstance(expected_browser, BrowserKind):
            raise BrowserBridgeError("El navegador esperado no es válido.")
        request_id = f"browser-{uuid.uuid4().hex}"
        message = {
            "schema_version": BRIDGE_SCHEMA_VERSION,
            "type": "request",
            "request_id": request_id,
            "operation": operation.value,
            "arguments": arguments,
        }
        with self._condition:
            if (
                self._state is not BrowserBridgeState.CONNECTED
                or self._browser is not expected_browser
                or self._connection is None
            ):
                raise BrowserBridgeError(
                    "La extensión del navegador elegido no está conectada."
                )
            connection = self._connection
            self._pending_requests.add(request_id)
        encoded = _canonical_json(message)
        if len(encoded) > MAX_BRIDGE_MESSAGE_BYTES:
            with self._condition:
                self._pending_requests.discard(request_id)
            raise BrowserBridgeError("La solicitud del navegador es demasiado grande.")
        try:
            with self._send_lock:
                connection.send_bytes(encoded)
        except (EOFError, OSError) as error:
            with self._condition:
                self._pending_requests.discard(request_id)
            self._mark_error()
            raise BrowserBridgeError(
                "Se perdió la conexión con la extensión."
            ) from error

        deadline = self._clock() + self._timeout
        with self._condition:
            while request_id not in self._responses:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    self._pending_requests.discard(request_id)
                    raise BrowserBridgeError(
                        "La extensión no respondió dentro del límite."
                    )
                self._condition.wait(remaining)
                if (
                    self._state is not BrowserBridgeState.CONNECTED
                    or self._connection is not connection
                ):
                    self._pending_requests.discard(request_id)
                    raise BrowserBridgeError(
                        "La extensión dejó de estar disponible."
                    )
            response = self._responses.pop(request_id)
            self._pending_requests.discard(request_id)
            return response

    def open_site(self, site_key: str, browser: PreferredBrowser) -> bool:
        try:
            response = self.request(
                BrowserBridgeOperation.OPEN_SITE,
                {"site_key": site_key},
                browser_kind_for_preference(browser),
            )
        except BrowserBridgeError:
            return False
        return response.success

    def close(self) -> None:
        self._closed.set()
        with self._condition:
            self._state = BrowserBridgeState.CLOSED
            connection = self._connection
            listener = self._listener
            self._connection = None
            self._listener = None
            self._pending_requests.clear()
            self._responses.clear()
            owns_descriptor = self._owns_descriptor
            self._owns_descriptor = False
            self._condition.notify_all()
        for resource in (connection, listener):
            if resource is None:
                continue
            try:
                resource.close()
            except Exception:
                pass
        if owns_descriptor:
            try:
                self._descriptor_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _accept_and_read(self) -> None:
        listener = self._listener
        if listener is None:
            return
        while not self._closed.is_set():
            try:
                connection = listener.accept()
            except (OSError, ValueError):
                if not self._closed.is_set():
                    self._mark_error()
                return
            try:
                if not isinstance(connection, ByteConnection):
                    raise BrowserBridgeError(
                        "La conexión local no cumple el contrato."
                    )
                with self._condition:
                    if self._closed.is_set():
                        connection.close()
                        return
                    self._connection = connection
                    self._state = BrowserBridgeState.WAITING
                    self._browser = None
                    self._condition.notify_all()
                while not self._closed.is_set():
                    raw = connection.recv_bytes(MAX_BRIDGE_MESSAGE_BYTES)
                    self._receive(_decode_json(raw))
            except (BrowserBridgeError, EOFError, OSError, ValueError):
                pass
            finally:
                try:
                    connection.close()
                except Exception:
                    pass
                with self._condition:
                    if self._connection is connection:
                        self._connection = None
                        self._browser = None
                        if not self._closed.is_set():
                            self._state = BrowserBridgeState.WAITING
                        self._condition.notify_all()

    def _receive(self, message: object) -> None:
        if not isinstance(message, dict):
            raise BrowserBridgeError("La extensión envió un mensaje inválido.")
        message_type = message.get("type")
        if message_type == "hello":
            if set(message) != {"schema_version", "type", "browser"}:
                raise BrowserBridgeError("El saludo de extensión no es válido.")
            if message["schema_version"] != BRIDGE_SCHEMA_VERSION:
                raise BrowserBridgeError("La extensión usa otro esquema.")
            try:
                browser = BrowserKind(message["browser"])
            except (TypeError, ValueError) as error:
                raise BrowserBridgeError(
                    "La extensión informó un navegador inválido."
                ) from error
            with self._condition:
                self._browser = browser
                self._state = BrowserBridgeState.CONNECTED
                self._condition.notify_all()
            return
        response = _parse_response(message)
        with self._condition:
            if response.request_id not in self._pending_requests:
                raise BrowserBridgeError(
                    "La extensión respondió a una solicitud desconocida."
                )
            self._responses[response.request_id] = response
            self._condition.notify_all()

    def _write_descriptor(self, pipe_name: str, authkey: bytes) -> None:
        descriptor = {
            "schema_version": BRIDGE_DESCRIPTOR_SCHEMA_VERSION,
            "pipe_name": pipe_name,
            "authkey": base64.b64encode(authkey).decode("ascii"),
        }
        temporary = self._descriptor_path.with_suffix(".tmp")
        try:
            protected = self._protector.protect(_canonical_json(descriptor))
            self._descriptor_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(base64.b64encode(protected))
            temporary.replace(self._descriptor_path)
        except (OSError, SecretProtectionError) as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise BrowserBridgeError(
                "No se pudo guardar la sesión local del navegador."
            ) from error

    def _mark_error(self) -> None:
        with self._condition:
            self._state = BrowserBridgeState.ERROR
            self._browser = None
            self._condition.notify_all()


def read_bridge_descriptor(
    path: Path | None = None,
    protector: SecretProtector | None = None,
) -> tuple[str, bytes]:
    selected_path = path or default_bridge_descriptor_path()
    selected_protector = protector or WindowsDpapiProtector()
    try:
        protected = base64.b64decode(selected_path.read_bytes(), validate=True)
        raw = selected_protector.unprotect(protected)
        value = json.loads(raw.decode("utf-8"))
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        binascii.Error,
        SecretProtectionError,
    ) as error:
        raise BrowserBridgeError(
            "La sesión local del navegador no está disponible."
        ) from error
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "pipe_name",
        "authkey",
    }:
        raise BrowserBridgeError("El descriptor del navegador no cumple el esquema.")
    if value["schema_version"] != BRIDGE_DESCRIPTOR_SCHEMA_VERSION:
        raise BrowserBridgeError("El descriptor del navegador usa otro esquema.")
    pipe_name = value["pipe_name"]
    if (
        not isinstance(pipe_name, str)
        or not pipe_name.startswith(r"\\.\pipe\desktop-agent-")
        or len(pipe_name) > 100
    ):
        raise BrowserBridgeError("El descriptor contiene un pipe inválido.")
    try:
        authkey = base64.b64decode(value["authkey"], validate=True)
    except (TypeError, ValueError, binascii.Error) as error:
        raise BrowserBridgeError("El descriptor contiene una clave inválida.") from error
    if len(authkey) != 32:
        raise BrowserBridgeError("El descriptor contiene una clave inválida.")
    return pipe_name, authkey


def _validate_request(
    operation: BrowserBridgeOperation,
    arguments: object,
) -> None:
    if not isinstance(operation, BrowserBridgeOperation):
        raise BrowserBridgeError("La operación del navegador no es válida.")
    if not isinstance(arguments, dict):
        raise BrowserBridgeError("Los argumentos del navegador no son válidos.")
    if operation is BrowserBridgeOperation.OPEN_SITE:
        if set(arguments) != {"site_key"} or arguments["site_key"] not in {
            "youtube",
            "google",
            "github",
            "spotify",
        }:
            raise BrowserBridgeError("El sitio del navegador no está permitido.")
    elif operation is BrowserBridgeOperation.YOUTUBE_SEARCH:
        if set(arguments) != {"query"}:
            raise BrowserBridgeError("La búsqueda no cumple el esquema.")
        normalize_search_query(arguments["query"])
    elif arguments:
        raise BrowserBridgeError("La operación no admite argumentos.")


def _parse_response(value: object) -> BrowserBridgeResponse:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "type",
        "request_id",
        "success",
        "payload",
        "error_code",
    }:
        raise BrowserBridgeError("La respuesta de extensión no cumple el esquema.")
    if (
        value["schema_version"] != BRIDGE_SCHEMA_VERSION
        or value["type"] != "response"
    ):
        raise BrowserBridgeError("La respuesta de extensión usa otro esquema.")
    request_id = value["request_id"]
    if not isinstance(request_id, str) or _SAFE_ID.fullmatch(request_id) is None:
        raise BrowserBridgeError("La respuesta de extensión no tiene ID válido.")
    if type(value["success"]) is not bool:
        raise BrowserBridgeError("La respuesta de extensión no tiene estado válido.")
    payload = value["payload"]
    error_code = value["error_code"]
    if value["success"]:
        if not isinstance(payload, dict) or error_code is not None:
            raise BrowserBridgeError("La respuesta exitosa no es válida.")
    elif (
        payload is not None
        or not isinstance(error_code, str)
        or _SAFE_ERROR.fullmatch(error_code) is None
    ):
        raise BrowserBridgeError("La respuesta fallida no es válida.")
    return BrowserBridgeResponse(
        request_id,
        value["success"],
        payload,
        error_code,
    )


def _decode_json(raw: bytes) -> object:
    if not isinstance(raw, bytes) or len(raw) > MAX_BRIDGE_MESSAGE_BYTES:
        raise BrowserBridgeError("El mensaje local es demasiado grande.")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise BrowserBridgeError("El mensaje local no es JSON válido.") from error


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
