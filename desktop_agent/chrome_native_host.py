import json
import os
import struct
import sys
import threading
from multiprocessing.connection import Client
from typing import BinaryIO, Sequence

from desktop_agent.browser_bridge import (
    EXTENSION_ID,
    MAX_BRIDGE_MESSAGE_BYTES,
    BrowserBridgeError,
    read_bridge_descriptor,
)


ALLOWED_EXTENSION_ORIGIN = f"chrome-extension://{EXTENSION_ID}/"


def _configure_binary_stdio() -> None:
    if os.name != "nt":
        return
    import msvcrt

    msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
    msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)


def read_native_message(stream: BinaryIO) -> bytes | None:
    header = stream.read(4)
    if header == b"":
        return None
    if len(header) != 4:
        raise BrowserBridgeError("El navegador cortó el encabezado nativo.")
    length = struct.unpack("=I", header)[0]
    if length == 0 or length > MAX_BRIDGE_MESSAGE_BYTES:
        raise BrowserBridgeError("El mensaje nativo excede el límite.")
    payload = stream.read(length)
    if len(payload) != length:
        raise BrowserBridgeError("El navegador cortó el mensaje nativo.")
    try:
        json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise BrowserBridgeError("El mensaje nativo no es JSON válido.") from error
    return payload


def write_native_message(stream: BinaryIO, payload: bytes) -> None:
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_BRIDGE_MESSAGE_BYTES:
        raise BrowserBridgeError("La respuesta nativa excede el límite.")
    stream.write(struct.pack("=I", len(payload)))
    stream.write(payload)
    stream.flush()


def _forward_agent_messages(connection, output: BinaryIO) -> None:
    try:
        while True:
            payload = connection.recv_bytes(MAX_BRIDGE_MESSAGE_BYTES)
            write_native_message(output, payload)
    except (BrowserBridgeError, EOFError, OSError, ValueError):
        try:
            connection.close()
        except Exception:
            pass


def run_native_host(
    arguments: Sequence[str] | None = None,
    input_stream: BinaryIO | None = None,
    output_stream: BinaryIO | None = None,
) -> int:
    args = list(sys.argv[1:] if arguments is None else arguments)
    if not args or args[0] != ALLOWED_EXTENSION_ORIGIN:
        return 2
    selected_input = input_stream or sys.stdin.buffer
    selected_output = output_stream or sys.stdout.buffer
    try:
        pipe_name, authkey = read_bridge_descriptor()
        connection = Client(pipe_name, family="AF_PIPE", authkey=authkey)
    except (BrowserBridgeError, OSError):
        return 3
    writer = threading.Thread(
        target=_forward_agent_messages,
        args=(connection, selected_output),
        name="desktop-agent-native-writer",
        daemon=True,
    )
    writer.start()
    try:
        while True:
            payload = read_native_message(selected_input)
            if payload is None:
                return 0
            connection.send_bytes(payload)
    except (BrowserBridgeError, EOFError, OSError, ValueError):
        return 4
    finally:
        try:
            connection.close()
        except Exception:
            pass


def main() -> int:
    _configure_binary_stdio()
    return run_native_host()


if __name__ == "__main__":
    raise SystemExit(main())
