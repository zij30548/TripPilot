"""Original evaluator/one Judge with controlled native wire and real temp quotas."""
from __future__ import annotations

import asyncio
from contextlib import closing
from dataclasses import replace
import importlib.util
import json
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

if any(importlib.util.find_spec(name) is None for name in ("jsonschema", "prometheus_client")):
    raise unittest.SkipTest("Travel runtime evaluation requires the explicit echomind extra")

from core.config import Settings
from core.usage_runtime import MeteredAsyncClient
from echomind_core.embedding import DIMENSIONS, EMBEDDING_MODEL, QwenIntentEmbedding
from echomind_core.execution import ExecutionError, ExecutionGate, bind_execution
from echomind_core.provider import MODEL, TaskBudget, bind_provider
from echomind_core.travel_domain import TravelIntent, TravelIntentRecognizer
from echomind_core.travel_evaluation import (
    JudgeAssessment, ReviewVerdict, TravelBlocker, TravelBusinessReview, TravelIntentCase,
    TravelIntentEvaluator, TravelJudge, assess_reply,
)
from echomind_core.travel_package import TravelDomainPackage
from echomind_core.upstream.core.intent_recognizer import IntentCategory
from echomind_core.upstream.evaluation.evaluator import IntentEvaluator
from tests.test_echomind_travel_bridge import SCOPE
from tests.usage_fixture import UsageFixture


def vector(category):
    result = [0.0] * DIMENSIONS
    result[list(TravelIntent).index(category)] = 1.0
    return result


def native(text, *, finish="stop"):
    return {"model": MODEL, "id": "controlled_eval", "choices": [{"finish_reason": finish,
        "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 17, "completion_tokens": 9}}


def reviewed(**changes):
    return TravelBusinessReview(**({"request_binding_valid": True, "tool_authority_valid": True,
        "citation_identity_valid": True, "evidence_support": ReviewVerdict.PASS,
        "question_coverage": ReviewVerdict.PASS, "conditions_and_scope": ReviewVerdict.PASS,
        "missing_and_state": ReviewVerdict.PASS} | changes))


class TravelEvaluationPureTests(unittest.TestCase):
    def test_cases_are_immutable_finite_and_reject_invalid_history(self):
        case = TravelIntentCase("case-1", "看看安排", TravelIntent.SCHEDULE)
        with self.assertRaises(Exception):
            case.expected_intent = TravelIntent.BUDGET
        for arguments in (("", "x", TravelIntent.SCHEDULE), ("case", "", TravelIntent.SCHEDULE),
                          ("case", "x", "schedule"), ("case", "x", IntentCategory.QUERY)):
            with self.subTest(arguments=arguments), self.assertRaises(ExecutionError):
                TravelIntentCase(*arguments)
        with self.assertRaises(ExecutionError):
            replace(case, history=(("system", "untrusted instructions"),))
        with self.assertRaises(ExecutionError):
            replace(case, history=[("user", "not immutable")])

    def test_high_judge_average_cannot_erase_program_or_semantic_blocker(self):
        judge = JudgeAssessment((1., 1., 1., 1.), False, 1, "end_turn", 1, "0" * 64, "1" * 64)
        for review in (reviewed(request_binding_valid=False), reviewed(tool_authority_valid=False),
                       reviewed(citation_identity_valid=False), reviewed(evidence_support=ReviewVerdict.FAIL),
                       reviewed(question_coverage=ReviewVerdict.FAIL), reviewed(conditions_and_scope=ReviewVerdict.FAIL),
                       reviewed(missing_and_state=ReviewVerdict.FAIL), reviewed(blockers=(TravelBlocker.WRONG_FACT,))):
            with self.subTest(review=review):
                result = assess_reply(judge, review)
                self.assertEqual(result["status"], "blocked")
                self.assertFalse(result["passed"])
                self.assertEqual(result["judge"]["overall"], 1)

    def test_pending_support_unscored_and_quality_failure_stay_distinct(self):
        judge = JudgeAssessment((1., 1., 1., 1.), False, 1, "end_turn", 1, "0" * 64, "1" * 64)
        self.assertEqual(assess_reply(judge, reviewed(evidence_support=ReviewVerdict.PENDING))["status"], "pending")
        unscored = replace(judge, dimensions=(None,) * 4, judge_failed=True)
        self.assertEqual(assess_reply(unscored, reviewed())["status"], "unscored")
        self.assertEqual(assess_reply(replace(judge, dimensions=(.2,) * 4), reviewed())["status"], "quality_failed")
        self.assertTrue(assess_reply(judge, reviewed())["passed"])

    def test_review_is_not_a_truthy_string_or_unbounded_unknown_blocker(self):
        for changes in ({"request_binding_valid": 1}, {"evidence_support": "pass"},
                        {"blockers": ("arbitrary private failure",)},
                        {"blockers": (TravelBlocker.WRONG_FACT,) * 2}):
            with self.subTest(changes=changes), self.assertRaises(ExecutionError):
                reviewed(**changes)

    def test_forged_judge_assessment_cannot_inject_nan_or_implicit_success(self):
        valid = JudgeAssessment((1., 1., 1., 1.), False, 1, "end_turn", 1, "0" * 64, "1" * 64)
        for changes in ({"dimensions": (float("nan"), 1., 1., 1.)}, {"dimensions": (True, 1., 1., 1.)},
                        {"judge_failed": True}, {"model_reservations": 2}, {"attempts": 2},
                        {"stop_reason": "max_tokens"}, {"input_hash": "not frozen"}):
            with self.subTest(changes=changes), self.assertRaises(ExecutionError):
                replace(valid, **changes)


class TravelEvaluationWireTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = UsageFixture()
        self.addCleanup(self.fixture.close)
        self.package = TravelDomainPackage().capture()
        self.settings = Settings(_env_file=None, qwen_api_key="controlled-not-real",
            qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", qwen_model=MODEL)
        self.sent = []
        self.gate = ExecutionGate(SCOPE, lambda: SCOPE, TaskBudget())
        self.path = f"/chat/conversations/{SCOPE.conversation_id}/assistant-turns"

    def recognizer(self):
        embedding = QwenIntentEmbedding(SCOPE.owner_id, "controlled-eval-index")
        return TravelIntentRecognizer(embedding)

    def prepare(self, recognizer):
        embedding = recognizer._embedding
        templates = recognizer.templates
        embedding.load_prepared(templates, [vector(category) for category, examples in templates.items() for _ in examples],
            fingerprint=embedding.template_fingerprint(templates), model=EMBEDDING_MODEL, dimensions=DIMENSIONS)

    async def run_bound(self, operation, responder):
        async def handler(message):
            self.sent.append((message.url.path, json.loads(message.content)))
            result = responder(message)
            if asyncio.iscoroutine(result):
                result = await result
            if isinstance(result, BaseException):
                raise result
            return httpx.Response(200, json=result)

        with self.fixture.bind(self.path):
            async with MeteredAsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
                with bind_execution(self.gate), bind_provider(self.settings, client, self.gate.budget):
                    return await operation()

    def rows(self):
        with closing(sqlite3.connect(self.fixture.store.settings.business_usage_path)) as database:
            return database.execute("SELECT service,state FROM usage_attempts ORDER BY rowid").fetchall()

    def classify_response(self, message):
        if message.url.path.endswith("/embeddings"):
            return {"object": "list", "model": EMBEDDING_MODEL, "data": [{
                "object": "embedding", "index": 0, "embedding": vector(TravelIntent.BUDGET)}]}
        return native(json.dumps({"intent": "budget", "confidence": .95, "reasoning": "controlled only"}))

    async def test_original_intent_metrics_actual_fusion_native_wire_and_labels_not_forwarded(self):
        results, original, calls = [], IntentEvaluator.evaluate, []

        async def observed(instance, cases):
            calls.append(cases)
            return await original(instance, cases)

        for expected in (TravelIntent.BUDGET, TravelIntent.SCHEDULE):
            self.gate = ExecutionGate(SCOPE, lambda: SCOPE, TaskBudget())
            case = TravelIntentCase("frozen-static-case", "核对当前费用", expected,
                                   reference_notes="REFERENCE_EXPECTATION_MUST_NOT_REACH_CLASSIFIER")
            recognizer = self.recognizer()
            evaluator = TravelIntentEvaluator(recognizer)

            async def classify():
                self.prepare(recognizer)
                return await evaluator.evaluate((case,))

            with patch.object(IntentEvaluator, "evaluate", observed):
                results.append(await self.run_bound(classify, self.classify_response))
            self.assertEqual(self.gate.budget.counts, (2, 0, 256))
        self.assertEqual(len(calls), 2)
        self.assertEqual(results[0]["metrics"]["accuracy"], 1)
        self.assertEqual(results[1]["metrics"]["accuracy"], 0)
        self.assertNotEqual(results[0]["input_hash"], results[1]["input_hash"])
        payloads = [body for path, body in self.sent if path.endswith("/chat/completions")]
        self.assertEqual(payloads[0], payloads[1])
        self.assertNotIn("REFERENCE_EXPECTATION_MUST_NOT_REACH_CLASSIFIER", json.dumps(self.sent))
        self.assertNotIn("frozen-static-case", json.dumps(self.sent))
        self.assertEqual(self.rows(), [("qwen_text", "received")] * 4)

    async def test_original_macro_f1_is_used_without_new_metric_implementation(self):
        recognizer = self.recognizer()
        predictions = [TravelIntent.BUDGET, TravelIntent.BUDGET, TravelIntent.SCHEDULE]
        outputs = [SimpleNamespace(intent=value, confidence=.9, reasoning="controlled") for value in predictions]
        cases = tuple(TravelIntentCase(f"case-{index}", f"固定问题{index}", expected) for index, expected in
                      enumerate((TravelIntent.BUDGET, TravelIntent.SCHEDULE, TravelIntent.SCHEDULE)))
        with patch.object(recognizer, "recognize", AsyncMock(side_effect=outputs)):
            report = await TravelIntentEvaluator(recognizer).evaluate(cases)
        self.assertEqual(report["metrics"]["correct"], 2)
        self.assertEqual(report["metrics"]["total"], 3)
        self.assertEqual(report["metrics"]["accuracy"], .6667)
        self.assertEqual(report["metrics"]["macro_f1"], .6667)
        self.assertEqual(self.sent, [])

    async def test_invalid_cases_and_predictions_do_not_become_partial_passes(self):
        recognizer = self.recognizer()
        evaluator = TravelIntentEvaluator(recognizer)
        case = TravelIntentCase("duplicate", "固定问题", TravelIntent.BUDGET)
        for cases in ((), (case, case), ("unknown",)):
            with self.subTest(cases=cases), self.assertRaises(ExecutionError):
                await evaluator.evaluate(cases)
        wrong = SimpleNamespace(intent=IntentCategory.BILLING, confidence=.9, reasoning="controlled")
        with patch.object(recognizer, "recognize", AsyncMock(return_value=wrong)), self.assertRaises(ExecutionError):
            await evaluator.evaluate((case,))
        self.assertEqual(self.sent, [])

    async def test_invalid_freeze_metadata_fails_before_classifier_is_called(self):
        recognizer = self.recognizer()
        case = TravelIntentCase("invalid-reference", "固定问题", TravelIntent.BUDGET,
                                reference_notes="invalid unicode surrogate: \ud800")
        with patch.object(recognizer, "recognize", AsyncMock()) as classify:
            with self.assertRaisesRegex(ExecutionError, "travel_evaluation_invalid"):
                await TravelIntentEvaluator(recognizer).evaluate((case,))
            classify.assert_not_awaited()
        self.assertEqual(self.sent, [])

    async def test_single_judge_injects_travel_stage_and_uses_same_metered_budget(self):
        judge = TravelJudge(self.package)
        scores = {key: value for key, value in zip(("relevance", "accuracy", "completeness", "helpfulness"), (.8, .9, .7, 1.0))}
        result = await self.run_bound(lambda: judge.judge("第二天安排？", "尚无正式草案", "受控资料：无正式草案"),
                                      lambda _: native(json.dumps(scores)))
        self.assertFalse(result.judge_failed)
        self.assertEqual(result.overall, .85)
        self.assertEqual(result.model_reservations, 1)
        self.assertEqual(self.gate.budget.counts, (1, 0, 1024))
        self.assertEqual(len(self.sent), 1)
        body = self.sent[0][1]
        self.assertEqual(body["messages"][0]["content"], self.package.stage_prompt("judge"))
        self.assertFalse(body["enable_thinking"])
        self.assertFalse(body["stream"])
        self.assertEqual(result.as_dict()["upstream_model"], "unknown")
        self.assertEqual(self.rows(), [("qwen_text", "received")])

    async def test_same_judge_object_cannot_be_resent_after_a_result(self):
        judge = TravelJudge(self.package)

        async def twice():
            await judge.judge("固定问题", "固定回复")
            with self.assertRaisesRegex(ExecutionError, "travel_evaluation_already_used"):
                await judge.judge("固定问题", "固定回复")

        await self.run_bound(twice, lambda _: native(
            '{"relevance":1,"accuracy":1,"completeness":1,"helpfulness":1}'))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.rows(), [("qwen_text", "received")])

    async def test_invalid_and_truncated_judge_outputs_stay_unscored_without_repair(self):
        for raw, finish in (("not JSON", "stop"), ('{"relevance":1}', "stop"),
                            ('{"relevance":1,"accuracy":1,"completeness":1,"helpfulness":1}', "length")):
            with self.subTest(raw=raw):
                self.gate = ExecutionGate(SCOPE, lambda: SCOPE, TaskBudget())
                before = len(self.sent)
                result = await self.run_bound(lambda: TravelJudge(self.package).judge("固定问题", "固定回复"),
                                              lambda _: native(raw, finish=finish))
                self.assertTrue(result.judge_failed)
                self.assertIsNone(result.overall)
                self.assertEqual(len(self.sent) - before, 1)
                self.assertEqual(result.attempts, 1)
                self.assertEqual(result.model_reservations, 1)

    async def test_provider_failure_is_one_unscored_attempt_and_no_raw_error_leak(self):
        result = await self.run_bound(lambda: TravelJudge(self.package).judge("固定问题", "固定回复"),
                                      lambda _: httpx.ConnectError("private vendor body"))
        self.assertTrue(result.judge_failed)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.gate.budget.counts, (1, 0, 1024))
        self.assertNotIn("private vendor body", json.dumps(result.as_dict()))
        self.assertEqual(self.rows(), [("qwen_text", "failed")])

    async def test_cancel_after_send_is_not_retried_or_turned_into_zero_score(self):
        dispatched, wait = asyncio.Event(), asyncio.Event()

        async def held(_):
            dispatched.set()
            await wait.wait()
            return native("{}")

        task = asyncio.create_task(self.run_bound(lambda: TravelJudge(self.package).judge("固定问题", "固定回复"), held))
        await dispatched.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.gate.budget.counts, (1, 0, 1024))
        self.assertEqual(self.rows(), [("qwen_text", "cancelled")])


if __name__ == "__main__":
    unittest.main()
