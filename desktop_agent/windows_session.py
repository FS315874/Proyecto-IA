import os
from enum import Enum
from typing import Protocol, runtime_checkable


class SessionAvailability(str, Enum):
    AVAILABLE = "available"
    LOCKED = "locked"
    UNKNOWN = "unknown"


@runtime_checkable
class WindowsSessionApi(Protocol):
    def open_input_desktop(self) -> int: ...

    def can_switch_desktop(self, handle: int) -> bool: ...

    def close_desktop(self, handle: int) -> bool: ...


class CtypesWindowsSessionApi:
    """Adaptador mínimo para comprobar el escritorio interactivo actual."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("La inspección de sesión requiere Windows.")
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.OpenInputDesktop.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        user32.OpenInputDesktop.restype = wintypes.HANDLE
        user32.SwitchDesktop.argtypes = [wintypes.HANDLE]
        user32.SwitchDesktop.restype = wintypes.BOOL
        user32.CloseDesktop.argtypes = [wintypes.HANDLE]
        user32.CloseDesktop.restype = wintypes.BOOL
        self._user32 = user32

    def open_input_desktop(self) -> int:
        desktop_switch = 0x0100
        return int(self._user32.OpenInputDesktop(0, False, desktop_switch) or 0)

    def can_switch_desktop(self, handle: int) -> bool:
        return bool(self._user32.SwitchDesktop(handle))

    def close_desktop(self, handle: int) -> bool:
        return bool(self._user32.CloseDesktop(handle))


class WindowsSessionMonitor:
    def __init__(self, api: WindowsSessionApi | None = None) -> None:
        selected = api or CtypesWindowsSessionApi()
        if not isinstance(selected, WindowsSessionApi):
            raise TypeError("El monitor de sesión no cumple su contrato.")
        self._api = selected

    def current(self) -> SessionAvailability:
        handle = 0
        try:
            handle = self._api.open_input_desktop()
            if handle == 0:
                return SessionAvailability.LOCKED
            available = self._api.can_switch_desktop(handle)
            closed = self._api.close_desktop(handle)
            handle = 0
            if not closed:
                return SessionAvailability.UNKNOWN
            return (
                SessionAvailability.AVAILABLE
                if available
                else SessionAvailability.LOCKED
            )
        except Exception:
            if handle:
                try:
                    self._api.close_desktop(handle)
                except Exception:
                    pass
            return SessionAvailability.UNKNOWN
