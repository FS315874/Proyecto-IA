from dataclasses import replace

from desktop_agent.interpretation import (
    ProposalProvider,
    ProposalProviderError,
    ProposalProviderResult,
    ProposalUsageTrackingError,
)
from desktop_agent.usage_budget import MonthlyUsageLedger, UsageLedgerError


class BudgetedProposalProvider:
    """Aplica presupuesto y persistencia alrededor de un proveedor externo."""

    def __init__(
        self,
        provider: ProposalProvider,
        ledger: MonthlyUsageLedger,
    ) -> None:
        self._provider = provider
        self._ledger = ledger

    def propose(self, command: str) -> ProposalProviderResult:
        try:
            reservation_id, _ = self._ledger.reserve()
        except UsageLedgerError:
            raise ProposalUsageTrackingError(
                "No se pudo verificar el presupuesto local de IA."
            ) from None

        try:
            provider_result = self._provider.propose(command)
        except ProposalProviderError as error:
            original_result = error.provider_result
            usage = original_result.usage if original_result is not None else None
            cost = (
                original_result.estimated_cost_usd
                if original_result is not None
                else None
            )
            try:
                snapshot = self._ledger.settle(reservation_id, usage, cost)
            except UsageLedgerError:
                raise ProposalUsageTrackingError(
                    "La llamada terminó, pero no se pudo actualizar su consumo.",
                    original_result,
                ) from None

            if original_result is None:
                original_result = ProposalProviderResult(payload=None)
            error.provider_result = replace(
                original_result,
                monthly_usage=snapshot,
            )
            raise
        except Exception:
            try:
                snapshot = self._ledger.settle(reservation_id, None, None)
            except UsageLedgerError:
                raise ProposalUsageTrackingError(
                    "La llamada falló y no se pudo actualizar su consumo."
                ) from None
            raise ProposalProviderError(
                "El proveedor externo no pudo generar una propuesta.",
                ProposalProviderResult(
                    payload=None,
                    monthly_usage=snapshot,
                ),
            ) from None

        if not isinstance(provider_result, ProposalProviderResult):
            try:
                snapshot = self._ledger.settle(reservation_id, None, None)
            except UsageLedgerError:
                raise ProposalUsageTrackingError(
                    "La llamada terminó, pero no se pudo actualizar su consumo."
                ) from None
            raise ProposalProviderError(
                "El proveedor externo devolvió un resultado inválido.",
                ProposalProviderResult(
                    payload=None,
                    monthly_usage=snapshot,
                ),
            )

        try:
            snapshot = self._ledger.settle(
                reservation_id,
                provider_result.usage,
                provider_result.estimated_cost_usd,
            )
        except UsageLedgerError:
            raise ProposalUsageTrackingError(
                "La llamada terminó, pero no se pudo actualizar su consumo.",
                provider_result,
            ) from None

        return replace(provider_result, monthly_usage=snapshot)
