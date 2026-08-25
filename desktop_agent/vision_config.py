import os
from collections.abc import Mapping

from desktop_agent.provider_config import (
    AI_ENABLED_ENV,
    ProviderConfig,
    ProviderConfigurationError,
    load_provider_config,
)

VISION_ENABLED_ENV = "DESKTOP_AGENT_VISION_ENABLED"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


class VisionConfigurationError(ProviderConfigurationError):
    pass


def load_vision_provider_config(
    environ: Mapping[str, str] | None = None,
) -> ProviderConfig | None:
    """Exige un segundo opt-in antes de permitir que una imagen salga del equipo."""

    source = os.environ if environ is None else environ
    raw = source.get(VISION_ENABLED_ENV)
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if normalized in _FALSE_VALUES:
        return None
    if normalized not in _TRUE_VALUES:
        raise VisionConfigurationError(
            f"Configuración inválida en {VISION_ENABLED_ENV}: use "
            "1/true/yes/on o 0/false/no/off."
        )
    config = load_provider_config(source)
    if not config.enabled:
        raise VisionConfigurationError(
            f"{VISION_ENABLED_ENV} requiere {AI_ENABLED_ENV}=true."
        )
    return config
