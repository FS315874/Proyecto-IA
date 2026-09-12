import json
import os
import shutil
import sysconfig
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from desktop_agent.browser_bridge import EXTENSION_ID, NATIVE_HOST_NAME


REGISTRY_SUBKEY = rf"Software\Google\Chrome\NativeMessagingHosts\{NATIVE_HOST_NAME}"


class BrowserBridgeInstallError(RuntimeError):
    """La instalación local del host nativo no pudo verificarse."""


@runtime_checkable
class RegistryPort(Protocol):
    def set_default(self, subkey: str, value: str) -> None: ...

    def read_default(self, subkey: str) -> str | None: ...

    def delete(self, subkey: str) -> None: ...


class WindowsCurrentUserRegistry:
    def set_default(self, subkey: str, value: str) -> None:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, subkey) as key:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, value)

    def read_default(self, subkey: str) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
                value, value_type = winreg.QueryValueEx(key, None)
        except FileNotFoundError:
            return None
        if value_type != winreg.REG_SZ or not isinstance(value, str):
            raise BrowserBridgeInstallError(
                "El registro del host nativo tiene un valor inválido."
            )
        return value

    def delete(self, subkey: str) -> None:
        import winreg

        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
        except FileNotFoundError:
            pass


@dataclass(frozen=True)
class BrowserBridgeInstallStatus:
    host_executable: Path | None
    manifest_path: Path
    registered_manifest: Path | None
    extension_id: str
    manifest_valid: bool

    @property
    def ready(self) -> bool:
        return (
            self.host_executable is not None
            and self.host_executable.is_file()
            and self.registered_manifest == self.manifest_path
            and self.manifest_path.is_file()
            and self.manifest_valid
        )


def default_native_manifest_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / "DesktopAgent" / "native_host.json"


def find_native_host_executable() -> Path | None:
    found = shutil.which("desktop-agent-browser-host.exe")
    if found is not None:
        return Path(found).resolve()
    scripts = Path(sysconfig.get_path("scripts"))
    candidate = scripts / "desktop-agent-browser-host.exe"
    return candidate.resolve() if candidate.is_file() else None


def install_native_host(
    *,
    host_executable: Path | None = None,
    manifest_path: Path | None = None,
    registry: RegistryPort | None = None,
) -> BrowserBridgeInstallStatus:
    selected_host = host_executable or find_native_host_executable()
    selected_manifest = (manifest_path or default_native_manifest_path()).resolve()
    selected_registry = registry or WindowsCurrentUserRegistry()
    if selected_host is None or not selected_host.is_file():
        raise BrowserBridgeInstallError(
            "No se encontró desktop-agent-browser-host.exe. "
            "Instalá el proyecto antes de registrar el puente."
        )
    selected_host = selected_host.resolve()
    if selected_host.suffix.casefold() != ".exe":
        raise BrowserBridgeInstallError("El host nativo debe ser un ejecutable fijo.")
    manifest = {
        "name": NATIVE_HOST_NAME,
        "description": "Desktop Agent local browser bridge",
        "path": str(selected_host),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{EXTENSION_ID}/"],
    }
    selected_manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary = selected_manifest.with_suffix(".tmp")
    try:
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(selected_manifest)
        selected_registry.set_default(REGISTRY_SUBKEY, str(selected_manifest))
    except OSError as error:
        raise BrowserBridgeInstallError(
            "No se pudo instalar el manifiesto del host nativo."
        ) from error
    return inspect_native_host(
        selected_manifest,
        selected_registry,
        host_executable=selected_host,
    )


def inspect_native_host(
    manifest_path: Path | None = None,
    registry: RegistryPort | None = None,
    *,
    host_executable: Path | None = None,
) -> BrowserBridgeInstallStatus:
    selected_manifest = (manifest_path or default_native_manifest_path()).resolve()
    selected_registry = registry or WindowsCurrentUserRegistry()
    selected_host = host_executable or find_native_host_executable()
    if selected_host is not None:
        selected_host = selected_host.resolve()
    registered = selected_registry.read_default(REGISTRY_SUBKEY)
    manifest_valid = False
    if selected_manifest.is_file() and selected_host is not None:
        try:
            value = json.loads(selected_manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            value = None
        manifest_valid = value == {
            "name": NATIVE_HOST_NAME,
            "description": "Desktop Agent local browser bridge",
            "path": str(selected_host),
            "type": "stdio",
            "allowed_origins": [f"chrome-extension://{EXTENSION_ID}/"],
        }
    return BrowserBridgeInstallStatus(
        selected_host,
        selected_manifest.resolve(),
        Path(registered).resolve() if registered is not None else None,
        EXTENSION_ID,
        manifest_valid,
    )


def uninstall_native_host(
    manifest_path: Path | None = None,
    registry: RegistryPort | None = None,
) -> None:
    selected_manifest = (manifest_path or default_native_manifest_path()).resolve()
    selected_registry = registry or WindowsCurrentUserRegistry()
    selected_registry.delete(REGISTRY_SUBKEY)
    try:
        selected_manifest.unlink(missing_ok=True)
    except OSError as error:
        raise BrowserBridgeInstallError(
            "No se pudo eliminar el manifiesto del host nativo."
        ) from error
