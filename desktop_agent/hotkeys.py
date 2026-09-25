"""Atajos registrados de Windows; no instala un hook ni observa otras teclas."""

import ctypes
import queue
import threading
from ctypes import wintypes


class GlobalHotkeys:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._events: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self.status = "Atajos: iniciando…"

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="desktop-agent-hotkeys", daemon=True)
            self._thread.start()

    def drain(self) -> tuple[str, ...]:
        values = []
        while True:
            try:
                values.append(self._events.get_nowait())
            except queue.Empty:
                return tuple(values)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(1.0)

    def _run(self) -> None:
        registered = []
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            user32.RegisterHotKey.restype = wintypes.BOOL
            user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.UnregisterHotKey.restype = wintypes.BOOL
            user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
            user32.PeekMessageW.restype = wintypes.BOOL
            # MOD_CONTROL | MOD_ALT | MOD_NOREPEAT. No escucha permanente.
            for identifier, key in ((1, 0x20), (2, 0x1B)):
                if not user32.RegisterHotKey(None, identifier, 0x4003, key):
                    self.status = "Atajos no disponibles: otra aplicación usa esa combinación."
                    return
                registered.append(identifier)
            self.status = "Ctrl+Alt+Espacio: hablar / terminar · Ctrl+Alt+Esc: emergencia"
            message = wintypes.MSG()
            while not self._stop.wait(0.03):
                while user32.PeekMessageW(ctypes.byref(message), None, 0x0312, 0x0312, 1):
                    self._events.put("voice" if message.wParam == 1 else "emergency")
        except (OSError, AttributeError):
            self.status = "Atajos globales no disponibles en esta sesión."
        finally:
            for identifier in registered:
                user32.UnregisterHotKey(None, identifier)
