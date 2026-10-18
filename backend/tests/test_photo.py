import asyncio
from io import BytesIO
import json
import unittest
from unittest.mock import patch
import httpx
from tests.business_client import BusinessTestClient as TestClient
from PIL import Image
from app.config import Settings, get_settings
from app.api.explanation import get_qwen_http_client
from app.integrations.qwen import QwenError, configured_endpoint
from app.integrations.qwen_vision import QwenVisionClient, vision_payload
from app.schemas.photo import PhotoSelection
from app.services.photo import normalize_photo, PhotoError, MAX_BYTES
from main import app


def photo(fmt="PNG", size=(30, 20), **kwargs):
    with Image.new("RGB", size, "red") as im, BytesIO() as out:
        im.save(out, fmt, **kwargs); return out.getvalue()


SELECTION = {"status": "possible", "clues": [{"name": "可能地点", "city": "上海"}]}


def answer(content=SELECTION, **message):
    return {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(content), **message}}],
            "usage": {"prompt_tokens": 40, "completion_tokens": 30}}


class ImageSafetyTests(unittest.TestCase):
    def test_formats_dimensions_and_metadata(self):
        for fmt in ["PNG", "JPEG", "WEBP"]:
            with self.subTest(fmt=fmt):
                with Image.open(BytesIO(normalize_photo(photo(fmt, (3000, 1000))))) as im:
                    self.assertEqual(im.size, (2048, 683)); self.assertEqual(im.format, "JPEG")
                    self.assertFalse(im.getexif()); self.assertNotIn("icc_profile", im.info)
        exif = Image.Exif(); exif[274] = 6; exif[270] = "private location"
        with Image.open(BytesIO(normalize_photo(photo("JPEG", exif=exif)))) as im:
            self.assertEqual(im.size, (20, 30)); self.assertFalse(im.getexif())

    def test_reject_corrupt_fake_animation_and_limits(self):
        frames = [Image.new("RGB", (20,20), c) for c in ["red", "blue"]]
        out = BytesIO(); frames[0].save(out, "PNG", save_all=True, append_images=frames[1:], duration=100)
        for raw in [b"", b"<svg></svg>", b"JPEG pretend", photo()[:20], photo("GIF"), out.getvalue(), b"x" * (MAX_BYTES+1)]:
            with self.subTest(size=len(raw)), self.assertRaises(PhotoError): normalize_photo(raw)
        with patch("app.services.photo.MAX_PIXELS", 599), self.assertRaises(PhotoError): normalize_photo(photo())

    def test_schema_no_executable_fields_or_fake_poi(self):
        for content in [dict(SELECTION, coordinates=[1,2]), {"status":"possible", "clues":[]},
                        {"status":"unrecognized", "clues":SELECTION["clues"]},
                        {"status":"uncertain", "clues":SELECTION["clues"]},
                        {"status":"possible", "clues":SELECTION["clues"]*2},
                        {"status":"possible", "clues":SELECTION["clues"]*4},
                        {"status":"possible", "clues":[dict(SELECTION["clues"][0], id="fake")]},
                        {"status":"possible", "clues":[dict(SELECTION["clues"][0], basis=" ")]}]:
            with self.assertRaises(ValueError): PhotoSelection.model_validate(content)
        self.assertEqual(PhotoSelection.model_validate({"status":"uncertain","clues":[]}).clues, [])
        self.assertEqual(PhotoSelection.model_validate({"status":"unrecognized","clues":[]}).clues, [])
        self.assertIsNone(PhotoSelection.model_validate({"status":"possible","clues":[{"name":"线索","city":None}]}).clues[0].city)


class VisionTests(unittest.IsolatedAsyncioTestCase):
    def settings(self, **updates):
        return Settings(_env_file=None, qwen_api_key="test-only", qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", **updates)

    async def test_single_attempt_and_payload_no_filename_or_selected_city(self):
        seen=[]
        def handle(request):
            seen.append(json.loads(request.content)); return httpx.Response(200,json=answer())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            result, usage = await QwenVisionClient(self.settings(),client).recognize(normalize_photo(photo()))
        self.assertEqual(result.status,"possible"); self.assertEqual(usage.output_tokens,30); self.assertEqual(len(seen),1)
        self.assertEqual(seen[0]["model"],"qwen3-vl-plus"); self.assertFalse(seen[0]["stream"]); self.assertFalse(seen[0]["enable_thinking"])
        self.assertEqual(seen[0]["max_tokens"],1024); self.assertNotIn("tools",seen[0])
        self.assertNotIn("destination_id",json.dumps(seen[0])); self.assertNotIn("filename",json.dumps(seen[0]))

    async def test_errors_no_retries_and_sanitized(self):
        for status, expected in [(401,"authentication"),(403,"authentication"),(429,"rate_limit"),(400,"model_unavailable"),(302,"upstream")]:
            calls=[]
            def handler(request):
                calls.append(request); return httpx.Response(status,text="sensitive upstream detail")
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
                with self.assertRaises(QwenError) as error: await QwenVisionClient(self.settings(),c).recognize(normalize_photo(photo()))
            self.assertEqual(error.exception.code,expected); self.assertNotIn("sensitive",str(error.exception)); self.assertEqual(len(calls),1)
        async def slow(request): await asyncio.sleep(.1); return httpx.Response(200,json=answer())
        async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as c:
            with self.assertRaises(QwenError) as error, patch("app.integrations.qwen_vision.DEADLINE",.01): await QwenVisionClient(self.settings(),c).recognize(normalize_photo(photo()))
        self.assertEqual(error.exception.code,"timeout")

    async def test_bad_output_refusal_tools_and_oversize(self):
        for data in [answer({"status":"possible", "clues":[]}), answer(tool_calls=[{}]), answer(refusal="no"),
                     {"choices":[{"finish_reason":"length","message":{}}]}, answer({"status":"unrecognized","clues":[],"command":"delete"})]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200,json=data))) as c:
                with self.assertRaises(QwenError): await QwenVisionClient(self.settings(),c).recognize(normalize_photo(photo()))
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200,content=b"x"*32769))) as c:
            with self.assertRaises(QwenError): await QwenVisionClient(self.settings(),c).recognize(normalize_photo(photo()))

    def test_text_models_still_closed_and_vision_config_separate(self):
        with self.assertRaises(QwenError): configured_endpoint(self.settings(qwen_model="qwen3-vl-plus"))
        with self.assertRaises(QwenError): vision_payload(b"image","other-model")
        self.assertEqual(self.settings().qwen_model,"qwen3.7-flash-2026-07-15")


class PhotoApiTests(unittest.TestCase):
    def setUp(self):
        self.calls=[]
        async def client():
            def handle(req): self.calls.append(req); return httpx.Response(200,json=answer())
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c: yield c
        app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None,qwen_api_key="test",qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
        app.dependency_overrides[get_qwen_http_client] = client
        self.client=TestClient(app)

    def tearDown(self): app.dependency_overrides.clear(); self.client.close()

    def test_binary_only_actual_content_not_mime_and_binding(self):
        result=self.client.post("/media/landmark?destination_id=shanghai&request_id=r1",content=photo(),headers={"Content-Type":"text/plain"})
        self.assertEqual(result.status_code,200,result.text); self.assertEqual(result.json()["request_id"],"r1"); self.assertEqual(len(self.calls),1)
        self.assertEqual(self.client.post("/media/landmark?destination_id=shanghai&request_id=r2",content=b"not image").status_code,422)
        self.assertEqual(len(self.calls),1)

    def test_query_bounds_and_upload_bounds_no_model(self):
        for query in ["destination_id=unknown&request_id=r", "destination_id=shanghai&request_id=r&url=http://bad", "destination_id=shanghai&request_id=r&request_id=s"]:
            self.assertEqual(self.client.post("/media/landmark?"+query,content=photo()).status_code,422)
        with patch("app.api.photo.MAX_BYTES",10):
            self.assertEqual(self.client.post("/media/landmark?destination_id=shanghai&request_id=r",content=photo()).status_code,413)
        with patch("app.api.photo.busy",True):
            self.assertEqual(self.client.post("/media/landmark?destination_id=shanghai&request_id=r",content=photo()).status_code,429)
        self.assertEqual(self.calls,[])
