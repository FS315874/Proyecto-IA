import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from desktop_agent.provider_config import (
    AI_MONTHLY_BUDGET_ENV,
    DEFAULT_MONTHLY_BUDGET_USD,
    MAX_MONTHLY_BUDGET_USD,
    MIN_MONTHLY_BUDGET_USD,
    OPENAI_API_KEY_ENV,
)


VOICE_TRANSCRIPTION_ENABLED_ENV = "DESKTOP_AGENT_VOICE_TRANSCRIPTION_ENABLED"
VOICE_TRANSCRIPTION_TIMEOUT_ENV = "DESKTOP_AGENT_VOICE_TIMEOUT_SECONDS"
DEFAULT_TRANSCRIPTION_MODEL = "gpt-transcribe"
DEFAULT_TRANSCRIPTION_TIMEOUT_SECONDS = 20.0
MAX_TRANSCRIPTION_TIMEOUT_SECONDS = 60.0

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


class VoiceTranscriptionConfigurationError(ValueError):
    """La transcripción externa no tiene una configuración segura."""


class InvalidVoiceTranscriptionSettingError(
    VoiceTranscriptionConfigurationError
):
    def __init__(self, setting: str, requirement: str) -> None:
        self.setting = setting
        super().__init__(f"Configuración inválida en {setting}: {requirement}.")


class MissingVoiceTranscriptionCredentialError(
    VoiceTranscriptionConfigurationError
):
    def __init__(self) -> None:
        self.setting = OPENAI_API_KEY_ENV
        super().__init__(
            f"Falta {OPENAI_API_KEY_ENV} para habilitar la transcripción de voz."
        )


@dataclass(frozen=True)
class VoiceTranscriptionConfig:
    enabled: bool
    timeout_seconds: float = DEFAULT_TRANSCRIPTION_TIMEOUT_SECONDS
    monthly_budget_usd: float = DEFAULT_MONTHLY_BUDGET_USD
    _api_key: str | None = field(default=None, repr=False, compare=False)
    model: str = field(default=DEFAULT_TRANSCRIPTION_MODEL, init=False)

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise InvalidVoiceTranscriptionSettingError(
                VOICE_TRANSCRIPTION_ENABLED_ENV,
                "debe ser booleano",
            )
        timeout = _validate_number(
            self.timeout_seconds,
            VOICE_TRANSCRIPTION_TIMEOUT_ENV,
            0,
            MAX_TRANSCRIPTION_TIMEOUT_SECONDS,
        )
        budget = _validate_number(
            self.monthly_budget_usd,
            AI_MONTHLY_BUDGET_ENV,
            MIN_MONTHLY_BUDGET_USD,
            MAX_MONTHLY_BUDGET_USD,
            inclusive_minimum=True,
        )
        object.__setattr__(self, "timeout_seconds", timeout)
        object.__setattr__(self, "monthly_budget_usd", budget)

        if self.enabled:
            if self._api_key is None or not isinstance(self._api_key, str):
                raise MissingVoiceTranscriptionCredentialError()
            if not self._api_key.strip():
                raise MissingVoiceTranscriptionCredentialError()
            if self._api_key != self._api_key.strip():
                raise InvalidVoiceTranscriptionSettingError(
                    OPENAI_API_KEY_ENV,
                    "no debe tener espacios al inicio ni al final",
                )
        elif self._api_key is not None:
            raise InvalidVoiceTranscriptionSettingError(
                OPENAI_API_KEY_ENV,
                "no debe conservarse cuando la voz externa está deshabilitada",
            )

    @property
    def api_key(self) -> str | None:
        return self._api_key


def _validate_number(
    value: object,
    setting: str,
    minimum: float,
    maximum: float,
    *,
    inclusive_minimum: bool = False,
) -> float:
    if type(value) not in (int, float):
        raise InvalidVoiceTranscriptionSettingError(setting, "debe ser numérico")
    numeric = float(value)
    minimum_valid = numeric >= minimum if inclusive_minimum else numeric > minimum
    if not math.isfinite(numeric) or not minimum_valid or numeric > maximum:
        relation = "mayor o igual" if inclusive_minimum else "mayor"
        raise InvalidVoiceTranscriptionSettingError(
            setting,
            f"debe ser {relation} que {minimum:g} y menor o igual que {maximum:g}",
        )
    return numeric


def _parse_enabled(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise InvalidVoiceTranscriptionSettingError(
        VOICE_TRANSCRIPTION_ENABLED_ENV,
        "use 1/true/yes/on o 0/false/no/off",
    )


def _parse_float(
    value: str | None,
    default: float,
    setting: str,
) -> float:
    if value is None:
        return default
    try:
        return float(value.strip())
    except ValueError:
        raise InvalidVoiceTranscriptionSettingError(
            setting,
            "debe ser numérico",
        ) from None


def load_voice_transcription_config(
    environ: Mapping[str, str] | None = None,
) -> VoiceTranscriptionConfig:
    source = os.environ if environ is None else environ
    enabled = _parse_enabled(source.get(VOICE_TRANSCRIPTION_ENABLED_ENV))
    if not enabled:
        return VoiceTranscriptionConfig(enabled=False)

    raw_key = source.get(OPENAI_API_KEY_ENV)
    if raw_key is None or not raw_key.strip():
        raise MissingVoiceTranscriptionCredentialError()
    return VoiceTranscriptionConfig(
        enabled=True,
        timeout_seconds=_parse_float(
            source.get(VOICE_TRANSCRIPTION_TIMEOUT_ENV),
            DEFAULT_TRANSCRIPTION_TIMEOUT_SECONDS,
            VOICE_TRANSCRIPTION_TIMEOUT_ENV,
        ),
        monthly_budget_usd=_parse_float(
            source.get(AI_MONTHLY_BUDGET_ENV),
            DEFAULT_MONTHLY_BUDGET_USD,
            AI_MONTHLY_BUDGET_ENV,
        ),
        _api_key=raw_key,
    )
