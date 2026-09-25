import ctypes
from ctypes import wintypes
from typing import Protocol, runtime_checkable


class SecretProtectionError(RuntimeError):
    """Windows no pudo proteger o recuperar un secreto local."""


@runtime_checkable
class SecretProtector(Protocol):
    def protect(self, plaintext: bytes) -> bytes: ...

    def unprotect(self, protected: bytes) -> bytes: ...


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


_CRYPTPROTECT_UI_FORBIDDEN = 0x1
_DESCRIPTION = "Desktop Agent remote device secret"
_ENTROPY = b"desktop-agent-remote-v1"


def _blob(value: bytes) -> tuple[_DataBlob, object]:
    buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    return _DataBlob(len(value), buffer), buffer


class WindowsDpapiProtector:
    """Protege secretos para el usuario actual mediante Windows DPAPI."""

    def __init__(self) -> None:
        if not hasattr(ctypes, "windll"):
            raise OSError("DPAPI solo está disponible en Windows.")
        self._crypt32 = ctypes.windll.crypt32
        self._kernel32 = ctypes.windll.kernel32

    def protect(self, plaintext: bytes) -> bytes:
        return self._transform(plaintext, protect=True)

    def unprotect(self, protected: bytes) -> bytes:
        return self._transform(protected, protect=False)

    def _transform(self, value: bytes, *, protect: bool) -> bytes:
        if not isinstance(value, bytes) or not value:
            raise SecretProtectionError("El secreto local no es válido.")
        source, source_buffer = _blob(value)
        entropy, entropy_buffer = _blob(_ENTROPY)
        output = _DataBlob()
        description = wintypes.LPWSTR()
        if protect:
            success = self._crypt32.CryptProtectData(
                ctypes.byref(source),
                _DESCRIPTION,
                ctypes.byref(entropy),
                None,
                None,
                _CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(output),
            )
        else:
            success = self._crypt32.CryptUnprotectData(
                ctypes.byref(source),
                ctypes.byref(description),
                ctypes.byref(entropy),
                None,
                None,
                _CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(output),
            )
        del source_buffer, entropy_buffer
        if not success:
            raise SecretProtectionError("DPAPI rechazó el secreto local.")
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            if description:
                self._kernel32.LocalFree(description)
            self._kernel32.LocalFree(output.pbData)
