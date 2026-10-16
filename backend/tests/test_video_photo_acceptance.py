import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from io import BytesIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import base64

from PIL import Image
from app.integrations.qwen import QwenError
from tools import video_photo_acceptance as trial


class VideoPhotoAccountingTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())) / "repo"
        self.root.mkdir(); (self.root / "code.py").write_text("frozen")
        self.stage = self.root.parent / "trippilot-evidence/media-2b/batch-20261016"
        self.stage.mkdir(parents=True)
        for name, value in [("tools.itinerary_query_acceptance.ROOT", self.root), ("tools.video_photo_acceptance.ROOT", self.root),
                            ("tools.video_photo_acceptance.STAGE", self.stage), ("tools.video_photo_acceptance.FILES", ["code.py"])]:
            self.stack.enter_context(patch(name, value))
        self.stack.enter_context(patch.object(trial.subprocess, "check_output", return_value="test-head"))
        with Image.new("RGB", (12, 12)) as image, BytesIO() as out:
            image.save(out, "JPEG"); self.image = out.getvalue()
        (self.stage / "frame.jpg").write_bytes(self.image); (self.stage / "video.mp4").write_bytes(b"controlled-video")
        cases = [{"id": i, "video": "video.mp4", "file": "frame.jpg", "frame_index": 1, "target_seconds": 1,
                  "destination_id": "shanghai", "source": "test", "expected": "test", "visual_review": "test", "related_samples": []} for i in trial.IDS]
        (self.stage / "samples.json").write_text(json.dumps({"cases": cases, "criteria": "controlled"}))
        self.old = self.stage.parents[1] / "old-calls.jsonl"; self.old.write_text('{"previous":32}\n')
        self.stack.enter_context(patch.object(trial, "validate_container"))
        self.stack.enter_context(patch.object(trial, "executables", return_value=("probe", "decode")))
        self.stack.enter_context(patch.object(trial, "extract_frames", return_value=SimpleNamespace(frames=[
            SimpleNamespace(index=1, target_seconds=1, jpeg_base64=base64.b64encode(self.image).decode())])))
        asyncio.run(trial.freeze()); self.ledger = trial.VideoPhotoLedger(self.stage)

    def attempt(self, i):
        c = self.ledger.protocol["cases"][i]
        return {"event": "attempt", "operation": c["id"], "payload_hash": c["payload_hash"]}

    def complete(self, i, pause=False):
        self.ledger.append({"event": "completed", "operation": trial.IDS[i], "pause": pause, "category": "invalid_output"})

    def test_eight_shared_operations_restart_duplicate_and_uncertain_fail_closed(self):
        for i in range(8):
            self.ledger.append(self.attempt(i), True)
            with self.assertRaises(QwenError): trial.VideoPhotoLedger(self.stage).append(self.attempt(min(i+1, 7)), True)
            self.complete(i)
            with self.assertRaises(QwenError): self.ledger.append(self.attempt(i), True)
        with self.assertRaises(QwenError): self.ledger.append(self.attempt(0), True)
        self.assertEqual(self.old.read_text(), '{"previous":32}\n')
        self.assertEqual(len(self.ledger.rows((self.stage / "calls.jsonl").read_bytes())), 17)

    def test_concurrent_reservations_only_one_and_failure_remains_counted(self):
        def reserve():
            try: self.ledger.append(self.attempt(0), True); return True
            except QwenError: return False
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(lambda _: reserve(), range(2))), [False, True])
        self.complete(0, pause=True)
        with self.assertRaises(QwenError): self.ledger.append(self.attempt(1), True)

    def test_bound_payload_city_id_and_chrome_requires_separate_arming(self):
        payload = trial.vision_payload(trial.normalize_photo(self.image))
        for ctx in ({"destination_id": "beijing", "request_id": "api01"}, {"destination_id": "shanghai", "request_id": "api02"}):
            with self.assertRaises(QwenError): self.ledger.next_case(payload, ctx)
        self.assertEqual(self.ledger.next_case(payload, {"destination_id": "shanghai", "request_id": "api01"})["id"], "api01")
        for i in range(4): self.ledger.append(self.attempt(i), True); self.complete(i)
        ctx = {"destination_id": "shanghai", "request_id": "random-browser-id"}
        with self.assertRaises(QwenError): self.ledger.next_case(payload, ctx)
        trial.arm("chrome01"); self.assertEqual(self.ledger.next_case(payload, ctx)["id"], "chrome01")
        self.ledger.append(self.attempt(4), True); self.complete(4)
        with self.assertRaises(QwenError): self.ledger.next_case(payload, ctx)
        with self.assertRaises(QwenError): trial.arm("chrome01")
        trial.arm("chrome02"); self.assertEqual(self.ledger.next_case(payload, ctx)["id"], "chrome02")
        self.assertNotIn("expected", str(payload)); self.assertNotIn("frame.jpg", str(payload)); self.assertNotIn("api01", str(payload))

    def test_versions_media_and_old_ledger_integrity_no_reset(self):
        for path in [self.root / "code.py", self.stage / "frame.jpg", self.stage / "video.mp4", self.old, self.stage / "protocol.json", self.stage / "samples.json"]:
            raw = path.read_bytes(); path.write_bytes(raw + b"changed")
            with self.assertRaises((QwenError, ValueError)): self.ledger.verify()
            path.write_bytes(raw)
        (self.stage / "calls.jsonl").write_bytes(b"")
        with self.assertRaises(QwenError): self.ledger.append(self.attempt(0), True)
        with self.assertRaises(ValueError): asyncio.run(trial.freeze())
