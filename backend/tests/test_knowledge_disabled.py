"""Official search-only routing, separate from controlled research Q&A tests."""
import unittest
from unittest.mock import patch

from tests.business_client import BusinessTestClient as TestClient

from api.explanation import get_qwen_http_client
from core.config import get_settings
from core.schemas.knowledge import KnowledgeResponse
from main import app


class OfficialKnowledgeTests(unittest.TestCase):
    def test_disabled_route_cannot_be_enabled_by_body_query_or_headers(self) -> None:
        def forbidden():
            raise AssertionError("Disabled entry must not acquire config or client")

        app.dependency_overrides[get_settings] = forbidden
        app.dependency_overrides[get_qwen_http_client] = forbidden
        try:
            with TestClient(app) as client, patch("tooling.providers.qwen.QwenClient.generate") as generate:
                for body in [b"", b"not-json", b'{"enabled":true,"model":"anything"}', b"x" * 4097]:
                    with self.subTest(body_length=len(body)):
                        r = client.post("/knowledge/answer?enabled=true", content=body,
                                        headers={"X-Enable-AI": "true"})
                        self.assertEqual(r.status_code, 503)
                        self.assertEqual(r.json(), {"detail": {
                            "code": "knowledge_answer_disabled",
                            "message": "AI问答暂未开放，请使用资料检索并查阅官方原文。",
                        }})
                generate.assert_not_called()
        finally:
            app.dependency_overrides.clear()

    def test_only_official_disabled_handler_registered_other_ai_routes_unchanged(self) -> None:
        paths = app.openapi()["paths"]
        self.assertEqual(paths["/knowledge/answer"]["post"]["operationId"],
                         "answer_disabled_knowledge_answer_post")
        # Invalid input is still handled by the existing itinerary AI endpoints,
        # not by a global Qwen-disable switch. No actual model request is needed.
        with TestClient(app) as client, patch("tooling.providers.qwen.QwenClient.generate") as generate:
            for path, operation in [("/trips/schedule-explanation", "explain_schedule_trips_schedule_explanation_post"),
                                    ("/trips/adjustment-intent", "adjustment_intent_trips_adjustment_intent_post")]:
                self.assertEqual(paths[path]["post"]["operationId"], operation)
                self.assertEqual(client.post(path, json={}).status_code, 422)
            generate.assert_not_called()

    def test_three_campuses_and_empty_result_remain_local_without_model(self) -> None:
        with TestClient(app) as client, patch("tooling.providers.qwen.QwenClient.generate") as generate, \
                patch("httpx.AsyncClient.send", side_effect=AssertionError("No external requests")):
            for venue, question in [("museum_people", "2025免预约"), ("museum_east", "数字馆预约"),
                                    ("natural", "儿童免票陪同"), ("natural", "迪士尼烟花")]:
                response = client.post("/knowledge/search", json={"venue_id": venue, "question": question, "limit": 5})
                self.assertEqual(response.status_code, 200)
                result = KnowledgeResponse.model_validate_json(response.content)
                self.assertEqual(result.status, "no_match" if question == "迪士尼烟花" else "matches")
                self.assertTrue(all(m.source.venue_id == venue for m in result.matches))
            generate.assert_not_called()
