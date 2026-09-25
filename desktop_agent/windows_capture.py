import ctypes
import os
import uuid
from ctypes import wintypes

from desktop_agent.observation import (
    CaptureRegion,
    ObservationError,
    RasterFrame,
    WindowTarget,
)

_SRCCOPY = 0x00CC0020
_CAPTUREBLT = 0x40000000
_DIB_RGB_COLORS = 0


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", _BitmapInfoHeader),
        ("bmiColors", wintypes.DWORD * 3),
    ]


class WindowsWindowCaptureBackend:
    """Captura solo ventanas descubiertas por título exacto en este backend."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise ObservationError("La captura nativa solo está disponible en Windows.")
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self._issued: dict[str, tuple[int, int]] = {}
        self._configure_signatures()

    def find_window_exact(self, title: str) -> WindowTarget:
        if not isinstance(title, str) or not title.strip() or len(title) > 256:
            raise ObservationError("El título exacto de ventana no es válido.")
        matches: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )

        def inspect(handle: int, _: int) -> bool:
            if not self._user32.IsWindowVisible(handle):
                return True
            length = self._user32.GetWindowTextLengthW(handle)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            self._user32.GetWindowTextW(handle, buffer, length + 1)
            if buffer.value == title:
                matches.append(int(handle))
            return True

        callback = callback_type(inspect)
        if not self._user32.EnumWindows(callback, 0):
            raise ObservationError("No se pudo enumerar las ventanas locales.")
        if not matches:
            raise ObservationError("No se encontró la ventana exacta.")
        if len(matches) > 1:
            raise ObservationError("El título coincide con más de una ventana.")

        handle = matches[0]
        process_id = wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(
            handle, ctypes.byref(process_id)
        )
        width, height = self._client_size(handle)
        window_id = f"window-{uuid.uuid4().hex}"
        self._issued[window_id] = (handle, int(process_id.value))
        return WindowTarget(
            window_id,
            handle,
            int(process_id.value),
            width,
            height,
        )

    def capture(
        self,
        target: WindowTarget,
        region: CaptureRegion,
    ) -> RasterFrame:
        issued = self._issued.get(target.window_id)
        if issued != (target.native_handle, target.process_id):
            raise ObservationError("La ventana no fue emitida por este backend.")
        handle = target.native_handle
        if not self._user32.IsWindow(handle):
            raise ObservationError("La ventana objetivo ya no existe.")
        if not self._user32.IsWindowVisible(handle):
            raise ObservationError("La ventana objetivo no está visible.")
        if self._user32.IsIconic(handle):
            raise ObservationError("La ventana objetivo está minimizada.")
        process_id = wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(
            handle, ctypes.byref(process_id)
        )
        if int(process_id.value) != target.process_id:
            raise ObservationError("La identidad de la ventana cambió.")
        if self._client_size(handle) != (
            target.client_width,
            target.client_height,
        ):
            raise ObservationError("El tamaño de la ventana cambió.")
        return self._capture_pixels(handle, region)

    def _client_size(self, handle: int) -> tuple[int, int]:
        rect = wintypes.RECT()
        if not self._user32.GetClientRect(handle, ctypes.byref(rect)):
            raise ObservationError("No se pudo leer la ventana objetivo.")
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width <= 0 or height <= 0:
            raise ObservationError("La ventana objetivo no tiene área visible.")
        return width, height

    def _capture_pixels(
        self,
        handle: int,
        region: CaptureRegion,
    ) -> RasterFrame:
        source_dc = self._user32.GetDC(handle)
        if not source_dc:
            raise ObservationError("No se pudo acceder a la ventana objetivo.")
        memory_dc = self._gdi32.CreateCompatibleDC(source_dc)
        bitmap = 0
        previous = 0
        try:
            if not memory_dc:
                raise ObservationError("No se pudo preparar la captura local.")
            bitmap = self._gdi32.CreateCompatibleBitmap(
                source_dc, region.width, region.height
            )
            if not bitmap:
                raise ObservationError("No se pudo preparar la imagen local.")
            previous = self._gdi32.SelectObject(memory_dc, bitmap)
            copied = self._gdi32.BitBlt(
                memory_dc,
                0,
                0,
                region.width,
                region.height,
                source_dc,
                region.x,
                region.y,
                _SRCCOPY | _CAPTUREBLT,
            )
            if not copied:
                raise ObservationError("Windows no pudo copiar la región visual.")
            stride = region.width * 4
            buffer = (ctypes.c_ubyte * (stride * region.height))()
            info = _BitmapInfo()
            info.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
            info.bmiHeader.biWidth = region.width
            info.bmiHeader.biHeight = -region.height
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            info.bmiHeader.biCompression = 0
            lines = self._gdi32.GetDIBits(
                memory_dc,
                bitmap,
                0,
                region.height,
                buffer,
                ctypes.byref(info),
                _DIB_RGB_COLORS,
            )
            if lines != region.height:
                raise ObservationError("Windows devolvió una captura incompleta.")
            return RasterFrame(
                region.width,
                region.height,
                stride,
                bytes(buffer),
            )
        finally:
            if previous:
                self._gdi32.SelectObject(memory_dc, previous)
            if bitmap:
                self._gdi32.DeleteObject(bitmap)
            if memory_dc:
                self._gdi32.DeleteDC(memory_dc)
            self._user32.ReleaseDC(handle, source_dc)

    def _configure_signatures(self) -> None:
        self._user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
        self._user32.EnumWindows.restype = wintypes.BOOL
        self._user32.IsWindow.argtypes = [wintypes.HWND]
        self._user32.IsWindow.restype = wintypes.BOOL
        self._user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self._user32.IsWindowVisible.restype = wintypes.BOOL
        self._user32.IsIconic.argtypes = [wintypes.HWND]
        self._user32.IsIconic.restype = wintypes.BOOL
        self._user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self._user32.GetWindowTextLengthW.restype = ctypes.c_int
        self._user32.GetWindowTextW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        self._user32.GetWindowTextW.restype = ctypes.c_int
        self._user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self._user32.GetClientRect.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
        ]
        self._user32.GetClientRect.restype = wintypes.BOOL
        self._user32.GetDC.argtypes = [wintypes.HWND]
        self._user32.GetDC.restype = wintypes.HDC
        self._user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        self._user32.ReleaseDC.restype = ctypes.c_int
        self._gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        self._gdi32.CreateCompatibleDC.restype = wintypes.HDC
        self._gdi32.CreateCompatibleBitmap.argtypes = [
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
        self._gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        self._gdi32.SelectObject.restype = wintypes.HGDIOBJ
        self._gdi32.BitBlt.argtypes = [
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.DWORD,
        ]
        self._gdi32.BitBlt.restype = wintypes.BOOL
        self._gdi32.GetDIBits.argtypes = [
            wintypes.HDC,
            wintypes.HBITMAP,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.c_void_p,
            ctypes.POINTER(_BitmapInfo),
            wintypes.UINT,
        ]
        self._gdi32.GetDIBits.restype = ctypes.c_int
        self._gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        self._gdi32.DeleteObject.restype = wintypes.BOOL
        self._gdi32.DeleteDC.argtypes = [wintypes.HDC]
        self._gdi32.DeleteDC.restype = wintypes.BOOL
