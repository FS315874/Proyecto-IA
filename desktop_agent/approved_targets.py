"""Proyectos y documentos elegidos explícitamente por el usuario."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from desktop_agent.catalog import SUPPORTED_APPLICATIONS
from desktop_agent.models import ToolResult
from desktop_agent.tools.applications import _is_any_process_running

MAX_TARGETS = 50
MAX_STORE_BYTES = 32_768
DOCUMENT_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".rst"})


class TargetError(ValueError):
    """Fallo seguro y clasificable del catálogo local."""

    def __init__(self, message: str, code: str = "target_rejected") -> None:
        super().__init__(message)
        self.code = code


class TargetKind(str, Enum):
    PROJECT = "project"
    DOCUMENT = "document"


def normalize_target_name(name: str) -> str:
    if not isinstance(name, str) or name != name.strip() or not 1 <= len(name) <= 80:
        raise TargetError("El nombre debe tener entre 1 y 80 caracteres sin espacios extremos.")
    if any(ord(char) < 32 or ord(char) == 127 or char in "\\/<>:|?*\"" for char in name):
        raise TargetError("El nombre contiene caracteres no permitidos.")
    folded = "".join(
        char for char in unicodedata.normalize("NFKD", name.casefold())
        if not unicodedata.combining(char)
    )
    key = "".join(char for char in folded if char.isalnum())
    if not key:
        raise TargetError("El nombre necesita letras o números reconocibles.")
    return key


@dataclass(frozen=True)
class ApprovedTarget:
    name: str
    kind: TargetKind
    path: Path = field(repr=False)

    def __post_init__(self) -> None:
        normalize_target_name(self.name)
        if not isinstance(self.kind, TargetKind) or not isinstance(self.path, Path) or not self.path.is_absolute():
            raise TargetError("El destino aprobado no es válido.")


def _validate_path(kind: TargetKind, path: Path) -> Path:
    if not isinstance(kind, TargetKind) or not isinstance(path, Path):
        raise TargetError("El destino aprobado no es válido.")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        raise TargetError(
            "El destino no existe o no se puede leer.",
            "target_unavailable",
        ) from None
    if not resolved.is_absolute() or str(resolved).startswith("\\\\"):
        raise TargetError("Sólo se permiten rutas locales absolutas.")
    if kind is TargetKind.PROJECT and not resolved.is_dir():
        raise TargetError("Un proyecto debe ser una carpeta existente.")
    if kind is TargetKind.DOCUMENT and (
        not resolved.is_file() or resolved.suffix.casefold() not in DOCUMENT_SUFFIXES
    ):
        raise TargetError("El documento debe ser un archivo de texto permitido.")
    return resolved


class ApprovedTargetStore:
    """Catálogo acotado, atómico y ajeno al repositorio y a las credenciales."""

    def __init__(self, path: Path | None = None) -> None:
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        self.path = path or base / "DesktopAgent" / "approved_targets.json"

    def list(self) -> tuple[ApprovedTarget, ...]:
        if not self.path.exists():
            return ()
        try:
            if self.path.stat().st_size > MAX_STORE_BYTES:
                raise ValueError()
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or set(raw) != {"schema_version", "targets"} or raw["schema_version"] != 1:
                raise ValueError()
            entries = raw["targets"]
            if not isinstance(entries, list) or len(entries) > MAX_TARGETS:
                raise ValueError()
            targets = []
            seen = set()
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != {"name", "kind", "path"}:
                    raise ValueError()
                name, kind, path = entry["name"], TargetKind(entry["kind"]), entry["path"]
                if not isinstance(path, str) or len(path) > 2048:
                    raise ValueError()
                key = normalize_target_name(name)
                if key in seen:
                    raise ValueError()
                seen.add(key)
                targets.append(ApprovedTarget(name, kind, Path(path)))
            return tuple(targets)
        except (OSError, ValueError, TypeError, UnicodeError):
            raise TargetError(
                "No se pudo leer el catálogo de proyectos; no se abrió nada.",
                "target_catalog_invalid",
            ) from None

    def add(self, name: str, kind: TargetKind, path: Path) -> ApprovedTarget:
        key = normalize_target_name(name)
        resolved = _validate_path(kind, path)
        targets = self.list()
        if len(targets) >= MAX_TARGETS:
            raise TargetError("El catálogo ya tiene 50 destinos.")
        if any(normalize_target_name(item.name) == key for item in targets):
            raise TargetError("Ya existe un destino con ese nombre.")
        target = ApprovedTarget(name, kind, resolved)
        self._save((*targets, target))
        return target

    def remove(self, name: str) -> None:
        key = normalize_target_name(name)
        targets = self.list()
        remaining = tuple(item for item in targets if normalize_target_name(item.name) != key)
        if len(remaining) == len(targets):
            raise TargetError("Ese destino no está registrado.")
        self._save(remaining)

    def resolve(
        self,
        name: str,
        kind: TargetKind | None = None,
    ) -> ApprovedTarget:
        key = normalize_target_name(name)
        if kind is not None and not isinstance(kind, TargetKind):
            raise TargetError("El tipo de destino aprobado no es válido.")
        targets = tuple(
            item for item in self.list()
            if kind is None or item.kind is kind
        )
        exact = [item for item in targets if normalize_target_name(item.name) == key]
        matches = exact or [
            item for item in targets
            if len(key) >= 2 and key in normalize_target_name(item.name)
        ]
        if not matches:
            requested = (
                "proyecto" if kind is TargetKind.PROJECT else
                "documento" if kind is TargetKind.DOCUMENT else
                "proyecto o documento"
            )
            raise TargetError(
                f"No hay un {requested} aprobado con ese nombre. Abrí Mis proyectos para registrarlo.",
                "target_not_registered",
            )
        if len(matches) > 1:
            raise TargetError(
                "Más de un destino coincide; usá el nombre completo.",
                "target_ambiguous",
            )
        return matches[0]

    def _save(self, targets: tuple[ApprovedTarget, ...]) -> None:
        raw = {
            "schema_version": 1,
            "targets": [
                {"name": item.name, "kind": item.kind.value, "path": str(item.path)}
                for item in targets
            ],
        }
        data = json.dumps(raw, ensure_ascii=False, indent=2)
        if len(data.encode("utf-8")) > MAX_STORE_BYTES:
            raise TargetError("El catálogo supera el tamaño permitido.")
        temporary: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix="targets-", suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        except OSError:
            raise TargetError("No se pudo guardar el catálogo; se conservó el anterior.") from None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass


def open_approved_target(
    name: str,
    *,
    kind: str | TargetKind | None = None,
    store: ApprovedTargetStore | None = None,
    finder: Callable[[str], str | None] = shutil.which,
    path_checker: Callable[[str], bool] = os.path.isfile,
    starter: Callable[..., object] = subprocess.Popen,
    process_checker: Callable[[tuple[str, ...]], bool] = _is_any_process_running,
    waiter: Callable[[float], None] = time.sleep,
) -> ToolResult:
    """Abre sólo un destino aprobado como argumento literal de VS Code."""

    try:
        try:
            requested_kind = None if kind is None else TargetKind(kind)
        except (TypeError, ValueError):
            raise TargetError("El tipo de destino aprobado no es válido.") from None
        target = (store or ApprovedTargetStore()).resolve(name, requested_kind)
        current = _validate_path(target.kind, target.path)
        if current != target.path:
            raise TargetError(
                "El destino cambió desde su aprobación; registralo de nuevo.",
                "target_changed",
            )
    except TargetError as error:
        return ToolResult(False, str(error), error.code, "target_resolution")

    application = SUPPORTED_APPLICATIONS["vscode"]
    executable = next(
        (candidate for template in application.windows_paths
         if path_checker(candidate := os.path.expandvars(template))),
        None,
    ) or next(
        (candidate for name in application.executable_names
         if name.casefold().endswith(".exe")
         and (candidate := finder(name))
         and candidate.casefold().endswith(".exe")),
        None,
    )
    if executable is None:
        return ToolResult(False, "No se encontró Visual Studio Code.", "app_missing", "target_open")
    try:
        starter(
            [executable, "--new-window", str(current)],
            cwd=os.path.dirname(executable) or None,
            close_fds=True,
        )
    except OSError:
        return ToolResult(False, "Windows no pudo iniciar Visual Studio Code.", "app_launch_failed", "target_open")
    for attempt in range(12):
        if process_checker(application.process_names):
            return ToolResult(
                True,
                f"Apertura solicitada: {target.name}. VS Code está activo; no se comprobó el contenido de su ventana.",
            )
        if attempt < 11:
            waiter(0.25)
    return ToolResult(
        False,
        "Se solicitó abrir el destino, pero no se pudo comprobar VS Code activo.",
        "app_unverified",
        "target_open",
    )
