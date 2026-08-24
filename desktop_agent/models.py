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
