"""All responses are synthetic. Diagnostics must never contain model values/keys."""
import json
import unittest

import httpx

from app.config import Settings
from app.integrations.photo_diagnostics import MAX_ISSUES, record_structure
from app.integrations.qwen import QwenError
from app.integrations.qwen_vision import QwenVisionClient
from test_photo import answer, SELECTION

LOGGER = "app.integrations.photo_diagnostics"
UNTRUSTED = "private-test-value\nAuthorization: secret-placeholder data:image/jpeg;base64,NOTREAL"


class PhotoDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def rejected(self, response: httpx.Response) -> list[dict]:
        calls = 0

        def upstream(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return response

        settings = Settings(_env_file=None, qwen_api_key="test-only",
                            qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
        with self.assertLogs(LOGGER, level="WARNING") as captured:
            async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
                with self.assertRaises(QwenError) as error:
                    await QwenVisionClient(settings, client).recognize(b"controlled-image")
        self.assertEqual(error.exception.code, "structure")
        self.assertEqual(calls, 1)
        self.assertEqual(len(captured.records), 1)
        records = []
        for record in captured.records:
            text = record.getMessage()
            self.assertLess(len(text), 1200)
            self.assertNotIn("private-test-value", text)
            self.assertNotIn("Authorization", text)
            self.assertNotIn("base64", text)
            self.assertNotIn("test-only", text)
            self.assertIsNone(record.exc_info)
            self.assertIsNone(record.stack_info)
            records.append(json.loads(text.split(" ", 1)[1]))
        return records

    async def test_json_envelope_and_content_have_separate_parse_stages(self) -> None:
        for raw in (b"not json", b"\xff", b'{"choices":1,"choices":2}'):
            with self.subTest(raw=repr(raw)):
                log = (await self.rejected(httpx.Response(200, content=raw)))[0]
                self.assertEqual(log["stage"], "envelope")
                self.assertEqual(log["issues"], [{"path": "$", "type": "json_parse"}])
        for text in (UNTRUSTED, '{"status":"uncertain","status":"possible"}', '{"clues":NaN}'):
            data = answer()
            data["choices"][0]["message"]["content"] = text
            log = (await self.rejected(httpx.Response(200, json=data)))[0]
            self.assertEqual(log["stage"], "content")
            self.assertEqual(log["issues"], [{"path": "$", "type": "json_parse"}])

    async def test_types_lengths_extra_fields_and_state_contradictions(self) -> None:
        clue = SELECTION["clues"][0]
        cases = [
            ({"status": "possible", "clues": [dict(clue, name={UNTRUSTED: UNTRUSTED})]}, "$.clues[0].name", "field_type"),
            (dict(SELECTION, **{UNTRUSTED: UNTRUSTED}), "$.<unknown>", "extra_field"),
            ({"status": "possible", "clues": [dict(clue, **{UNTRUSTED: UNTRUSTED})]}, "$.clues[0].<unknown>", "extra_field"),
            ({"status": "possible", "clues": [dict(clue, basis=UNTRUSTED * 10)]}, "$.clues[0].basis", "length"),
            ({"status": "possible", "clues": [clue] * 4}, "$.clues", "length"),
            ({"status": "possible", "clues": []}, "$", "state_consistency"),
            ({"status": "unrecognized", "clues": [clue]}, "$", "state_consistency"),
            ({"status": "uncertain", "clues": [clue, clue]}, "$", "state_consistency"),
            ({"status": UNTRUSTED, "clues": []}, "$.status", "enum"),
            ({"clues": []}, "$.status", "missing_field"),
            ([UNTRUSTED], "$", "field_type"),
        ]
        for content, path, reason in cases:
            with self.subTest(path=path, reason=reason):
                log = (await self.rejected(httpx.Response(200, json=answer(content))))[0]
                self.assertEqual(log["stage"], "selection")
                self.assertEqual(log["issues"], [{"path": path, "type": reason}])

    async def test_error_count_and_emitted_entries_bounded(self) -> None:
        content = {"status": "uncertain", "clues": [], **{UNTRUSTED + str(i): UNTRUSTED for i in range(30)}}
        log = (await self.rejected(httpx.Response(200, json=answer(content))))[0]
        self.assertEqual(log["error_count_capped"], 30)
        self.assertEqual(len(log["issues"]), MAX_ISSUES)
        self.assertTrue(all(item == {"path": "$.<unknown>", "type": "extra_field"} for item in log["issues"]))

    async def test_content_type_size_envelope_and_response_size_rejections(self) -> None:
        for response, stage, reason in [
            (httpx.Response(200, json=answer(content=[UNTRUSTED])), "content", "field_type"),
            (httpx.Response(200, json=answer(content=UNTRUSTED * 150)), "content", "length"),
            (httpx.Response(200, json=answer(tool_calls=[{UNTRUSTED: UNTRUSTED}])), "envelope", "envelope"),
            (httpx.Response(200, content=b"x" * 32769), "envelope", "length"),
        ]:
            with self.subTest(stage=stage, reason=reason):
                # answer() normally JSON-encodes content. For the non-string
                # protocol-content case replace it with an actual array.
                if reason == "field_type":
                    data = answer()
                    data["choices"][0]["message"]["content"] = [UNTRUSTED]
                    response = httpx.Response(200, json=data)
                log = (await self.rejected(response))[0]
                self.assertEqual(log["stage"], stage)
                self.assertEqual(log["issues"], [{"path": "$", "type": reason}])

    def test_logger_cannot_accept_arbitrary_stage_reason_or_path(self) -> None:
        with self.assertLogs(LOGGER, level="WARNING") as captured:
            record_structure(UNTRUSTED, UNTRUSTED, ("clues", 9999, UNTRUSTED))
        data = json.loads(captured.records[0].getMessage().split(" ", 1)[1])
        self.assertEqual(data["stage"], "unknown")
        self.assertEqual(data["issues"], [{"path": "$.clues[*].<unknown>", "type": "validation"}])
