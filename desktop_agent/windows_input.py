import ctypes
import os
import threading
from ctypes import wintypes
from pathlib import PureWindowsPath

from desktop_agent.input_control import (
    AccessibleControl,
    EmergencyStop,
    InputBackend,
    InputWindowState,
)
from desktop_agent.observation import CaptureRegion, WindowTarget
from desktop_agent.vision import VisualElement, VisualRole

_LRESULT = ctypes.c_ssize_t

_GW_OWNER = 4
_GWL_STYLE = -16
_BM_CLICK = 0x00F5
_WM_SETTEXT = 0x000C
_WM_LBUTTONDOWN = 0x0201
_WM_LBUTTONUP = 0x0202
_MK_LBUTTON = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_CWP_SKIPINVISIBLE = 0x0001
_CWP_SKIPDISABLED = 0x0002
_ES_PASSWORD = 0x0020
_BS_TYPEMASK = 0x000F
_BS_CHECKBOX_TYPES = frozenset({2, 3, 5, 6})
_BS_RADIO_TYPES = frozenset({4, 9})
_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012
_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_NOREPEAT = 0x4000
_VK_ESCAPE = 0x1B
_EMERGENCY_HOTKEY_ID = 0xDA08

_BLOCKED_EXECUTABLES = frozenset(
    {
        "cmd.exe",
        "powershell.exe",
        "pwsh.exe",
        "wt.exe",
        "windowsterminal.exe",
        "regedit.exe",
        "credentialuihost.exe",
        "consent.exe",
        "securityhealthsystray.exe",
        "keepass.exe",
        "keepassxc.exe",
    }
)
_BLOCKED_TITLE_FRAGMENTS = (
    "powershell",
    "command prompt",
    "terminal",
    "símbolo del sistema",
    "iniciar sesión",
    "sign in",
    "password",
    "contraseña",
    "windows security",
    "seguridad de windows",
)


class _GuiThreadInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


def _iou(first: CaptureRegion, second: CaptureRegion) -> float:
    left = max(first.x, second.x)
    top = max(first.y, second.y)
    right = min(first.x + first.width, second.x + second.width)
    bottom = min(first.y + first.height, second.y + second.height)
    intersection = max(0, right - left) * max(0, bottom - top)
    if not intersection:
        return 0.0
    union = first.width * first.height + second.width * second.height - intersection
    return intersection / union


class WindowsInputBackend(InputBackend):
    """Input dirigido a una ventana exacta mediante controles y mensajes Win32."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("El backend de input solo está disponible en Windows.")
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_signatures()

    def inspect_window(self, target: WindowTarget) -> InputWindowState:
        handle = target.native_handle
        exists = bool(self._user32.IsWindow(handle))
        if not exists:
            return InputWindowState(False, False, False, False, 0, 0, 0)
        process_id = wintypes.DWORD()
        thread_id = self._user32.GetWindowThreadProcessId(
            handle, ctypes.byref(process_id)
        )
        rect = wintypes.RECT()
        has_rect = bool(self._user32.GetClientRect(handle, ctypes.byref(rect)))
        width = int(rect.right - rect.left) if has_rect else 0
        height = int(rect.bottom - rect.top) if has_rect else 0
        popup = self._user32.GetLastActivePopup(handle)
        unknown_modal = bool(
            popup
            and int(popup) != handle
            and self._user32.IsWindowVisible(popup)
        )
        info = _GuiThreadInfo()
        info.cbSize = ctypes.sizeof(_GuiThreadInfo)
        focused = None
        if thread_id and self._user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
            focused = int(info.hwndFocus) if info.hwndFocus else None
        return InputWindowState(
            exists=True,
            visible=bool(self._user32.IsWindowVisible(handle)),
            minimized=bool(self._user32.IsIconic(handle)),
            foreground=int(self._user32.GetForegroundWindow() or 0) == handle,
            process_id=int(process_id.value),
            client_width=width,
            client_height=height,
            unknown_modal=unknown_modal,
            blocked_context=self._blocked_context(handle, int(process_id.value)),
            focused_control_handle=focused,
        )

    def focus_window(self, target: WindowTarget) -> bool:
        current_thread = int(self._kernel32.GetCurrentThreadId())
        target_thread = int(
            self._user32.GetWindowThreadProcessId(
                target.native_handle, None
            )
        )
        foreground = int(self._user32.GetForegroundWindow() or 0)
        foreground_thread = (
            int(self._user32.GetWindowThreadProcessId(foreground, None))
            if foreground
            else 0
        )
        attached: list[int] = []
        for thread_id in {target_thread, foreground_thread}:
            if thread_id and thread_id != current_thread:
                if not self._user32.AttachThreadInput(
                    current_thread, thread_id, True
                ):
                    for previous in reversed(attached):
                        self._user32.AttachThreadInput(
                            current_thread, previous, False
                        )
                    return False
                attached.append(thread_id)
        try:
            self._user32.ShowWindow(target.native_handle, 9)
            self._user32.BringWindowToTop(target.native_handle)
            self._user32.SetActiveWindow(target.native_handle)
            self._user32.SetForegroundWindow(target.native_handle)
        finally:
            for thread_id in reversed(attached):
                self._user32.AttachThreadInput(
                    current_thread, thread_id, False
                )
        return int(self._user32.GetForegroundWindow() or 0) == target.native_handle

    def resolve_accessible(
        self,
        target: WindowTarget,
        element: VisualElement,
    ) -> AccessibleControl | None:
        candidates = [
            control
            for control in self._enumerate_controls(target)
            if control.role is element.role
        ]
        if not candidates:
            return None
        control = max(candidates, key=lambda item: _iou(item.bounds, element.bounds))
        if _iou(control.bounds, element.bounds) < 0.25:
            return None
        return control

    def click_accessible(
        self,
        target: WindowTarget,
        control: AccessibleControl,
    ) -> bool:
        if not self._valid_child(target, control):
            return False
        if control.role not in {
            VisualRole.BUTTON,
            VisualRole.CHECKBOX,
            VisualRole.RADIO,
        }:
            return False
        self._user32.SendMessageW(control.native_handle, _BM_CLICK, 0, 0)
        return bool(self._user32.IsWindow(control.native_handle))

    def focus_control(
        self,
        target: WindowTarget,
        control: AccessibleControl,
    ) -> bool:
        if not self._valid_child(target, control):
            return False
        current_thread = self._kernel32.GetCurrentThreadId()
        target_thread = self._user32.GetWindowThreadProcessId(
            control.native_handle, None
        )
        attached = False
        if current_thread != target_thread:
            attached = bool(
                self._user32.AttachThreadInput(
                    current_thread, target_thread, True
                )
            )
            if not attached:
                return False
        try:
            self._user32.SetFocus(control.native_handle)
        finally:
            if attached:
                self._user32.AttachThreadInput(
                    current_thread, target_thread, False
                )
        state = self.inspect_window(target)
        return state.focused_control_handle == control.native_handle

    def type_text(
        self,
        target: WindowTarget,
        control: AccessibleControl,
        text: str,
    ) -> bool:
        if not self._valid_child(target, control) or control.is_password:
            return False
        buffer = ctypes.create_unicode_buffer(text)
        pointer = ctypes.cast(buffer, ctypes.c_void_p).value or 0
        result = self._user32.SendMessageW(
            control.native_handle, _WM_SETTEXT, 0, pointer
        )
        return bool(result)

    def verify_control_text(
        self,
        target: WindowTarget,
        control: AccessibleControl,
        text: str,
    ) -> bool:
        if not self._valid_child(target, control) or control.is_password:
            return False
        length = self._user32.GetWindowTextLengthW(control.native_handle)
        if length < 0 or length > 2000:
            return False
        buffer = ctypes.create_unicode_buffer(length + 1)
        self._user32.GetWindowTextW(
            control.native_handle, buffer, length + 1
        )
        return buffer.value == text

    def click_client_point(
        self,
        target: WindowTarget,
        x: int,
        y: int,
    ) -> bool:
        if (
            type(x) is not int
            or type(y) is not int
            or not 0 <= x < target.client_width
            or not 0 <= y < target.client_height
        ):
            return False
        point = wintypes.POINT(x, y)
        child = self._user32.ChildWindowFromPointEx(
            target.native_handle,
            point,
            _CWP_SKIPINVISIBLE | _CWP_SKIPDISABLED,
        )
        destination = int(child) if child else target.native_handle
        mapped = wintypes.POINT(x, y)
        if destination != target.native_handle:
            self._user32.MapWindowPoints(
                target.native_handle,
                destination,
                ctypes.byref(mapped),
                1,
            )
        packed = (mapped.y & 0xFFFF) << 16 | (mapped.x & 0xFFFF)
        self._user32.SendMessageW(destination, _WM_LBUTTONDOWN, _MK_LBUTTON, packed)
        self._user32.SendMessageW(destination, _WM_LBUTTONUP, 0, packed)
        return bool(self._user32.IsWindow(destination))

    def _enumerate_controls(
        self,
        target: WindowTarget,
    ) -> list[AccessibleControl]:
        controls: list[AccessibleControl] = []
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )

        def inspect(handle: int, _: int) -> bool:
            role = self._control_role(handle)
            if role is None or not self._user32.IsWindowVisible(handle):
                return True
            bounds = self._control_bounds(target.native_handle, handle)
            if bounds is None:
                return True
            style = int(self._user32.GetWindowLongPtrW(handle, _GWL_STYLE))
            controls.append(
                AccessibleControl(
                    native_handle=int(handle),
                    role=role,
                    bounds=bounds,
                    enabled=bool(self._user32.IsWindowEnabled(handle)),
                    focusable=role.actionable,
                    is_password=(
                        role is VisualRole.TEXT_FIELD
                        and bool(style & _ES_PASSWORD)
                    ),
                )
            )
            return True

        callback = callback_type(inspect)
        self._user32.EnumChildWindows(target.native_handle, callback, 0)
        return controls

    def _control_role(self, handle: int) -> VisualRole | None:
        buffer = ctypes.create_unicode_buffer(128)
        if not self._user32.GetClassNameW(handle, buffer, len(buffer)):
            return None
        class_name = buffer.value.casefold()
        if class_name == "button":
            style = int(self._user32.GetWindowLongPtrW(handle, _GWL_STYLE))
            button_type = style & _BS_TYPEMASK
            if button_type in _BS_CHECKBOX_TYPES:
                return VisualRole.CHECKBOX
            if button_type in _BS_RADIO_TYPES:
                return VisualRole.RADIO
            return VisualRole.BUTTON
        if class_name == "edit" or "richedit" in class_name:
            return VisualRole.TEXT_FIELD
        if class_name == "syslink":
            return VisualRole.LINK
        return None

    def _control_bounds(
        self,
        parent: int,
        child: int,
    ) -> CaptureRegion | None:
        rect = wintypes.RECT()
        if not self._user32.GetWindowRect(child, ctypes.byref(rect)):
            return None
        points = (wintypes.POINT * 2)(
            wintypes.POINT(rect.left, rect.top),
            wintypes.POINT(rect.right, rect.bottom),
        )
        self._user32.MapWindowPoints(0, parent, points, 2)
        width = int(points[1].x - points[0].x)
        height = int(points[1].y - points[0].y)
        if width <= 0 or height <= 0:
            return None
        return CaptureRegion(int(points[0].x), int(points[0].y), width, height)

    def _valid_child(
        self,
        target: WindowTarget,
        control: AccessibleControl,
    ) -> bool:
        if not self._user32.IsWindow(control.native_handle):
            return False
        process_id = wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(
            control.native_handle, ctypes.byref(process_id)
        )
        return bool(
            int(process_id.value) == target.process_id
            and self._user32.IsChild(
                target.native_handle, control.native_handle
            )
        )

    def _blocked_context(self, handle: int, process_id: int) -> bool:
        executable = self._process_name(process_id)
        if executable is None or executable.casefold() in _BLOCKED_EXECUTABLES:
            return True
        length = self._user32.GetWindowTextLengthW(handle)
        buffer = ctypes.create_unicode_buffer(max(1, min(length + 1, 513)))
        self._user32.GetWindowTextW(handle, buffer, len(buffer))
        title = buffer.value.casefold()
        return any(fragment in title for fragment in _BLOCKED_TITLE_FRAGMENTS)

    def _process_name(self, process_id: int) -> str | None:
        process = self._kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION, False, process_id
        )
        if not process:
            return None
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not self._kernel32.QueryFullProcessImageNameW(
                process, 0, buffer, ctypes.byref(size)
            ):
                return None
            return PureWindowsPath(buffer.value).name
        finally:
            self._kernel32.CloseHandle(process)

    def _configure_signatures(self) -> None:
        self._user32.IsWindow.argtypes = [wintypes.HWND]
        self._user32.IsWindow.restype = wintypes.BOOL
        self._user32.IsChild.argtypes = [wintypes.HWND, wintypes.HWND]
        self._user32.IsChild.restype = wintypes.BOOL
        self._user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self._user32.IsWindowVisible.restype = wintypes.BOOL
        self._user32.IsWindowEnabled.argtypes = [wintypes.HWND]
        self._user32.IsWindowEnabled.restype = wintypes.BOOL
        self._user32.IsIconic.argtypes = [wintypes.HWND]
        self._user32.IsIconic.restype = wintypes.BOOL
        self._user32.GetForegroundWindow.restype = wintypes.HWND
        self._user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        self._user32.SetForegroundWindow.restype = wintypes.BOOL
        self._user32.BringWindowToTop.argtypes = [wintypes.HWND]
        self._user32.BringWindowToTop.restype = wintypes.BOOL
        self._user32.SetActiveWindow.argtypes = [wintypes.HWND]
        self._user32.SetActiveWindow.restype = wintypes.HWND
        self._user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self._user32.ShowWindow.restype = wintypes.BOOL
        self._user32.SetFocus.argtypes = [wintypes.HWND]
        self._user32.SetFocus.restype = wintypes.HWND
        self._user32.AttachThreadInput.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.BOOL,
        ]
        self._user32.AttachThreadInput.restype = wintypes.BOOL
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
        self._user32.GetWindowRect.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
        ]
        self._user32.GetWindowRect.restype = wintypes.BOOL
        self._user32.GetLastActivePopup.argtypes = [wintypes.HWND]
        self._user32.GetLastActivePopup.restype = wintypes.HWND
        self._user32.GetGUIThreadInfo.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(_GuiThreadInfo),
        ]
        self._user32.GetGUIThreadInfo.restype = wintypes.BOOL
        self._user32.EnumChildWindows.argtypes = [
            wintypes.HWND,
            ctypes.c_void_p,
            wintypes.LPARAM,
        ]
        self._user32.EnumChildWindows.restype = wintypes.BOOL
        self._user32.GetClassNameW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        self._user32.GetClassNameW.restype = ctypes.c_int
        self._user32.GetWindowLongPtrW.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
        ]
        self._user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        self._user32.MapWindowPoints.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_void_p,
            wintypes.UINT,
        ]
        self._user32.MapWindowPoints.restype = ctypes.c_int
        self._user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self._user32.GetWindowTextLengthW.restype = ctypes.c_int
        self._user32.GetWindowTextW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        self._user32.GetWindowTextW.restype = ctypes.c_int
        self._user32.SendMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self._user32.SendMessageW.restype = _LRESULT
        self._user32.ChildWindowFromPointEx.argtypes = [
            wintypes.HWND,
            wintypes.POINT,
            wintypes.UINT,
        ]
        self._user32.ChildWindowFromPointEx.restype = wintypes.HWND
        self._kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        self._kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self._kernel32.OpenProcess.restype = wintypes.HANDLE
        self._kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL


class WindowsEmergencyHotkey:
    """Mantiene Ctrl+Alt+Esc registrado y activa el canal de emergencia."""

    def __init__(self, emergency: EmergencyStop) -> None:
        if not isinstance(emergency, EmergencyStop):
            raise TypeError("El canal de emergencia no es válido.")
        if os.name != "nt":
            raise OSError("El atajo de emergencia solo está disponible en Windows.")
        self._emergency = emergency
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread_id = 0
        self._error: OSError | None = None
        self._armed = False
        self._configure_hotkey_signatures()

    @property
    def is_armed(self) -> bool:
        return self._armed and self._thread.is_alive()

    def start(self, timeout: float = 5.0) -> None:
        if self._thread.is_alive() or self._armed:
            raise RuntimeError("El atajo de emergencia ya está iniciado.")
        self._thread.start()
        if not self._ready.wait(timeout):
            raise OSError("El atajo de emergencia no inició a tiempo.")
        if self._error is not None:
            raise self._error

    def close(self, timeout: float = 5.0) -> None:
        if self._thread_id:
            self._user32.PostThreadMessageW(
                self._thread_id, _WM_QUIT, 0, 0
            )
        if self._thread.is_alive():
            self._thread.join(timeout)
        self._armed = False

    def _run(self) -> None:
        self._thread_id = int(self._kernel32.GetCurrentThreadId())
        registered = self._user32.RegisterHotKey(
            None,
            _EMERGENCY_HOTKEY_ID,
            _MOD_CONTROL | _MOD_ALT | _MOD_NOREPEAT,
            _VK_ESCAPE,
        )
        if not registered:
            self._error = OSError(
                "No se pudo registrar Ctrl+Alt+Esc como emergencia."
            )
            self._ready.set()
            return
        self._armed = True
        self._ready.set()
        try:
            message = wintypes.MSG()
            while True:
                result = self._user32.GetMessageW(
                    ctypes.byref(message), 0, 0, 0
                )
                if result <= 0:
                    break
                if (
                    message.message == _WM_HOTKEY
                    and message.wParam == _EMERGENCY_HOTKEY_ID
                ):
                    self._emergency.trigger()
        finally:
            self._user32.UnregisterHotKey(None, _EMERGENCY_HOTKEY_ID)
            self._armed = False

    def _configure_hotkey_signatures(self) -> None:
        self._kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        self._user32.RegisterHotKey.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self._user32.RegisterHotKey.restype = wintypes.BOOL
        self._user32.UnregisterHotKey.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
        ]
        self._user32.UnregisterHotKey.restype = wintypes.BOOL
        self._user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self._user32.GetMessageW.restype = wintypes.BOOL
        self._user32.PostThreadMessageW.argtypes = [
            wintypes.DWORD,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self._user32.PostThreadMessageW.restype = wintypes.BOOL
