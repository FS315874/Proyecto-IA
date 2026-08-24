import os
import shutil
import subprocess
from collections.abc import Callable

from desktop_agent.catalog import SUPPORTED_APPLICATIONS
from desktop_agent.models import ToolResult

ExecutableFinder = Callable[[str], str | None]
PathChecker = Callable[[str], bool]
ProcessStarter = Callable[[str], None]


def _start_process(executable: str) -> None:
    subprocess.Popen([executable], close_fds=True)


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
) -> ToolResult:
    """Abre una aplicación permitida sin ejecutar texto arbitrario en una shell."""

    application = SUPPORTED_APPLICATIONS.get(name)
    if application is None:
        return ToolResult(
            success=False,
            message=f"La aplicación '{name}' no está permitida.",
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
        )

    try:
        starter(executable)
    except OSError as error:
        return ToolResult(
            success=False,
            message=f"No se pudo abrir {application.name}: {error}",
        )

    return ToolResult(
        success=True,
        message=f"Aplicación abierta correctamente: {application.name}.",
    )
