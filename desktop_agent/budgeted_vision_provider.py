from dataclasses import replace

from desktop_agent.interpretation import ProposalBudgetExceededError
from desktop_agent.usage_budget import MonthlyUsageLedger, UsageLedgerError
from desktop_agent.vision import (
    VisionBudgetExceededError,
    VisionProvider,
    VisionProviderError,
    VisionProviderResult,
    VisionUsageTrackingError,
)


class BudgetedVisionProvider:
    """Aplica el presupuesto mensual existente a una llamada visual explícita."""

    def __init__(
        self,
        provider: VisionProvider,
        ledger: MonthlyUsageLedger,
    ) -> None:
        self._provider = provider
        self._ledger = ledger

    def analyze(self, image_png: bytes) -> VisionProviderResult:
        try:
            reservation_id, _ = self._ledger.reserve()
        except ProposalBudgetExceededError as error:
            monthly = (
                error.provider_result.monthly_usage
                if error.provider_result is not None
                else None
            )
            raise VisionBudgetExceededError(
                "El presupuesto mensual no permite el análisis visual.",
                VisionProviderResult(payload=None, monthly_usage=monthly),
            ) from None
        except UsageLedgerError:
            raise VisionUsageTrackingError(
                "No se pudo verificar el presupuesto visual."
            ) from None

        try:
            result = self._provider.analyze(image_png)
        except VisionProviderError as error:
            original = error.provider_result
            try:
                snapshot = self._ledger.settle(
                    reservation_id,
                    original.usage if original else None,
                    original.estimated_cost_usd if original else None,
                )
            except UsageLedgerError:
                raise VisionUsageTrackingError(
                    "El análisis terminó sin poder actualizar el consumo.",
                    original,
                ) from None
            if original is None:
                original = VisionProviderResult(payload=None)
            error.provider_result = replace(original, monthly_usage=snapshot)
            raise
        except Exception:
            try:
                snapshot = self._ledger.settle(reservation_id, None, None)
            except UsageLedgerError:
                raise VisionUsageTrackingError(
                    "El análisis falló sin poder actualizar el consumo."
                ) from None
            raise VisionProviderError(
                "El proveedor visual falló.",
                VisionProviderResult(payload=None, monthly_usage=snapshot),
            ) from None

        if not isinstance(result, VisionProviderResult):
            try:
                snapshot = self._ledger.settle(reservation_id, None, None)
            except UsageLedgerError:
                raise VisionUsageTrackingError(
                    "El análisis terminó sin poder actualizar el consumo."
                ) from None
            raise VisionProviderError(
                "El proveedor visual devolvió un resultado inválido.",
                VisionProviderResult(payload=None, monthly_usage=snapshot),
            )
        try:
            snapshot = self._ledger.settle(
                reservation_id,
                result.usage,
                result.estimated_cost_usd,
            )
        except UsageLedgerError:
            raise VisionUsageTrackingError(
                "El análisis terminó sin poder actualizar el consumo.", result
            ) from None
        return replace(result, monthly_usage=snapshot)
