"""Demo INPUT-08 sobre una ventana Win32 propia con datos ficticios."""

import ctypes
import logging
import threading
import time
from ctypes import wintypes

from desktop_agent.input_control import (
    EmergencyStop,
    InputAction,
    InputConfirmation,
    InputController,
    InputKind,
    InputStatus,
)
from desktop_agent.observation import (
    CaptureRegion,
    Observation,
    ObservationLimits,
    ObservationService,
    WindowTarget,
)
from desktop_agent.vision import (
    VisualElement,
    VisualInterpretation,
    VisualRole,
    VisualSource,
    VisualStatus,
)
from desktop_agent.windows_capture import WindowsWindowCaptureBackend
from desktop_agent.windows_input import WindowsEmergencyHotkey, WindowsInputBackend

WINDOW_TITLE = "Desktop Agent INPUT-08 - DATOS FICTICIOS"
EXPECTED_TEXT = "Cliente ficticio 123"

_WM_DESTROY = 0x0002
_WM_CLOSE = 0x0010
_WM_COMMAND = 0x0111
_WS_OVERLAPPEDWINDOW = 0x00CF0000
_WS_VISIBLE = 0x10000000
_WS_CHILD = 0x40000000
_WS_BORDER = 0x00800000
_WS_TABSTOP = 0x00010000
_ES_AUTOHSCROLL = 0x0080
_SW_SHOW = 5
_STATUS_ID = 101
_EDIT_ID = 102
_BUTTON_ID = 103
_LRESULT = ctypes.c_ssize_t

_WNDPROC = ctypes.WINFUNCTYPE(
    _LRESULT,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class _WindowClass(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class NativeDemoWindow:
    def __init__(self) -> None:
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.ready = threading.Event()
        self.error: BaseException | None = None
        self.handle = 0
        self.edit_handle = 0
        self.status_handle = 0
        self._class_name = f"DesktopAgentInputDemo{threading.get_ident()}"
        self._callback = _WNDPROC(self._window_proc)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._configure_signatures()

    def start(self) -> None:
        self._thread.start()
        if not self.ready.wait(5):
            raise RuntimeError("La ventana ficticia no inició a tiempo.")
        if self.error is not None:
            raise RuntimeError("La ventana ficticia no pudo iniciarse.")

    def close(self) -> None:
        if self.handle:
            self.user32.PostMessageW(self.handle, _WM_CLOSE, 0, 0)
        self._thread.join(5)

    def read_text(self, handle: int) -> str:
        length = self.user32.GetWindowTextLengthW(handle)
        buffer = ctypes.create_unicode_buffer(length + 1)
        self.user32.GetWindowTextW(handle, buffer, length + 1)
        return buffer.value

    def _run(self) -> None:
        try:
            instance = self.kernel32.GetModuleHandleW(None)
            window_class = _WindowClass(
                0,
                self._callback,
                0,
                0,
                instance,
                None,
                None,
                self.user32.GetSysColorBrush(15),
                None,
                self._class_name,
            )
            if not self.user32.RegisterClassW(ctypes.byref(window_class)):
                raise OSError("No se pudo registrar la ventana ficticia.")
            self.handle = self._create(
                0,
                self._class_name,
                WINDOW_TITLE,
                _WS_OVERLAPPEDWINDOW | _WS_VISIBLE,
                100,
                100,
                440,
                180,
                0,
                0,
                instance,
            )
            self._create(
                0,
                "STATIC",
                "Todos los valores de esta ventana son ficticios.",
                _WS_CHILD | _WS_VISIBLE,
                20,
                15,
                380,
                22,
                self.handle,
                0,
                instance,
            )
            self.edit_handle = self._create(
                0,
                "EDIT",
                "",
                _WS_CHILD
                | _WS_VISIBLE
                | _WS_BORDER
                | _WS_TABSTOP
                | _ES_AUTOHSCROLL,
                20,
                50,
                240,
                28,
                self.handle,
                _EDIT_ID,
                instance,
            )
            self._create(
                0,
                "BUTTON",
                "Confirmar prueba",
                _WS_CHILD | _WS_VISIBLE | _WS_TABSTOP,
                270,
                50,
                120,
                28,
                self.handle,
                _BUTTON_ID,
                instance,
            )
            self.status_handle = self._create(
                0,
                "STATIC",
                "PENDIENTE",
                _WS_CHILD | _WS_VISIBLE,
                20,
                95,
                370,
                22,
                self.handle,
                _STATUS_ID,
                instance,
            )
            self.user32.ShowWindow(self.handle, _SW_SHOW)
            self.user32.SetForegroundWindow(self.handle)
            self.ready.set()
            message = wintypes.MSG()
            while self.user32.GetMessageW(ctypes.byref(message), 0, 0, 0) > 0:
                self.user32.TranslateMessage(ctypes.byref(message))
                self.user32.DispatchMessageW(ctypes.byref(message))
        except BaseException as error:
            self.error = error
            self.ready.set()

    def _create(
        self,
        extended_style: int,
        class_name: str,
        text: str,
        style: int,
        x: int,
        y: int,
        width: int,
        height: int,
        parent: int,
        control_id: int,
        instance: int,
    ) -> int:
        handle = self.user32.CreateWindowExW(
            extended_style,
            class_name,
            text,
            style,
            x,
            y,
            width,
            height,
            parent,
            control_id,
            instance,
            None,
        )
        if not handle:
            raise OSError("No se pudo crear un control ficticio.")
        return int(handle)

    def _window_proc(
        self,
        handle: int,
        message: int,
        wparam: int,
        lparam: int,
    ) -> int:
        if message == _WM_COMMAND and (wparam & 0xFFFF) == _BUTTON_ID:
            self.user32.SetWindowTextW(self.status_handle, "CLICK_OK")
            return 0
        if message == _WM_CLOSE:
            self.user32.DestroyWindow(handle)
            return 0
        if message == _WM_DESTROY:
            self.user32.PostQuitMessage(0)
            return 0
        return self.user32.DefWindowProcW(handle, message, wparam, lparam)

    def _configure_signatures(self) -> None:
        self.kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        self.user32.RegisterClassW.argtypes = [ctypes.POINTER(_WindowClass)]
        self.user32.RegisterClassW.restype = wintypes.ATOM
        self.user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            ctypes.c_void_p,
        ]
        self.user32.CreateWindowExW.restype = wintypes.HWND
        self.user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.DefWindowProcW.restype = _LRESULT
        self.user32.DestroyWindow.argtypes = [wintypes.HWND]
        self.user32.DestroyWindow.restype = wintypes.BOOL
        self.user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.PostMessageW.restype = wintypes.BOOL
        self.user32.SetWindowTextW.argtypes = [
            wintypes.HWND,
            wintypes.LPCWSTR,
        ]
        self.user32.SetWindowTextW.restype = wintypes.BOOL
        self.user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self.user32.GetWindowTextLengthW.restype = ctypes.c_int
        self.user32.GetWindowTextW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        self.user32.GetWindowTextW.restype = ctypes.c_int
        self.user32.GetSysColorBrush.restype = wintypes.HBRUSH
        self.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.ShowWindow.restype = wintypes.BOOL
        self.user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        self.user32.SetForegroundWindow.restype = wintypes.BOOL
        self.user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self.user32.GetMessageW.restype = wintypes.BOOL
        self.user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self.user32.TranslateMessage.restype = wintypes.BOOL
        self.user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self.user32.DispatchMessageW.restype = _LRESULT


def interpretation_for(
    observation: Observation,
    target: WindowTarget,
    role: VisualRole,
    bounds: CaptureRegion,
) -> tuple[VisualInterpretation, VisualElement]:
    element = VisualElement(
        f"{observation.observation_id}:element-1",
        observation.observation_id,
        target.window_id,
        target.revision,
        role,
        bounds,
        "dato ficticio",
        0.99,
        VisualSource.ACCESSIBILITY_FUSED,
    )
    return (
        VisualInterpretation(VisualStatus.READY, (element,), 0.0, False),
        element,
    )


def execute_confirmed(
    controller: InputController,
    action: InputAction,
) -> object:
    prepared = controller.prepare(action)
    return controller.execute(
        prepared,
        InputConfirmation(action.action_id, prepared.challenge, True),
    )


def main() -> int:
    window = NativeDemoWindow()
    observations: ObservationService | None = None
    hotkey: WindowsEmergencyHotkey | None = None
    stage = "start"
    detail = "none"
    try:
        stage = "window"
        window.start()
        stage = "target"
        capture_backend = WindowsWindowCaptureBackend()
        target = capture_backend.find_window_exact(WINDOW_TITLE)
        identifiers = iter(("obs-input-1", "obs-input-2"))
        observations = ObservationService(
            capture_backend,
            logging.getLogger("input08-observation"),
            limits=ObservationLimits(
                max_width=640,
                max_height=480,
                max_pixels=307_200,
                min_interval_seconds=0.01,
                max_captures=2,
            ),
            id_factory=lambda: next(identifiers),
        )
        emergency = EmergencyStop()
        hotkey = WindowsEmergencyHotkey(emergency)
        hotkey.start()
        if not hotkey.is_armed:
            raise RuntimeError("El atajo de emergencia no quedó activo.")
        controller = InputController(
            observations,
            WindowsInputBackend(),
            logging.getLogger("input08"),
            emergency,
        )

        stage = "first_capture"
        first = observations.capture(target)
        text_interpretation, text_element = interpretation_for(
            first,
            target,
            VisualRole.TEXT_FIELD,
            CaptureRegion(20, 50, 240, 28),
        )
        controller.register_context(target, first, text_interpretation)
        stage = "typing"
        typed = execute_confirmed(
            controller,
            InputAction(
                "input-type-1",
                InputKind.TYPE_TEXT,
                target.window_id,
                target.revision,
                first.observation_id,
                text_element.element_id,
                EXPECTED_TEXT,
            ),
        )
        detail = (
            f"status={typed.status.value},stage={typed.failure_stage},"
            f"message={typed.message}"
        )
        if (
            typed.status is not InputStatus.SUCCEEDED
            or window.read_text(window.edit_handle) != EXPECTED_TEXT
        ):
            raise RuntimeError("La escritura accesible no se verificó.")

        target = typed.current_target
        time.sleep(0.02)
        stage = "second_capture"
        second = observations.capture(target)
        button_interpretation, button_element = interpretation_for(
            second,
            target,
            VisualRole.BUTTON,
            CaptureRegion(270, 50, 120, 28),
        )
        controller.register_context(target, second, button_interpretation)
        stage = "clicking"
        clicked = execute_confirmed(
            controller,
            InputAction(
                "input-click-1",
                InputKind.CLICK_ELEMENT,
                target.window_id,
                target.revision,
                second.observation_id,
                button_element.element_id,
            ),
        )
        detail = (
            f"status={clicked.status.value},stage={clicked.failure_stage},"
            f"message={clicked.message}"
        )
        if (
            clicked.status is not InputStatus.SUCCEEDED
            or window.read_text(window.status_handle) != "CLICK_OK"
        ):
            raise RuntimeError("El clic accesible no se verificó.")
        print(
            "INPUT_MANUAL_OK: escritura y clic accesibles confirmados sobre "
            "ventana ficticia; observaciones invalidadas."
        )
        return 0
    except BaseException as error:
        print(
            f"INPUT_MANUAL_FAILED: stage={stage} type={type(error).__name__} "
            f"detail={detail}"
        )
        return 1
    finally:
        if observations is not None:
            observations.close()
        if hotkey is not None:
            hotkey.close()
        window.close()


if __name__ == "__main__":
    raise SystemExit(main())
