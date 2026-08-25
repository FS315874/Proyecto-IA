import logging
from collections.abc import Callable, Mapping

from desktop_agent.budgeted_vision_provider import BudgetedVisionProvider
from desktop_agent.openai_vision_provider import OpenAIVisionProvider
from desktop_agent.provider_config import ProviderConfig, ProviderConfigurationError
from desktop_agent.usage_budget import MonthlyUsageLedger
from desktop_agent.vision import VisionProvider, VisionProviderError, VisualInterpreter
from desktop_agent.vision_config import load_vision_provider_config

VisionProviderFactory = Callable[[ProviderConfig], VisionProvider]
VisionBudgetFactory = Callable[[ProviderConfig], MonthlyUsageLedger]
Output = Callable[[str], None]


def _default_budget_factory(config: ProviderConfig) -> MonthlyUsageLedger:
    return MonthlyUsageLedger(config.monthly_budget_usd)


def build_visual_interpreter(
    logger: logging.Logger,
    environ: Mapping[str, str] | None = None,
    provider_factory: VisionProviderFactory = OpenAIVisionProvider,
    budget_factory: VisionBudgetFactory = _default_budget_factory,
    warning_output: Output | None = None,
) -> VisualInterpreter:
    """Construye visión solo con el opt-in visual adicional y presupuesto local."""

    provider: VisionProvider | None = None
    try:
        config = load_vision_provider_config(environ)
        if config is not None:
            provider = BudgetedVisionProvider(
                provider_factory(config), budget_factory(config)
            )
    except ProviderConfigurationError:
        if warning_output is not None:
            warning_output(
                "Configuración visual inválida; no se enviarán imágenes."
            )
    except VisionProviderError:
        if warning_output is not None:
            warning_output(
                "Proveedor visual no disponible; no se enviarán imágenes."
            )
    return VisualInterpreter(provider, logger)
