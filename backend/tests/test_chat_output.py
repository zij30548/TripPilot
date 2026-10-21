"""Controlled transport/capacity/privacy checks; never real Qwen evaluation."""
import asyncio
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from core.config import Settings
from tooling.providers.qwen import MAX_OUTPUT_TOKENS, QwenClient, QwenError, parse_content
from tooling.providers.qwen_chat import (
    MAX_INTENT_CONTENT_BYTES, MAX_INTENT_TOKENS, QwenIntentClient,
    response_diagnostic, validation_diagnostic,
)
from core.schemas.chat import Intent, Selection


def envelope(*, tokens: int = 1500, finish: str = "stop", content: str | None = None) -> dict:
    return {"choices": [{"finish_reason": finish, "message": {"role": "assistant", "content":
        content if content is not None else json.dumps({"requirements": Intent().model_dump(), "sources": [], "issues": []})}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": tokens}}


def settings() -> Settings:
    return Settings(_env_file=None, qwen_api_key="controlled-key-not-a-real-secret",
                    qwen_base_url="https://controlled.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")


class ChatOutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_intent_has_new_capacity_old_parser_and_client_remain_strict(self) -> None:
        observed = []
        raw = envelope(tokens=2048)
        def handler(request):
            observed.append(json.loads(request.content))
            return httpx.Response(200, json=raw)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
            intent_client = QwenIntentClient(settings(), transport)
            parsed, usage = await intent_client.generate({"model": settings().qwen_model, "max_tokens": 2048})
            self.assertEqual(Selection.model_validate(parsed).requirements, Intent())
            self.assertEqual(usage.output_tokens, 2048)
            self.assertEqual(intent_client.diagnostic["finish_reason"], "stop")
            with self.assertRaises(QwenError) as old:
                await QwenClient(settings(), transport).generate({"model": settings().qwen_model, "max_tokens": 1024})
        self.assertEqual(old.exception.code, "truncated")
        self.assertEqual([p["max_tokens"] for p in observed], [2048, 1024])
        self.assertEqual(MAX_OUTPUT_TOKENS, 1024)

    async def test_length_is_rejected_even_if_json_is_complete_no_repair_or_retry(self) -> None:
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json=envelope(tokens=2048, finish="length"))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
            client = QwenIntentClient(settings(), transport)
            with self.assertRaises(QwenError) as caught:
                await client.generate({"max_tokens": 2048})
        self.assertEqual(caught.exception.code, "truncated")
        self.assertEqual(client.diagnostic["json_state"], "valid")
        self.assertEqual(client.diagnostic["outcome"], "truncated")
        self.assertEqual(len(calls), 1)

    async def test_wrong_response_profile_never_sends(self) -> None:
        observed = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: observed.append(request))) as transport:
            client = QwenIntentClient(settings(), transport)
            self.assertEqual(client.diagnostic["outcome"], "not_sent")
            for token_limit in (1024, 4096, None, True, "2048"):
                with self.subTest(limit=token_limit), self.assertRaises(QwenError):
                    await client.generate({"max_tokens": token_limit})
        self.assertEqual(observed, [])
        self.assertEqual(client.diagnostic["outcome"], "not_sent")

    async def test_http_failure_and_output_overflow_are_one_attempt(self) -> None:
        responses = [httpx.Response(401, text="PRIVATE_AUTH_ERROR"),
                     httpx.Response(429, text="PRIVATE_LIMIT_ERROR"),
                     httpx.Response(200, content=b" " * 32769)]
        for response in responses:
            calls = []
            def handler(request):
                calls.append(request)
                return response
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
                client = QwenIntentClient(settings(), transport)
                with self.assertRaises(QwenError):
                    await client.generate({"max_tokens": 2048})
            self.assertEqual(len(calls), 1)
            self.assertNotIn("PRIVATE", json.dumps(client.diagnostic))

    async def test_shared_whole_deadline_and_cancellation_still_drain_transport(self) -> None:
        closed = asyncio.Event()
        class Slow(httpx.AsyncByteStream):
            async def __aiter__(self):
                try:
                    while True:
                        await asyncio.sleep(.002)
                        yield b" "
                finally:
                    closed.set()
        def handler(request):
            return httpx.Response(200, stream=Slow())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
            client = QwenIntentClient(settings(), transport)
            with patch("tooling.providers.qwen.DEADLINE_SECONDS", .02), self.assertRaises(QwenError) as caught:
                await client.generate({"max_tokens": 2048})
            self.assertEqual(caught.exception.code, "timeout")
            self.assertTrue(closed.is_set())
            closed.clear()
            task = asyncio.create_task(client.generate({"max_tokens": 2048}))
            await asyncio.sleep(.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(closed.is_set())
            self.assertEqual(client.diagnostic["outcome"], "cancelled")


class ChatParserAndDiagnosticTests(unittest.TestCase):
    def test_complete_capacity_boundaries_and_unknown_usage(self) -> None:
        def parse(raw):
            return parse_content(raw, max_output_tokens=MAX_INTENT_TOKENS,
                                 max_content_bytes=MAX_INTENT_CONTENT_BYTES)
        self.assertEqual(parse(envelope(tokens=2048))[1].output_tokens, 2048)
        with self.assertRaises(QwenError) as caught:
            parse(envelope(tokens=2049))
        self.assertEqual(caught.exception.code, "truncated")
        raw = envelope()
        raw.pop("usage")
        self.assertIsNone(parse(raw)[1].output_tokens)
        self.assertIsNone(response_diagnostic(raw)["output_tokens"])
        # Exactly 16 KiB of valid JSON is accepted by the content parser, not a
        # proof of Selection validity. Its byte cap is independent of tokens.
        raw = envelope(tokens=10, content='"' + "x" * 16382 + '"')
        self.assertEqual(len(parse(raw)[0]), 16382)
        raw["choices"][0]["message"]["content"] += " "
        with self.assertRaises(QwenError) as caught:
            parse(raw)
        self.assertEqual(caught.exception.code, "structure")

    def test_bad_json_duplicates_tools_refusal_and_reasoning_still_rejected(self) -> None:
        base = envelope(tokens=10)
        cases = []
        for content in ('{"requirements":', '{"x":1,"x":2}', '{"x":NaN}'):
            raw = deepcopy(base)
            raw["choices"][0]["message"]["content"] = content
            cases.append((raw, "structure"))
        for field, value, code in (("tool_calls", [{"function": {"name": "bad"}}], "structure"),
                                   ("reasoning_content", "PRIVATE_THINKING", "structure"),
                                   ("refusal", "PRIVATE_REFUSAL", "refused")):
            raw = deepcopy(base)
            raw["choices"][0]["message"][field] = value
            cases.append((raw, code))
        for raw, code in cases:
            with self.subTest(code=code), self.assertRaises(QwenError) as caught:
                parse_content(raw, max_output_tokens=2048, max_content_bytes=16384)
            self.assertEqual(caught.exception.code, code)

    def test_response_diagnostic_contains_only_counts_enums_and_known_fields(self) -> None:
        private = "PRIVATE_CONTENT_SECRET"
        content = {"requirements": Intent(accommodation_hint=private).model_dump() | {private: private},
                   "sources": [{"field": "accommodation_hint", "quote": private}], "issues": []}
        raw = envelope(content=json.dumps(content))
        raw["id"] = private
        raw["model"] = private
        raw["choices"][0]["message"]["reasoning_content"] = private
        result = response_diagnostic(raw)
        self.assertEqual(result["field_sizes"]["accommodation_hint"]["characters_capped"], len(private))
        self.assertEqual(result["sources_count_capped"], 1)
        self.assertNotIn(private, json.dumps(result))
        self.assertLess(len(json.dumps(result)), 2500)
        raw["choices"][0]["finish_reason"] = private
        self.assertEqual(response_diagnostic(raw)["finish_reason"], "unknown")
        for usage in ({"prompt_tokens": True, "completion_tokens": -1},
                      {"prompt_tokens": 1000001, "completion_tokens": private}):
            raw["usage"] = usage
            self.assertIsNone(response_diagnostic(raw)["output_tokens"])
            self.assertIsNone(response_diagnostic(raw)["input_tokens"])

    def test_invalid_json_or_large_content_is_never_retained_or_repaired(self) -> None:
        for content, state in (("PRIVATE_BROKEN_JSON", "invalid"),
                               ("PRIVATE_TEXT" * 2000, "not_checked_size_limit"),
                               ('"\\ud800"', "valid")):
            result = response_diagnostic(envelope(content=content))
            self.assertEqual(result["json_state"], state)
            self.assertNotIn("PRIVATE", json.dumps(result))
        result = response_diagnostic(envelope(content="\ud800"))
        self.assertEqual(result["json_state"], "invalid")

    def test_validation_paths_and_error_counts_do_not_leak_values_or_unknown_names(self) -> None:
        secret = "PRIVATE_KEY_AND_INPUT"
        raw = {"requirements": {"travelers": "private", "start_date": secret,
            "accommodation_hint": " ", secret: secret},
            "sources": [{"field": secret, "quote": "x" * 301, secret: secret}] * 2,
            "issues": [secret], secret: secret}
        with self.assertRaises(ValidationError) as caught:
            Selection.model_validate(raw)
        result = validation_diagnostic(caught.exception)
        self.assertGreater(result["error_count_capped"], 8)
        self.assertEqual(len(result["issues"]), 8)
        encoded = json.dumps(result)
        self.assertNotIn(secret, encoded)
        self.assertNotIn("input", encoded)
        self.assertIn("$.requirements.travelers", encoded)
        self.assertIn("$.requirements.<unknown>", encoded)
        self.assertLess(len(encoded), 1600)


if __name__ == "__main__":
    unittest.main()
