"""Configuración local y credencial protegida; nunca guarda una clave en claro."""

import base64
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from collections.abc import Mapping

from desktop_agent.windows_secrets import SecretProtector, WindowsDpapiProtector


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class AppSettings:
    ai_enabled: bool = False
    voice_enabled: bool = False
    monthly_budget_usd: float = 1.0
    microphone: str | None = None
    auto_send_voice: bool = False
    voice_hotkey: bool = False
    remember_key: bool = False
    api_key: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        for name in ("ai_enabled", "voice_enabled", "auto_send_voice", "voice_hotkey", "remember_key"):
            if type(getattr(self, name)) is not bool:
                raise SettingsError("Las opciones de configuración deben ser booleanas.")
        budget = self.monthly_budget_usd
        if type(budget) not in (int, float) or not math.isfinite(budget) or not 0.01 <= budget <= 1000:
            raise SettingsError("El presupuesto mensual debe estar entre USD 0,01 y USD 1000.")
        if self.microphone is not None and (not isinstance(self.microphone, str) or not 1 <= len(self.microphone) <= 500):
            raise SettingsError("La selección de micrófono no es válida.")
        if self.api_key is not None and (
            not isinstance(self.api_key, str) or not self.api_key.strip()
            or len(self.api_key) > 4096 or any(c.isspace() for c in self.api_key)
        ):
            raise SettingsError("La clave debe ser texto sin espacios. Pegala completa.")

    def environment(self, base: Mapping[str, str]) -> dict[str, str]:
        result = dict(base)
        result["DESKTOP_AGENT_AI_ENABLED"] = str(self.ai_enabled).lower()
        result["DESKTOP_AGENT_VOICE_TRANSCRIPTION_ENABLED"] = str(self.voice_enabled).lower()
        result["DESKTOP_AGENT_AI_MONTHLY_BUDGET_USD"] = str(self.monthly_budget_usd)
        result.pop("OPENAI_API_KEY", None)
        if self.api_key:
            result["OPENAI_API_KEY"] = self.api_key
        return result

    @classmethod
    def from_environment(cls, source: Mapping[str, str]) -> "AppSettings":
        enabled = {"1", "true", "on", "yes"}
        try:
            return cls(
                ai_enabled=source.get("DESKTOP_AGENT_AI_ENABLED", "false").lower() in enabled,
                voice_enabled=source.get("DESKTOP_AGENT_VOICE_TRANSCRIPTION_ENABLED", "false").lower() in enabled,
                monthly_budget_usd=float(source.get("DESKTOP_AGENT_AI_MONTHLY_BUDGET_USD", "1.00")),
                api_key=source.get("OPENAI_API_KEY") or None,
            )
        except (TypeError, ValueError):
            raise SettingsError("La configuración del entorno no es válida.") from None


class AppSettingsStore:
    def __init__(self, path: Path | None = None, protector: SecretProtector | None = None) -> None:
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        self.path = path or base / "DesktopAgent" / "settings.json"
        self._protector = protector

    def _protection(self) -> SecretProtector:
        if self._protector is None:
            self._protector = WindowsDpapiProtector()
        return self._protector

    def load(self, fallback: AppSettings | None = None) -> AppSettings:
        if not self.path.exists():
            return fallback or AppSettings()
        try:
            if self.path.stat().st_size > 32_768:
                raise ValueError()
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            expected = (set(AppSettings.__dataclass_fields__) - {"api_key"}) | {"schema_version", "protected_key"}
            if not isinstance(raw, dict) or set(raw) != expected or type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
                raise ValueError()
            raw.pop("schema_version")
            protected = raw.pop("protected_key")
            key = None
            if protected is not None:
                if not isinstance(protected, str) or raw["remember_key"] is not True:
                    raise ValueError()
                key = self._protection().unprotect(base64.b64decode(protected, validate=True)).decode("utf-8")
            return AppSettings(**raw, api_key=key)
        except Exception:
            raise SettingsError("No se pudo leer la configuración o descifrar la clave de este usuario de Windows.") from None

    def save(self, settings: AppSettings) -> None:
        if not isinstance(settings, AppSettings):
            raise SettingsError("La configuración no es válida.")
        temporary = None
        try:
            raw = asdict(settings)
            key = raw.pop("api_key")
            raw["schema_version"] = 1
            raw["protected_key"] = (
                base64.b64encode(self._protection().protect(key.encode("utf-8"))).decode("ascii")
                if key and settings.remember_key else None
            )
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, prefix="settings-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(raw, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        except Exception:
            raise SettingsError("No se pudo guardar la configuración protegida; se conservó la anterior.") from None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass  # El archivo temporal solo contiene la credencial cifrada.
