import tempfile
import unittest
from pathlib import Path

from desktop_agent.budgeted_vision_provider import BudgetedVisionProvider
from desktop_agent.interpretation import ProposalUsage
from desktop_agent.usage_budget import MonthlyUsageLedger
from desktop_agent.vision import (
    VisionBudgetExceededError,
    VisionProviderError,
    VisionProviderResult,
)


class FakeVisionProvider:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls = 0

    def analyze(self, image_png: bytes) -> VisionProviderResult:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class BudgetedVisionProviderTests(unittest.TestCase):
    def ledger(self, path: Path, budget: float = 1) -> MonthlyUsageLedger:
        return MonthlyUsageLedger(
            budget,
            path=path,
            month_provider=lambda: "2026-08",
            reservation_id_factory=lambda: "vision-reservation",
        )

    def test_success_updates_the_existing_monthly_token_and_cost_totals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.json"
            usage = ProposalUsage(100, 20, 120)
            provider = FakeVisionProvider(
                VisionProviderResult({}, usage, 0.000044)
            )

            result = BudgetedVisionProvider(
                provider, self.ledger(path)
            ).analyze(b"png")

            self.assertEqual(provider.calls, 1)
            self.assertEqual(result.monthly_usage.total_tokens, 120)
            self.assertEqual(
                result.monthly_usage.estimated_cost_usd, 0.000044
            )

    def test_budget_blocks_a_second_unmetered_call_before_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.json"
            provider = FakeVisionProvider(VisionProviderResult({}))
            budgeted = BudgetedVisionProvider(
                provider, self.ledger(path, budget=0.01)
            )
            budgeted.analyze(b"first")

            with self.assertRaises(VisionBudgetExceededError):
                budgeted.analyze(b"second")

            self.assertEqual(provider.calls, 1)

    def test_provider_failure_is_settled_conservatively(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.json"
            provider = FakeVisionProvider(VisionProviderError("simulated"))
            ledger = self.ledger(path)

            with self.assertRaises(VisionProviderError) as caught:
                BudgetedVisionProvider(provider, ledger).analyze(b"image")

            self.assertIsNotNone(caught.exception.provider_result)
            self.assertEqual(
                caught.exception.provider_result.monthly_usage.request_count,
                1,
            )


if __name__ == "__main__":
    unittest.main()
