import ctypes
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from ctypes import wintypes

from desktop_agent.catalog import SUPPORTED_APPLICATIONS
from desktop_agent.models import ToolResult

ExecutableFinder = Callable[[str], str | None]
PathChecker = Callable[[str], bool]
ProcessStarter = Callable[..., None]
ProcessChecker = Callable[[tuple[str, ...]], bool]
Waiter = Callable[[float], None]

_VERIFICATION_ATTEMPTS = 20
_VERIFICATION_INTERVAL_SECONDS = 0.25
_TH32CS_SNAPPROCESS = 0x00000002
_MAX_PATH = 260


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * _MAX_PATH),
    ]


def _start_process(executable: str, arguments: tuple[str, ...] = ()) -> None:
    # Algunos juegos resuelven sus recursos respecto de su directorio de inicio.
    directory = os.path.dirname(executable) if os.path.isabs(executable) else None
    subprocess.Popen([executable, *arguments], cwd=directory, close_fds=True)


def _snapshot_process_names() -> frozenset[str] | None:
    """Obtiene nombres de procesos mediante Tool Help sin abrir una shell."""

    if os.name != "nt":
        return None

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_snapshot = kernel32.CreateToolhelp32Snapshot
        process_first = kernel32.Process32FirstW
        process_next = kernel32.Process32NextW
        close_handle = kernel32.CloseHandle

        create_snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        create_snapshot.restype = wintypes.HANDLE
        process_first.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W)]
        process_first.restype = wintypes.BOOL
        process_next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W)]
        process_next.restype = wintypes.BOOL
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        snapshot = create_snapshot(_TH32CS_SNAPPROCESS, 0)
        invalid_handle = ctypes.c_void_p(-1).value
        if snapshot == invalid_handle:
            return None

        try:
            entry = _ProcessEntry32W()
            entry.dwSize = ctypes.sizeof(_ProcessEntry32W)
            if not process_first(snapshot, ctypes.byref(entry)):
                return frozenset()

            names = {entry.szExeFile.casefold()}
            while process_next(snapshot, ctypes.byref(entry)):
                names.add(entry.szExeFile.casefold())
            return frozenset(names)
        finally:
            close_handle(snapshot)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _is_any_process_running(process_names: tuple[str, ...]) -> bool:
    """Compara únicamente nombres completos registrados en el catálogo."""

    running = _snapshot_process_names()
    if running is None:
        return False
    return any(name.casefold() in running for name in process_names)


def _wait_until_running(
    process_names: tuple[str, ...],
    *,
    checker: ProcessChecker,
    waiter: Waiter,
) -> bool:
    consecutive = 0
    for attempt in range(_VERIFICATION_ATTEMPTS):
        try:
            consecutive = consecutive + 1 if checker(process_names) else 0
            # Un launcher puede existir un instante y fallar antes de abrir la app.
            if consecutive >= 2:
                return True
        except Exception:
            return False
        if attempt + 1 < _VERIFICATION_ATTEMPTS:
            waiter(_VERIFICATION_INTERVAL_SECONDS)
    return False


def _resolve_executable(
    name: str,
    *,
    finder: ExecutableFinder,
    path_checker: PathChecker,
) -> str | None:
    application = SUPPORTED_APPLICATIONS[name]

    for path_template in application.windows_paths:
        candidate = os.path.expandvars(path_template)
        if path_checker(candidate):
            return candidate

    for executable_name in application.executable_names:
        candidate = finder(executable_name)
        if candidate is not None:
            return candidate

    return None


def open_application(
    name: str,
    *,
    finder: ExecutableFinder = shutil.which,
    path_checker: PathChecker = os.path.isfile,
    starter: ProcessStarter = _start_process,
    process_checker: ProcessChecker = _is_any_process_running,
    waiter: Waiter = time.sleep,
) -> ToolResult:
    """Abre una aplicación permitida sin ejecutar texto arbitrario en una shell."""

    application = SUPPORTED_APPLICATIONS.get(name)
    if application is None:
        return ToolResult(
            success=False,
            message=f"La aplicación '{name}' no está permitida.",
            error_code="action_rejected",
        )

    executable = _resolve_executable(
        name,
        finder=finder,
        path_checker=path_checker,
    )
    if executable is None:
        return ToolResult(
            success=False,
            message=(
                f"No se encontró {application.name}. "
                "Comprobá que esté instalada."
            ),
            error_code="app_missing",
        )

    try:
        if application.launch_arguments:
            # Los argumentos proceden del catálogo; nunca de la orden o del LLM.
            starter(executable, application.launch_arguments)
        else:
            starter(executable)
    except OSError as error:
        code = getattr(error, "winerror", None)
        detail = {
            740: "Windows exige permisos de administrador. Abrila manualmente; el agente no eleva privilegios.",
            5: "Windows denegó el acceso al ejecutable. Revisá los permisos de la instalación.",
            2: "El ejecutable ya no está disponible. Revisá la instalación.",
            193: "Windows no reconoce el archivo como una aplicación ejecutable válida.",
            126: "Falta un componente requerido por la aplicación. Revisá su instalación.",
        }.get(code, "")
        if type(code) is int and not detail:
            detail = f"Windows informó el código {code}; no se modificó la seguridad del equipo."
        return ToolResult(
            success=False,
            message=f"No se pudo iniciar {application.name}." + (f" {detail}" if detail else ""),
            error_code="app_launch_denied" if code in (5, 740) else "app_launch_failed",
        )

    if not _wait_until_running(
        application.process_names,
        checker=process_checker,
        waiter=waiter,
    ):
        launcher_hint = ""
        if name == "league_of_legends":
            try:
                if process_checker(("RiotClientServices.exe",)):
                    launcher_hint = " Riot Client quedó activo: revisá si requiere iniciar sesión, actualizar o confirmar el arranque del juego."
            except Exception:
                pass
        return ToolResult(
            success=False,
            message=(
                f"Se solicitó abrir {application.name}, pero no se pudo comprobar "
                "que el proceso quedara activo." + launcher_hint
            ),
            error_code="app_unverified",
        )

    return ToolResult(
        success=True,
        message=f"Apertura verificada a nivel de proceso: {application.name}. No se comprobó el contenido de la ventana.",
    )
