import json
import os
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable


BROWSER_PREFERENCES_SCHEMA_VERSION = 1


class BrowserPreferenceError(RuntimeError):
    """La preferencia local de navegador no cumple el contrato."""


class PreferredBrowser(str, Enum):
    SYSTEM_DEFAULT = "system_default"
    CHROME = "chrome"
    OPERA_GX = "opera_gx"


@dataclass(frozen=True)
class BrowserPreference:
    browser: PreferredBrowser = PreferredBrowser.SYSTEM_DEFAULT
    use_current_session: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.browser, PreferredBrowser):
            raise BrowserPreferenceError("El navegador preferido no es válido.")
        if type(self.use_current_session) is not bool:
            raise BrowserPreferenceError("La selección de sesión no es válida.")
        if (
            self.browser is PreferredBrowser.SYSTEM_DEFAULT
            and self.use_current_session
        ):
            raise BrowserPreferenceError(
                "La sesión actual requiere elegir Chrome u Opera GX."
            )


@runtime_checkable
class BrowserPreferenceSource(Protocol):
    def load(self) -> BrowserPreference: ...


class StaticBrowserPreferenceSource:
    def __init__(self, preference: BrowserPreference = BrowserPreference()) -> None:
        if not isinstance(preference, BrowserPreference):
            raise TypeError("La preferencia estática no es válida.")
        self._preference = preference

    def load(self) -> BrowserPreference:
        return self._preference


def default_browser_preferences_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / "AppData" / "Local")
    return Path(base) / "DesktopAgent" / "browser_preferences.json"


class BrowserPreferenceStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or default_browser_preferences_path()
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> BrowserPreference:
        with self._lock:
            if not self._path.exists():
                return BrowserPreference()
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise BrowserPreferenceError(
                    "La configuración del navegador está dañada."
                ) from error
            if not isinstance(raw, dict) or set(raw) != {
                "schema_version",
                "browser",
                "use_current_session",
            }:
                raise BrowserPreferenceError(
                    "La configuración del navegador no cumple el esquema."
                )
            if raw["schema_version"] != BROWSER_PREFERENCES_SCHEMA_VERSION:
                raise BrowserPreferenceError(
                    "La configuración del navegador usa otro esquema."
                )
            try:
                return BrowserPreference(
                    PreferredBrowser(raw["browser"]),
                    raw["use_current_session"],
                )
            except (TypeError, ValueError) as error:
                raise BrowserPreferenceError(
                    "La configuración del navegador no es válida."
                ) from error

    def save(self, preference: BrowserPreference) -> None:
        if not isinstance(preference, BrowserPreference):
            raise BrowserPreferenceError("La preferencia no es válida.")
        value = {
            "schema_version": BROWSER_PREFERENCES_SCHEMA_VERSION,
            "browser": preference.browser.value,
            "use_current_session": preference.use_current_session,
        }
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(".tmp")
            try:
                temporary.write_text(
                    json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                temporary.replace(self._path)
            except OSError as error:
                raise BrowserPreferenceError(
                    "No se pudo guardar la preferencia del navegador."
                ) from error
