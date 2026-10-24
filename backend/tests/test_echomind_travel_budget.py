"""Finite full-path envelopes reuse original reservations; no upstream calls."""
from __future__ import annotations

import asyncio
import importlib.util
import math
import unittest

if any(importlib.util.find_spec(name) is None for name in ("jsonschema", "prometheus_client")):
    raise unittest.SkipTest("Travel budget checks require the explicit echomind extra")

from echomind_core.provider import ProviderError, TaskBudget
from echomind_core.travel_budget import LIMITS, TravelBudgetTier, TravelTaskBudget


class TravelBudgetConfigurationTests(unittest.TestCase):
    def test_exact_three_finite_tiers_and_detached_summaries_not_authorization(self):
        self.assertEqual(set(LIMITS), set(TravelBudgetTier))
        self.assertEqual({tier.value for tier in TravelBudgetTier}, {"single", "collaboration", "reference"})
        for tier in TravelBudgetTier:
            budget = TravelTaskBudget(tier)
            self.assertEqual((budget.model_limit, budget.tool_limit, budget.token_limit, budget.seconds), LIMITS[tier])
            self.assertTrue(all(math.isfinite(value) and value > 0 for value in LIMITS[tier]))
            self.assertEqual(budget.counts, (0, 0, 0))
            summary = budget.summary()
            self.assertFalse(summary["real_call_authorization"])
            self.assertTrue(summary["shared"])
            summary["model_limit"] = 100000
            self.assertEqual(budget.model_limit, LIMITS[tier][0])
        with self.assertRaises(TypeError):
            LIMITS[TravelBudgetTier.SINGLE] = (100000, 100000, 100000, float("inf"))

    def test_original_default_and_original_validation_unchanged(self):
        budget = TaskBudget()
        self.assertEqual((budget.model_limit, budget.tool_limit, budget.token_limit, budget.seconds), (3, 4, 6144, 45.0))
        for kwargs in ({"model_limit": 4}, {"tool_limit": 5}, {"token_limit": 6145}, {"seconds": 45.1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ProviderError):
                TaskBudget(**kwargs)

    def test_reservation_algorithms_and_stop_are_original_not_replaced(self):
        for name in ("reserve_model", "reserve_embedding", "reserve_tool", "stop"):
            self.assertIs(getattr(TravelTaskBudget, name), getattr(TaskBudget, name))
        self.assertIs(TravelTaskBudget.remaining_seconds, TaskBudget.remaining_seconds)
        self.assertIs(TravelTaskBudget.counts, TaskBudget.counts)

    def test_only_trusted_finite_enum_no_strings_numbers_or_arbitrary_limits(self):
        for tier in ("single", "reference", 1, None, True, {}, [], object()):
            with self.subTest(tier=tier), self.assertRaises(ProviderError):
                TravelTaskBudget(tier)
        for kwargs in ({"model_limit": 1000}, {"seconds": 1000}, {"token_limit": 1000000}):
            with self.subTest(kwargs=kwargs), self.assertRaises(TypeError):
                TravelTaskBudget(**kwargs)
        class UnreviewedSubclass(TravelTaskBudget):
            pass
        with self.assertRaises(ProviderError):
            UnreviewedSubclass()


class TravelBudgetConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_mixed_model_embedding_competition_same_lock_never_exceeds_tier(self):
        for tier in TravelBudgetTier:
            with self.subTest(tier=tier):
                budget = TravelTaskBudget(tier)
                reservations = [budget.reserve_model(1) if index % 2 else budget.reserve_embedding()
                                for index in range(budget.model_limit * 3)]
                results = await asyncio.gather(*reservations, return_exceptions=True)
                accepted = sum(result is None for result in results)
                self.assertEqual(accepted, budget.model_limit)
                self.assertEqual(budget.counts[0], budget.model_limit)
                self.assertTrue(all(result is None or isinstance(result, ProviderError) and result.code == "task_budget"
                                    for result in results))
                before = budget.counts
                with self.assertRaises(ProviderError):
                    await budget.reserve_embedding()
                self.assertEqual(budget.counts, before)

    async def test_tool_competition_shared_by_child_roles_and_no_new_budget(self):
        for tier in TravelBudgetTier:
            with self.subTest(tier=tier):
                shared = TravelTaskBudget(tier)
                async def child_role():
                    return await shared.reserve_tool()
                results = await asyncio.gather(*(child_role() for _ in range(shared.tool_limit * 4)), return_exceptions=True)
                self.assertEqual(sum(value is None for value in results), shared.tool_limit)
                self.assertEqual(shared.counts, (0, shared.tool_limit, 0))

    async def test_output_tokens_and_attempts_independent_embedding_is_not_free_call(self):
        for tier in TravelBudgetTier:
            with self.subTest(tier=tier):
                budget = TravelTaskBudget(tier)
                successful_llms = budget.token_limit // 2048
                for _ in range(successful_llms):
                    await budget.reserve_model(2048)
                with self.assertRaises(ProviderError):
                    await budget.reserve_model(1)
                self.assertEqual(budget.counts, (successful_llms, 0, successful_llms * 2048))
                for _ in range(budget.model_limit - successful_llms):
                    await budget.reserve_embedding()
                self.assertEqual(budget.counts, (budget.model_limit, 0, budget.token_limit))
                with self.assertRaises(ProviderError):
                    await budget.reserve_embedding()

    async def test_failed_operation_stops_new_work_without_refunding_reservations(self):
        budget = TravelTaskBudget()
        await budget.reserve_model(1024)
        await budget.reserve_embedding()
        await budget.reserve_tool()
        before = budget.counts
        try:
            raise RuntimeError("controlled_failure_after_reservation")
        except RuntimeError:
            budget.stop()
        self.assertEqual(before, (2, 1, 1024))
        for operation in (lambda: budget.reserve_model(1), budget.reserve_embedding, budget.reserve_tool):
            with self.assertRaises(ProviderError) as captured:
                await operation()
            self.assertEqual(captured.exception.code, "task_stopped")
        self.assertEqual(budget.counts, before)

    async def test_cancel_after_reservation_keeps_attempt_occupied_and_stop_latched(self):
        budget = TravelTaskBudget()
        reserved, pending = asyncio.Event(), asyncio.Event()
        async def controlled_send():
            await budget.reserve_embedding()
            reserved.set()
            try:
                await pending.wait()
            finally:
                budget.stop()
        task = asyncio.create_task(controlled_send())
        await reserved.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(budget.counts, (1, 0, 0))
        self.assertTrue(budget.stopped)
        with self.assertRaises(ProviderError):
            await budget.reserve_embedding()
        self.assertEqual(budget.counts, (1, 0, 0))

    async def test_cancel_waiting_for_reservation_lock_does_not_record_unsent_work_or_leak_lock(self):
        budget = TravelTaskBudget()
        await budget._lock.acquire()
        task = asyncio.create_task(budget.reserve_model(64))
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(budget.counts, (0, 0, 0))
        budget._lock.release()
        await budget.reserve_model(64)
        self.assertEqual(budget.counts, (1, 0, 64))

    async def test_shared_deadline_blocks_all_kinds_no_partial_reservations(self):
        budget = TravelTaskBudget(TravelBudgetTier.REFERENCE)
        budget._started -= budget.seconds + 1
        for operation in (lambda: budget.reserve_model(1), budget.reserve_embedding, budget.reserve_tool):
            with self.assertRaises(ProviderError) as captured:
                await operation()
            self.assertEqual(captured.exception.code, "task_deadline")
        self.assertEqual(budget.counts, (0, 0, 0))

    async def test_invalid_per_call_tokens_do_not_use_an_attempt(self):
        budget = TravelTaskBudget()
        for tokens in (0, -1, True, 1.0, "1", 2049, None):
            with self.subTest(tokens=tokens), self.assertRaises(ProviderError):
                await budget.reserve_model(tokens)
        self.assertEqual(budget.counts, (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
