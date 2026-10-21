import asyncio
import copy
import json
import logging
import unittest
from dataclasses import asdict
from unittest.mock import patch

import httpx
from tests.business_client import BusinessTestClient as TestClient
from fastapi import HTTPException, Request
from pydantic import ValidationError

from api.explanation import explain_schedule, get_qwen_http_client
from core.config import Settings, get_settings
from tooling.providers.qwen import MODEL, QwenClient, QwenError, configured_endpoint, model_payload, parse_result
from core.logging_filters import RedactHttpCredentials
from core.schemas.explanation import ExplanationRequest, ModelSelection, ScheduleSnapshot
from tooling.travel.explanation import CODE_TYPES, build_facts, compact_schedule, fixed_limitations, render_selection, validate_snapshot
from main import app
from tests.explanation_samples import business_samples


def settings(**updates):
    return Settings(_env_file=None, qwen_api_key="test-only-not-a-real-key",
                    qwen_base_url="https://llm-test.cn-beijing.maas.aliyuncs.com/compatible-mode/v1", **updates)


def envelope(content=None, **updates):
    return {"model": MODEL, "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": content if content is not None else json.dumps({"highlights": [{"message_code": "arrangement", "fact_ids": ["f0"]}]})}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 30}} | updates


def request_body(name="complete_walk"):
    return {"contract_version": "schedule-explanation-v1", "request_id": "controlled-1", "snapshot": compact_schedule(business_samples()[name])}


class SnapshotTests(unittest.TestCase):
    def test_generation_schema_binds_each_code_to_only_matching_fact_ids(self):
        for name, sample in business_samples().items():
            with self.subTest(name=name):
                facts = build_facts(sample)
                payload = model_payload(facts)
                schema = payload["response_format"]["json_schema"]["schema"]
                choices = schema["$defs"]["Highlight"]["anyOf"]
                seen = set()
                for choice in choices:
                    code = choice["properties"]["message_code"]["const"]
                    ids = choice["properties"]["fact_ids"]["items"]["enum"]
                    self.assertEqual(ids, [fact.id for fact in facts if fact.kind in CODE_TYPES[code]])
                    self.assertTrue(ids)
                    self.assertFalse(seen.intersection(ids))
                    seen.update(ids)
                    self.assertFalse(choice["additionalProperties"])
                self.assertEqual(seen, {fact.id for fact in facts})
                self.assertNotIn("uniqueItems", json.dumps(schema))
                catalogue = json.loads(payload["messages"][1]["content"])
                for fact in catalogue["facts"]:
                    self.assertIn(fact["kind"], CODE_TYPES[fact["message_code"]])

    def test_twelve_controlled_samples_replay_and_fact_references(self):
        samples = business_samples()
        self.assertEqual(len(samples), 12)
        for name, original in samples.items():
            with self.subTest(name=name):
                snapshot = ScheduleSnapshot.model_validate_json(json.dumps(compact_schedule(original)))
                verified = validate_snapshot(snapshot)
                facts = build_facts(verified)
                self.assertEqual(len({fact.id for fact in facts}), len(facts))
                # Exercise every fact/type, not a fabricated 'real model' selection.
                for fact in facts:
                    code = next(code for code, kinds in CODE_TYPES.items() if fact.kind in kinds)
                    rendered = render_selection(ModelSelection.model_validate({"highlights": [{"message_code": code, "fact_ids": [fact.id]}]}), facts)
                    self.assertEqual(rendered[0].text, fact.text)
                notices = fixed_limitations(verified)
                self.assertIn("未核验营业", "".join(notices))
                self.assertIn("参考", "".join(notices))
                self.assertEqual(any("个必去地点未安排" in n for n in notices), bool(verified.unscheduled))

    def test_invalid_structure_and_cross_field_mutations_rejected(self):
        original = request_body()["snapshot"]
        changes = [
            lambda s: s.update(arbitrary_prompt="instruction"),
            lambda s: s["request"].update(weather="rain"),
            lambda s: s["request"]["accommodation_place"].update(name=" " ),
            lambda s: s["request"]["accommodation_place"].update(name="x" * 161),
            lambda s: s["request"]["must_visit_places"][0].update(source="verified"),
            lambda s: s["request"]["must_visit_places"][0].update(latitude=float("nan")),
            lambda s: s["days"][0].update(date="2026-02-30"),
            lambda s: s["days"].reverse(),
            lambda s: s["days"][0].update(return_time=None),
            lambda s: s["days"][0]["items"][1].update(place_id="missing"),
            lambda s: s["days"][0]["items"][1].update(duration_minutes=59),
            lambda s: s["days"][0]["items"][1].update(end_time="22:00"),
            lambda s: s["days"][0]["items"][0].update(edge_id="e999"),
            lambda s: s["days"][0]["items"][-1].update(to_place_id="p2"),
            lambda s: s["edges"][0].update(duration_minutes=9),
            lambda s: s["edges"][0].update(duration_seconds=True),
            lambda s: s["edges"][0].update(duration_seconds=float("inf")),
            lambda s: s["edges"][0].update(transport_mode="transit"),
            lambda s: s["edges"][0].update(status="same_place", duration_seconds=0, duration_minutes=0, source="same_place"),
            lambda s: s["edges"][0].update(used=False),
            lambda s: s["edges"][0]["origin"].update(longitude=122),
            lambda s: s["edges"].append(copy.deepcopy(s["edges"][0])),
            lambda s: s["edges"].pop(),
            lambda s: s.update(status="unscheduled"),
            lambda s: s["request"]["must_visit_places"].reverse(),
        ]
        for change in changes:
            data = copy.deepcopy(original); change(data)
            with self.subTest(change=changes.index(change)), self.assertRaises((ValidationError, ValueError)):
                validate_snapshot(ScheduleSnapshot.model_validate_json(json.dumps(data)))

    def test_reasons_optional_roles_lunch_waits_and_budget_are_replayed(self):
        for name, mutate in [
            ("required_prefix", lambda s: s["unscheduled"][0].update(reason="no_route")),
            ("optional_failure", lambda s: s["optional_results"][0]["attempts"][0].update(outcome="scheduled")),
            ("lunch_wait", lambda s: s["days"][0]["items"].__setitem__(slice(None), [i for i in s["days"][0]["items"] if i["kind"] != "lunch"])),
            ("transit_unsupported", lambda s: s["optional_results"][0].update(not_attempted_reason=None)),
            ("three_days_blank", lambda s: s["days"].pop()),
        ]:
            value = request_body(name)["snapshot"]; mutate(value)
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_snapshot(ScheduleSnapshot.model_validate_json(json.dumps(value)))

    def test_model_does_not_receive_coordinates_geometry_or_arbitrary_fields(self):
        sample = business_samples()["transit_unknown_legs"]
        sample.rules = ["untrusted arbitrary instructions"]
        sample.edges[0].transit_route.legs[0].instruction = "untrusted transit text"
        data = json.dumps(model_payload(build_facts(validate_snapshot(ScheduleSnapshot.model_validate_json(json.dumps(compact_schedule(sample)))))), ensure_ascii=False)
        for forbidden in ["longitude", "latitude", "geometry", "fare_cny", "weather", "untrusted arbitrary", "untrusted transit"]:
            self.assertNotIn(forbidden, data)

    def test_injection_is_label_only_and_cannot_select_unverified_conclusion(self):
        facts = build_facts(business_samples()["injection_same_name_ids"])
        payload = model_payload(facts)
        self.assertIn("不可信标签", payload["messages"][0]["content"])
        self.assertIn("<script>", payload["messages"][1]["content"])
        for highlight in [
            {"message_code": "visits", "fact_ids": ["f0"]},
            {"message_code": "arrangement", "fact_ids": ["missing"]},
            {"message_code": "arrangement", "fact_ids": ["f0", "f0"]},
        ]:
            with self.assertRaises(ValueError):
                render_selection(ModelSelection.model_validate({"highlights": [highlight]}), facts)
        with self.assertRaises(ValidationError):
            ModelSelection.model_validate({"highlights": [{"message_code": "optimal", "fact_ids": ["f0"], "text": "所有预约成功"}]})


class QwenTests(unittest.IsolatedAsyncioTestCase):
    async def test_browser_disconnect_cancels_one_pending_operation_and_cleans_watcher(self):
        started, cancelled = asyncio.Event(), asyncio.Event()
        calls = []
        async def handler(request):
            calls.append(request); started.set()
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()
        async def receive():
            await started.wait()
            return {"type": "http.disconnect"}
        request = Request({"type": "http", "method": "POST", "path": "/trips/schedule-explanation", "headers": []}, receive=receive)
        request._body = json.dumps(request_body()).encode()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(HTTPException) as caught:
                await explain_schedule(request, settings(), client)
        self.assertEqual(caught.exception.status_code, 499)
        self.assertEqual(len(calls), 1); self.assertTrue(cancelled.is_set())

    async def test_one_nonstreaming_nonthinking_strict_bounded_request(self):
        calls = []
        async def handler(request):
            calls.append(request)
            body = json.loads(request.content)
            self.assertEqual(body["model"], MODEL)
            self.assertFalse(body["stream"]); self.assertFalse(body["enable_thinking"])
            self.assertEqual(body["max_tokens"], 1024)
            self.assertTrue(body["response_format"]["json_schema"]["strict"])
            self.assertNotIn("tools", body); self.assertNotIn("enable_search", body)
            return httpx.Response(200, json=envelope())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            value, usage = await QwenClient(settings(), client).select(build_facts(business_samples()["complete_walk"]))
        self.assertEqual(len(calls), 1); self.assertEqual(usage.output_tokens, 30)
        self.assertEqual(value.highlights[0].fact_ids, ["f0"])

    async def test_http_failures_are_safe_never_retried_and_no_redirect(self):
        for status, code in [(301, "upstream"), (400, "model_unavailable"), (401, "authentication"), (402, "quota"), (403, "authentication"), (429, "rate_limit"), (500, "upstream")]:
            calls = []
            async def handler(request):
                calls.append(request)
                return httpx.Response(status, headers={"Location": "https://evil.example"}, text="sensitive key raw payload")
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
                with self.assertRaises(QwenError) as caught:
                    await QwenClient(settings(), client).select([])
            self.assertEqual(caught.exception.code, code); self.assertNotIn("sensitive", str(caught.exception))
            self.assertEqual(len(calls), 1)

    async def test_connection_read_timeout_deadline_cancellation_and_output_size(self):
        for error in [httpx.ConnectError("sensitive"), httpx.ConnectTimeout("sensitive"), httpx.ReadTimeout("sensitive")]:
            calls = []
            async def fail(request):
                calls.append(request); raise error
            async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
                with self.assertRaises(QwenError):
                    await QwenClient(settings(), client).select([])
            self.assertEqual(len(calls), 1)
        cancelled = asyncio.Event()
        class Slow(httpx.AsyncByteStream):
            async def __aiter__(self):
                try:
                    while True:
                        await asyncio.sleep(.003); yield b" "
                finally:
                    cancelled.set()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=Slow()))) as client:
            with patch("tooling.providers.qwen.DEADLINE_SECONDS", .02), self.assertRaises(QwenError) as caught:
                await QwenClient(settings(), client).select([])
            self.assertEqual(caught.exception.code, "timeout"); self.assertTrue(cancelled.is_set())
            task = asyncio.create_task(QwenClient(settings(), client).select([]))
            await asyncio.sleep(.01); task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b" " * 32769))) as client:
            with self.assertRaises(QwenError) as caught: await QwenClient(settings(), client).select([])
            self.assertEqual(caught.exception.code, "structure")


class ParsingTests(unittest.TestCase):
    def test_authorization_workspace_and_httpcore_logs_are_suppressed(self):
        for name, message in [("httpx", "POST https://llm-private.cn-beijing.maas.aliyuncs.com/compatible-mode/v1 Authorization: Bearer sensitive"),
                              ("httpcore.http11", "headers authorization sensitive")]:
            record = logging.LogRecord(name, logging.INFO, "", 0, message, (), None)
            RedactHttpCredentials().filter(record)
            self.assertNotIn("sensitive", record.getMessage()); self.assertNotIn("llm-private", record.getMessage())

    def test_safe_configuration_does_not_break_other_features(self):
        for url in ["http://dashscope.aliyuncs.com/compatible-mode/v1", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                    "https://dashscope.aliyuncs.com.evil.com/compatible-mode/v1", "https://user:pass@dashscope.aliyuncs.com/compatible-mode/v1",
                    "https://dashscope.aliyuncs.com:443/compatible-mode/v1", "https://dashscope.aliyuncs.com/compatible-mode/v1?key=x",
                    "https://dashscope.aliyuncs.com/compatible-mode/v1#x", "https://llm-a.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
                    "https://localhost/compatible-mode/v1", "https://dashscope.aliyuncs.com/api/v1"]:
            cfg = settings(); cfg.qwen_base_url = url
            with self.subTest(url=url), self.assertRaises(QwenError): configured_endpoint(cfg)
        for url in ["https://dashscope.aliyuncs.com/compatible-mode/v1", "https://llm-example.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/"]:
            cfg = settings(); cfg.qwen_base_url = url
            self.assertTrue(configured_endpoint(cfg).endswith("/chat/completions"))
        cfg = settings(); cfg.qwen_model = "more-expensive"
        with self.assertRaises(QwenError): configured_endpoint(cfg)
        self.assertNotIn("test-only", repr(settings()))

    def test_refusal_truncation_empty_invalid_shape_tools_references_usage(self):
        mutations = [
            (lambda d: d["choices"][0]["message"].update(refusal="sensitive refusal"), "refused"),
            (lambda d: d["choices"][0].update(finish_reason="length"), "truncated"),
            (lambda d: d["choices"][0].update(finish_reason="content_filter"), "refused"),
            (lambda d: d["choices"][0]["message"].update(content=""), "empty"),
            (lambda d: d["choices"][0]["message"].update(content="not JSON"), "structure"),
            (lambda d: d["choices"][0]["message"].update(content='{"highlights":[]}'), "structure"),
            (lambda d: d["choices"][0]["message"].update(content='{"highlights":[],"highlights":[]}'), "structure"),
            (lambda d: d["choices"][0]["message"].update(tool_calls=[{"sensitive": "tool"}]), "structure"),
            (lambda d: d["choices"][0]["message"].update(reasoning_content="unexpected thought"), "structure"),
            (lambda d: d["usage"].update(completion_tokens=1025), "truncated"),
        ]
        for mutate, code in mutations:
            data = envelope(); mutate(data)
            with self.assertRaises(QwenError) as caught: parse_result(data)
            self.assertEqual(caught.exception.code, code)
        self.assertIsNone(parse_result(envelope(usage=None))[1].output_tokens)


class ExplanationAPITests(unittest.TestCase):
    def tearDown(self): app.dependency_overrides.clear()

    def test_safe_api_success_invalid_snapshot_size_missing_config_and_bad_references(self):
        calls = []
        async def handler(request):
            calls.append(request)
            return httpx.Response(200, json=envelope())
        async def client():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as value: yield value
        app.dependency_overrides[get_settings] = lambda: settings()
        app.dependency_overrides[get_qwen_http_client] = client
        with TestClient(app) as client:
            body = request_body()
            response = client.post("/trips/schedule-explanation", json=body)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["request_id"], body["request_id"])
            self.assertIn("基于当前提交快照", response.json()["limitations"][0])
            self.assertEqual(len(calls), 1)
            app.dependency_overrides[get_settings] = lambda: settings()
            with patch("tooling.providers.qwen.QwenClient.select", return_value=(ModelSelection.model_validate({"highlights": [{"message_code": "visits", "fact_ids": ["f0"]}]}), parse_result(envelope())[1])):
                invalid_reference = client.post("/trips/schedule-explanation", json=body)
                self.assertEqual(invalid_reference.status_code, 502)
                self.assertEqual(invalid_reference.json()["detail"]["code"], "references")
            duplicate = json.dumps(body)[:-1] + ',"request_id":"duplicate"}'
            self.assertEqual(client.post("/trips/schedule-explanation", content=duplicate).status_code, 422)
            invalid = copy.deepcopy(body); invalid["snapshot"]["days"][0]["return_time"] = "23:00"
            self.assertEqual(client.post("/trips/schedule-explanation", json=invalid).status_code, 422)
            self.assertEqual(client.post("/trips/schedule-explanation", content=b"x" * 65537).status_code, 413)
            self.assertEqual(len(calls), 1)
            app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, qwen_api_key="", qwen_base_url="")
            self.assertEqual(client.post("/trips/schedule-explanation", json=body).status_code, 503)
            self.assertEqual(client.get("/health").status_code, 200)
            self.assertEqual(len(calls), 1)
