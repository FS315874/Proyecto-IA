from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class RiskLevel(str, Enum):
    """Nivel de riesgo declarado por una acción."""

    SAFE = "SAFE"
    CAUTION = "CAUTION"
    DANGEROUS = "DANGEROUS"


class Intent(str, Enum):
    OPEN_URL = "OPEN_URL"
    OPEN_APPLICATION = "OPEN_APPLICATION"
    BROWSER_NAVIGATION = "BROWSER_NAVIGATION"
    MEDIA_PLAYBACK = "MEDIA_PLAYBACK"
    DESKTOP_INPUT = "DESKTOP_INPUT"
    MODIFY_LOCAL_DATA = "MODIFY_LOCAL_DATA"
    SEND_EXTERNAL_DATA = "SEND_EXTERNAL_DATA"
    SYSTEM_CHANGE = "SYSTEM_CHANGE"


@dataclass(frozen=True)
class Action:
    """Solicitud estructurada que el ejecutor puede procesar."""

    intent: Intent
    tool_name: str
    arguments: Mapping[str, str]
    risk_level: RiskLevel
    requires_confirmation: bool

    def __post_init__(self) -> None:
        # Evita que los argumentos cambien después de validar la acción.
        object.__setattr__(self, "arguments", MappingProxyType(dict(self.arguments)))


@dataclass(frozen=True)
class ToolResult:
    success: bool
    message: str
    error_code: str | None = None
    error_stage: str | None = None
