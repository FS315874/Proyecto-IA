import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field

AI_ENABLED_ENV = "DESKTOP_AGENT_AI_ENABLED"
AI_TIMEOUT_ENV = "DESKTOP_AGENT_AI_TIMEOUT_SECONDS"
AI_MONTHLY_BUDGET_ENV = "DESKTOP_AGENT_AI_MONTHLY_BUDGET_USD"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"

DEFAULT_PROVIDER_NAME = "openai"
DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_REASONING_EFFORT = "none"
DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_TIMEOUT_SECONDS = 30.0
DEFAULT_MONTHLY_BUDGET_USD = 1.0
MIN_MONTHLY_BUDGET_USD = 0.01
MAX_MONTHLY_BUDGET_USD = 1_000.0

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


class ProviderConfigurationError(ValueError):
    """La configuración del proveedor no permite habilitar la integración."""


class InvalidProviderSettingError(ProviderConfigurationError):
    """Una variable de configuración tiene un valor no permitido."""

    def __init__(self, setting: str, requirement: str) -> None:
        self.setting = setting
        super().__init__(f"Configuración inválida en {setting}: {requirement}.")


class MissingProviderCredentialError(ProviderConfigurationError):
    """La integración está habilitada pero no tiene una credencial."""

    def __init__(self) -> None:
        self.setting = OPENAI_API_KEY_ENV
        super().__init__(
            f"Falta {OPENAI_API_KEY_ENV} para habilitar el proveedor externo."
        )


@dataclass(frozen=True)
class ProviderConfig:
    """Configuración local validada, independiente del SDK del proveedor."""

    enabled: bool
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    monthly_budget_usd: float = DEFAULT_MONTHLY_BUDGET_USD
    _api_key: str | None = field(default=None, repr=False, compare=False)
    provider_name: str = field(default=DEFAULT_PROVIDER_NAME, init=False)
    model: str = field(default=DEFAULT_MODEL, init=False)
    reasoning_effort: str = field(
        default=DEFAULT_REASONING_EFFORT,
        init=False,
    )

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise InvalidProviderSettingError(
                AI_ENABLED_ENV,
                "debe resolverse como un valor booleano",
            )

        if type(self.timeout_seconds) not in (int, float):
            raise InvalidProviderSettingError(
                AI_TIMEOUT_ENV,
                "debe ser un número de segundos",
            )

        timeout = float(self.timeout_seconds)
        if (
            not math.isfinite(timeout)
            or timeout <= 0
            or timeout > MAX_TIMEOUT_SECONDS
        ):
            raise InvalidProviderSettingError(
                AI_TIMEOUT_ENV,
                f"debe ser mayor que 0 y menor o igual que {MAX_TIMEOUT_SECONDS:g}",
            )
        object.__setattr__(self, "timeout_seconds", timeout)

        if type(self.monthly_budget_usd) not in (int, float):
            raise InvalidProviderSettingError(
                AI_MONTHLY_BUDGET_ENV,
                "debe ser un importe numérico en dólares",
            )
        monthly_budget = float(self.monthly_budget_usd)
        if (
            not math.isfinite(monthly_budget)
            or monthly_budget < MIN_MONTHLY_BUDGET_USD
            or monthly_budget > MAX_MONTHLY_BUDGET_USD
        ):
            raise InvalidProviderSettingError(
                AI_MONTHLY_BUDGET_ENV,
                f"debe estar entre {MIN_MONTHLY_BUDGET_USD:g} y "
                f"{MAX_MONTHLY_BUDGET_USD:g}",
            )
        object.__setattr__(self, "monthly_budget_usd", monthly_budget)

        if self.enabled:
            if self._api_key is None:
                raise MissingProviderCredentialError()
            if not isinstance(self._api_key, str):
                raise InvalidProviderSettingError(
                    OPENAI_API_KEY_ENV,
                    "debe ser texto",
                )
            if not self._api_key.strip():
                raise MissingProviderCredentialError()
            if self._api_key != self._api_key.strip():
                raise InvalidProviderSettingError(
                    OPENAI_API_KEY_ENV,
                    "no debe tener espacios al inicio ni al final",
                )
        elif self._api_key is not None:
            raise InvalidProviderSettingError(
                OPENAI_API_KEY_ENV,
                "no debe conservarse cuando la integración está deshabilitada",
            )

    @property
    def api_key(self) -> str | None:
        """Expone la credencial solo al futuro adaptador que la necesite."""

        return self._api_key


def _parse_enabled(raw_value: str | None) -> bool:
    if raw_value is None:
        return False

    normalized = raw_value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise InvalidProviderSettingError(
        AI_ENABLED_ENV,
        "use 1/true/yes/on o 0/false/no/off",
    )


def _parse_timeout(raw_value: str | None) -> float:
    if raw_value is None:
        return DEFAULT_TIMEOUT_SECONDS

    try:
        return float(raw_value.strip())
    except ValueError as error:
        raise InvalidProviderSettingError(
            AI_TIMEOUT_ENV,
            "debe ser un número de segundos",
        ) from error


def _parse_monthly_budget(raw_value: str | None) -> float:
    if raw_value is None:
        return DEFAULT_MONTHLY_BUDGET_USD

    try:
        return float(raw_value.strip())
    except ValueError as error:
        raise InvalidProviderSettingError(
            AI_MONTHLY_BUDGET_ENV,
            "debe ser un importe numérico en dólares",
        ) from error


def _read_api_key(environ: Mapping[str, str]) -> str:
    raw_value = environ.get(OPENAI_API_KEY_ENV)
    if raw_value is None or not raw_value.strip():
        raise MissingProviderCredentialError()
    if raw_value != raw_value.strip():
        raise InvalidProviderSettingError(
            OPENAI_API_KEY_ENV,
            "no debe tener espacios al inicio ni al final",
        )
    return raw_value


def load_provider_config(
    environ: Mapping[str, str] | None = None,
) -> ProviderConfig:
    """Carga el opt-in y los secretos desde un entorno inyectable y validado."""

    source = os.environ if environ is None else environ
    enabled = _parse_enabled(source.get(AI_ENABLED_ENV))
    if not enabled:
        return ProviderConfig(enabled=False)

    return ProviderConfig(
        enabled=True,
        timeout_seconds=_parse_timeout(source.get(AI_TIMEOUT_ENV)),
        monthly_budget_usd=_parse_monthly_budget(
            source.get(AI_MONTHLY_BUDGET_ENV)
        ),
        _api_key=_read_api_key(source),
    )
