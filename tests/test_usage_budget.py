import json
import tempfile
import unittest
from pathlib import Path

from desktop_agent.budgeted_provider import BudgetedProposalProvider
from desktop_agent.interpretation import (
    ProposalBudgetExceededError,
    ProposalProviderError,
    ProposalProviderResult,
    ProposalUsage,
    ProposalUsageTrackingError,
)
from desktop_agent.usage_budget import (
    CALL_RESERVATION_USD,
    MonthlyUsageLedger,
    UsageLedgerError,
)


class FakeProvider:
    def __init__(
        self,
        result: ProposalProviderResult | None = None,
        error: ProposalProviderError | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.commands: list[str] = []

    def propose(self, command: str) -> ProposalProviderResult:
        self.commands.append(command)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


class UnexpectedFailingProvider:
    def __init__(self, secret: str) -> None:
        self.secret = secret

    def propose(self, command: str) -> ProposalProviderResult:
        raise RuntimeError(self.secret)


class UsageTestSupport:
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.path = Path(self.temporary_directory.name) / "ai_usage.json"
        self.month = "2026-08"
        self.next_reservation = 0

    def reservation_id(self) -> str:
        self.next_reservation += 1
        return f"reservation-{self.next_reservation}"

    def ledger(self, budget: float = 1.0) -> MonthlyUsageLedger:
        return MonthlyUsageLedger(
            budget,
            path=self.path,
            month_provider=lambda: self.month,
            reservation_id_factory=self.reservation_id,
        )


class MonthlyUsageLedgerTests(UsageTestSupport, unittest.TestCase):

    def test_accumulates_tokens_cost_and_persists_across_instances(self) -> None:
        ledger = self.ledger()
        first_id, reserved = ledger.reserve()

        self.assertEqual(reserved.request_count, 1)
        self.assertEqual(reserved.pending_reservation_count, 1)
        self.assertAlmostEqual(
            reserved.estimated_cost_usd,
            CALL_RESERVATION_USD,
        )

        first = ledger.settle(
            first_id,
            ProposalUsage(
                input_tokens=100,
                output_tokens=20,
                total_tokens=120,
                cached_input_tokens=10,
                cache_write_tokens=5,
            ),
            0.000044,
        )
        second_id, _ = ledger.reserve()
        second = ledger.settle(
            second_id,
            ProposalUsage(50, 10, 60),
            0.000022,
        )

        self.assertEqual(first.total_tokens, 120)
        self.assertEqual(second.request_count, 2)
        self.assertEqual(second.input_tokens, 150)
        self.assertEqual(second.output_tokens, 30)
        self.assertEqual(second.total_tokens, 180)
        self.assertEqual(second.cached_input_tokens, 10)
        self.assertEqual(second.cache_write_tokens, 5)
        self.assertEqual(second.pending_reservation_count, 0)
        self.assertEqual(second.unmetered_request_count, 0)
        self.assertAlmostEqual(second.estimated_cost_usd, 0.000066)
        self.assertAlmostEqual(second.remaining_usd, 0.999934)

        reloaded = self.ledger().current_snapshot()
        self.assertEqual(reloaded, second)

    def test_new_month_starts_at_zero_and_preserves_history(self) -> None:
        ledger = self.ledger()
        reservation_id, _ = ledger.reserve()
        ledger.settle(reservation_id, ProposalUsage(10, 2, 12), 0.0000044)

        self.month = "2026-09"
        september = ledger.current_snapshot()

        self.assertEqual(september.month, "2026-09")
        self.assertEqual(september.request_count, 0)
        self.assertEqual(september.total_tokens, 0)
        self.assertEqual(september.estimated_cost_usd, 0)

        september_id, _ = ledger.reserve()
        ledger.settle(september_id, ProposalUsage(20, 4, 24), 0.0000088)
        persisted = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(set(persisted["months"]), {"2026-08", "2026-09"})

    def test_pending_reservation_survives_restart_and_blocks_overspend(self) -> None:
        ledger = self.ledger(budget=CALL_RESERVATION_USD)
        ledger.reserve()

        reloaded = self.ledger(budget=CALL_RESERVATION_USD)
        snapshot = reloaded.current_snapshot()

        self.assertEqual(snapshot.request_count, 1)
        self.assertEqual(snapshot.pending_reservation_count, 1)
        self.assertEqual(snapshot.remaining_usd, 0)
        with self.assertRaises(ProposalBudgetExceededError):
            reloaded.reserve()

    def test_corrupt_file_fails_closed(self) -> None:
        self.path.write_text("not-json", encoding="utf-8")

        with self.assertRaises(UsageLedgerError):
            self.ledger().current_snapshot()


class BudgetedProposalProviderTests(UsageTestSupport, unittest.TestCase):
    def test_successful_call_exposes_monthly_totals_without_storing_command(self) -> None:
        inner = FakeProvider(
            ProposalProviderResult(
                payload={
                    "schema_version": 1,
                    "intent": "UNSUPPORTED",
                    "target": None,
                },
                usage=ProposalUsage(25, 5, 30),
                estimated_cost_usd=0.000011,
            )
        )
        provider = BudgetedProposalProvider(inner, self.ledger())

        result = provider.propose("orden privada de prueba")

        self.assertEqual(inner.commands, ["orden privada de prueba"])
        self.assertIsNotNone(result.monthly_usage)
        assert result.monthly_usage is not None
        self.assertEqual(result.monthly_usage.total_tokens, 30)
        self.assertAlmostEqual(
            result.monthly_usage.estimated_cost_usd,
            0.000011,
        )
        persisted = self.path.read_text(encoding="utf-8")
        self.assertNotIn("orden privada de prueba", persisted)

    def test_unmetered_failure_is_counted_conservatively(self) -> None:
        inner = FakeProvider(error=ProposalProviderError("simulated"))
        provider = BudgetedProposalProvider(inner, self.ledger())

        with self.assertRaises(ProposalProviderError) as context:
            provider.propose("orden privada de prueba")

        result = context.exception.provider_result
        self.assertIsNotNone(result)
        assert result is not None
        self.assertIsNotNone(result.monthly_usage)
        assert result.monthly_usage is not None
        self.assertEqual(result.monthly_usage.request_count, 1)
        self.assertEqual(result.monthly_usage.unmetered_request_count, 1)
        self.assertEqual(result.monthly_usage.total_tokens, 0)
        self.assertAlmostEqual(
            result.monthly_usage.estimated_cost_usd,
            CALL_RESERVATION_USD,
        )

    def test_budget_blocks_inner_provider_before_call(self) -> None:
        inner = FakeProvider(
            ProposalProviderResult(payload={"unexpected": True})
        )
        provider = BudgetedProposalProvider(
            inner,
            self.ledger(budget=CALL_RESERVATION_USD / 2),
        )

        with self.assertRaises(ProposalBudgetExceededError) as context:
            provider.propose("no debe salir del proceso")

        self.assertEqual(inner.commands, [])
        result = context.exception.provider_result
        self.assertIsNotNone(result)
        assert result is not None
        self.assertIsNotNone(result.monthly_usage)
        self.assertFalse(self.path.exists())

    def test_corrupt_file_blocks_inner_provider(self) -> None:
        self.path.write_text("not-json", encoding="utf-8")
        inner = FakeProvider(ProposalProviderResult(payload={"unexpected": True}))
        provider = BudgetedProposalProvider(inner, self.ledger())

        with self.assertRaises(ProposalUsageTrackingError):
            provider.propose("no debe salir del proceso")

        self.assertEqual(inner.commands, [])

    def test_unexpected_provider_error_is_redacted_and_counted(self) -> None:
        secret = "private-provider-detail"
        provider = BudgetedProposalProvider(
            UnexpectedFailingProvider(secret),
            self.ledger(),
        )

        with self.assertRaises(ProposalProviderError) as context:
            provider.propose("orden privada de prueba")

        self.assertNotIn(secret, str(context.exception))
        result = context.exception.provider_result
        self.assertIsNotNone(result)
        assert result is not None
        self.assertIsNotNone(result.monthly_usage)
        assert result.monthly_usage is not None
        self.assertEqual(result.monthly_usage.unmetered_request_count, 1)
        self.assertAlmostEqual(
            result.monthly_usage.estimated_cost_usd,
            CALL_RESERVATION_USD,
        )


if __name__ == "__main__":
    unittest.main()
