"""Offline, real FFmpeg fixtures in independent temporary directories. No APIs."""
import asyncio
import base64
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from tests.business_client import BusinessTestClient as TestClient
from PIL import Image
from starlette.requests import Request

from app.api import video as api
from app.services import video as service
from main import app


def command(args: list[str]) -> None:
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)


def fixture(path: Path, size="320x180", duration="12", codec="libx264", extra: list[str] | None = None) -> bytes:
    command([service.executables()[1], "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
             f"color=red:size={size}:rate=2:duration={duration}", "-vf",
             "drawbox=color=green:t=fill:enable='gte(t,4)',drawbox=color=blue:t=fill:enable='gte(t,8)'",
             "-c:v", codec, "-threads", "1", *(extra or []), "-y", str(path)])
    return path.read_bytes()


class VideoDecodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_landscape_portrait_rotation_and_midpoint_colors(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for size in ("320x180", "180x320"):
                path = root/"input.mp4"
                data = fixture(path, size)
                service.validate_container(data)
                result = await service.extract_frames(path, "controlled", service.executables())
                self.assertEqual([f.target_seconds for f in result.frames], [2., 6., 10.])
                self.assertEqual((result.width, result.height), tuple(map(int, size.split("x"))))
                colors = []
                for frame in result.frames:
                    raw = base64.b64decode(frame.jpeg_base64)
                    self.assertLessEqual(len(raw), service.MAX_FRAME)
                    with Image.open(BytesIO(raw)) as im:
                        colors.append(max(range(3), key=lambda i: im.getpixel((10, 10))[i]))
                        self.assertFalse(im.getexif()); self.assertNotIn("comment", im.info)
                self.assertEqual(colors, [0, 1, 2])
            rotated = root/"rotated.mp4"
            # Asymmetric quadrant also verifies rotation direction, not just dimensions.
            command([service.executables()[1], "-loglevel", "error", "-f", "lavfi", "-i",
                     "color=red:size=180x320:rate=2:duration=2", "-vf", "drawbox=x=0:y=0:w=90:h=160:color=blue:t=fill",
                     "-c:v", "libx264", "-threads", "1", "-y", str(path)])
            command([service.executables()[1], "-loglevel", "error", "-display_rotation", "90", "-i", str(path), "-c", "copy", str(rotated)])
            result = await service.extract_frames(rotated, "r", service.executables())
            self.assertEqual((result.width, result.height, result.rotation_degrees), (320, 180, 90))
            self.assertGreater(result.frames[0].width, result.frames[0].height)
            with Image.open(BytesIO(base64.b64decode(result.frames[0].jpeg_base64))) as im:
                self.assertGreater(im.getpixel((10, 170))[2], 200)  # top-left → bottom-left (CCW90)
                self.assertGreater(im.getpixel((310, 10))[0], 200)

    async def test_short_static_not_duplicated_and_max_six(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/"input.mp4"
            fixture(p, duration="0.5")
            # Single frame at t=0 cannot satisfy midpoint: do not duplicate it.
            with self.assertRaises(service.VideoError) as caught:
                await service.extract_frames(p,"r",service.executables())
            self.assertEqual(caught.exception.code,"no_frames")
            fixture(p,duration="1")
            result=await service.extract_frames(p,"r",service.executables())
            self.assertEqual(len(result.frames),1)

    async def test_static_repeat_and_size_downscale(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"input.mp4"
            fixture(p,size="1920x1080",duration="30")
            result=await service.extract_frames(p,"r",service.executables())
            self.assertEqual(result.sample_count,6)
            self.assertLess(len(result.frames),6)
            self.assertEqual((result.frames[0].width,result.frames[0].height),(1280,720))
            self.assertLessEqual(len(result.model_dump_json().encode()),service.MAX_RESPONSE)

    async def test_reject_actual_unsupported_duration_dimensions_codec_and_streams(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"input.mp4"
            for kwargs in [dict(duration="31"),dict(size="2000x100"),dict(size="1800x1800"),dict(codec="mpeg4")]:
                fixture(p,**kwargs)
                with self.assertRaises(service.VideoError): await service.extract_frames(p,"r",service.executables())
            fixture(p)
            for kind in ("double","audio"):
                out=Path(d)/(kind+".mp4")
                if kind=="double":
                    command([service.executables()[1],"-loglevel","error","-i",str(p),"-map","0:v","-map","0:v","-c","copy",str(out)])
                else:
                    command([service.executables()[1],"-loglevel","error","-f","lavfi","-i","sine=duration=1","-c:a","aac",str(out)])
                with self.assertRaises(service.VideoError): await service.extract_frames(out,"r",service.executables())

    def test_container_fake_corrupt_external_references(self):
        for raw in (b"",b"#EXTM3U\nhttp://example.test",b"<svg/>",b"ftypisom",b"x"*30):
            with self.assertRaises(service.VideoError): service.validate_container(raw)
        with tempfile.TemporaryDirectory() as d:
            data=fixture(Path(d)/"video.mp4",duration="1")
            for bad in [data[:-20],data.replace(b"url \x00\x00\x00\x01",b"url \x00\x00\x00\x00")]:
                with self.assertRaises(service.VideoError): service.validate_container(bad)

    def test_missing_dependencies_does_not_prevent_app_start(self):
        with patch("app.services.video.Path.is_file",return_value=False), TestClient(app) as client:
            self.assertEqual(client.get("/health").status_code,200)
            r=client.post("/media/video-frames?request_id=r",content=b"x")
            self.assertEqual(r.status_code,503); self.assertEqual(r.json()["detail"]["code"],"video_unavailable")

    def test_probe_unknown_nan_rotation_and_ambiguous_metadata(self):
        v={"format":{"format_name":"mov,mp4,m4a,3gp,3g2,mj2","duration":"2"},"streams":[{"codec_type":"video","codec_name":"h264","width":320,"height":180,"duration":"2"}]}
        self.assertEqual(service.parse_probe(json.dumps(v).encode()),(2.,320,180,0))
        for changes in [{"duration":"nan"},{"duration":"0"},{"width":None},{"sample_aspect_ratio":"4:3"},{"side_data_list":[{"rotation":45}]}]:
            other=deepcopy(v); other["streams"][0].update(changes)
            with self.assertRaises(service.VideoError): service.parse_probe(json.dumps(other).encode())

    def test_actual_http_binary_limits_binding_and_no_external_client(self):
        with tempfile.TemporaryDirectory() as d, TestClient(app) as client:
            data=fixture(Path(d)/"sample.mp4")
            with patch("httpx.AsyncClient.send",side_effect=AssertionError("No external request")):
                response=client.post("/media/video-frames?request_id=bound",content=data,headers={"Content-Type":"text/plain"})
            self.assertEqual(response.status_code,200,response.text[:200])
            self.assertEqual(response.json()["request_id"],"bound")
            self.assertEqual(response.headers["cache-control"],"no-store")
            for query in ["","?request_id=x&url=x","?request_id=x&request_id=x","?request_id=../a"]:
                self.assertEqual(client.post("/media/video-frames"+query,content=data).status_code,422)
            self.assertEqual(client.post("/media/video-frames?request_id=x",content=b"").status_code,413)
            with patch.object(api,"MAX_UPLOAD",10):
                self.assertEqual(client.post("/media/video-frames?request_id=x",content=data).status_code,413)
            self.assertFalse(api.busy)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.data=fixture(Path(self.tmp.name)/"fixture.mp4",duration="2")
        self.queue=asyncio.Queue();self.queue.put_nowait({"type":"http.request","body":self.data,"more_body":False})
        self.receivers=0;self.max_receivers=0
        async def receive():
            self.receivers+=1;self.max_receivers=max(self.max_receivers,self.receivers)
            try:return await self.queue.get()
            finally:self.receivers-=1
        self.request=Request({"type":"http","method":"POST","path":"/media/video-frames","query_string":b"request_id=r","headers":[]},receive)
        self.fresh_request=lambda:Request(self.request.scope,receive)
        self.created=[]
        original=tempfile.TemporaryDirectory
        def private(**kwargs):
            obj=original(dir=self.tmp.name,**kwargs); self.created.append(Path(obj.name));return obj
        self.patch=patch.object(api.tempfile,"TemporaryDirectory",side_effect=private);self.patch.start();self.addCleanup(self.patch.stop)
        api.busy=False

    def assert_clean(self):
        self.assertFalse(api.busy);self.assertTrue(all(not p.exists() for p in self.created));self.assertLessEqual(self.max_receivers,1)

    async def test_disconnect_before_decode_never_starts_and_cleans(self):
        self.queue.put_nowait({"type":"http.disconnect"})
        with patch.object(api,"extract_frames",new_callable=AsyncMock) as extract:
            with self.assertRaises(Exception) as caught: await api.video_frames(self.request)
            self.assertEqual(caught.exception.status_code,499);extract.assert_not_awaited()
        self.assert_clean()

    async def test_disconnect_timeout_repeated_cancel_waits_for_child_cleanup_and_busy(self):
        for mode in ("disconnect","timeout","cancel"):
            with self.subTest(mode=mode):
                self.request=self.fresh_request()
                self.queue.put_nowait({"type":"http.request","body":self.data,"more_body":False}) if self.queue.empty() else None
                started=asyncio.Event();cancelled=asyncio.Event();release=asyncio.Event()
                async def held(*args):
                    started.set()
                    try: await asyncio.Future()
                    finally:
                        cancelled.set();await service.settled(release.wait())
                with patch.object(api,"extract_frames",held),patch.object(api,"PROCESS_SECONDS",.05 if mode=="timeout" else 20):
                    task=asyncio.create_task(api.video_frames(self.request));await asyncio.wait_for(started.wait(),2)
                    if mode=="disconnect":self.queue.put_nowait({"type":"http.disconnect"})
                    elif mode=="cancel":task.cancel()
                    await cancelled.wait()
                    self.assertTrue(api.busy)
                    with self.assertRaises(Exception) as busy: await api.video_frames(self.request)
                    self.assertEqual(busy.exception.status_code,429)
                    if mode=="cancel":task.cancel();await asyncio.sleep(0);self.assertTrue(api.busy)
                    release.set()
                    result=await asyncio.gather(task,return_exceptions=True)
                    self.assertIsInstance(result[0],BaseException)
                self.assert_clean()

    async def test_decode_exception_and_normal_one_call(self):
        with patch.object(api,"extract_frames",new_callable=AsyncMock,side_effect=service.invalid()) as extract:
            with self.assertRaises(Exception): await api.video_frames(self.request)
            extract.assert_awaited_once()
        self.assert_clean()
        self.queue.put_nowait({"type":"http.request","body":self.data,"more_body":False})
        result=await api.video_frames(self.fresh_request()); self.assertEqual(result.status_code,200);self.assert_clean()

    async def test_upload_deadline_disconnect_and_partial_body_do_not_decode(self):
        for disconnect in (False,True):
            with self.subTest(disconnect=disconnect):
                while not self.queue.empty():self.queue.get_nowait()
                self.queue.put_nowait({"type":"http.request","body":self.data[:32],"more_body":True})
                if disconnect:self.queue.put_nowait({"type":"http.disconnect"})
                with patch.object(api,"UPLOAD_SECONDS",.02),patch.object(api,"extract_frames",new_callable=AsyncMock) as extract:
                    with self.assertRaises(Exception) as caught:await api.video_frames(self.fresh_request())
                    self.assertEqual(caught.exception.status_code,499 if disconnect else 408);extract.assert_not_awaited()
                self.assert_clean()

    async def test_disconnect_during_validation_keeps_capacity_until_thread_retires(self):
        import threading
        started=threading.Event();release=threading.Event()
        def held(data):
            started.set();release.wait(3)
        with patch.object(api,"validate_container",held),patch.object(api,"extract_frames",new_callable=AsyncMock) as extract:
            task=asyncio.create_task(api.video_frames(self.request))
            try:
                while not started.is_set():await asyncio.sleep(.01)
                self.assertEqual(self.created[0].stat().st_mode & 0o777,0o700)
                self.queue.put_nowait({"type":"http.disconnect"});await asyncio.sleep(.02)
                task.cancel();task.cancel();await asyncio.sleep(.02)
                self.assertTrue(api.busy);extract.assert_not_awaited();self.assertTrue(self.created[0].exists())
            finally:release.set()
            await asyncio.gather(task,return_exceptions=True)
            extract.assert_not_awaited()
        self.assert_clean()

    async def test_cancel_while_spawn_is_pending_still_reaps_child(self):
        import sys
        original=asyncio.create_subprocess_exec;entered=asyncio.Event();release=asyncio.Event();children=[]
        async def slow(*args,**kwargs):
            entered.set();await release.wait();child=await original(*args,**kwargs);children.append(child);return child
        with patch.object(asyncio,"create_subprocess_exec",slow):
            task=asyncio.create_task(service.run_tool(sys.executable,["-B","-c","import time;time.sleep(30)"],Path(self.tmp.name)/"fixture.mp4",32))
            await entered.wait();task.cancel();await asyncio.sleep(0);task.cancel();release.set()
            await asyncio.wait_for(asyncio.gather(task,return_exceptions=True),3)
            self.assertEqual(len(children),1);self.assertIsNotNone(children[0].returncode)

    async def test_real_child_cancel_output_limit_and_no_shell(self):
        # A locally controlled Python child deliberately ignores TERM. No media/model call.
        import sys
        child=None; original=asyncio.create_subprocess_exec
        async def spawn(*args,**kwargs):
            nonlocal child
            self.assertEqual(kwargs["stderr"],asyncio.subprocess.DEVNULL)
            self.assertNotIn("shell",kwargs)
            child=await original(*args,**kwargs);return child
        path=Path(self.tmp.name)/"fixture.mp4"
        with patch.object(asyncio,"create_subprocess_exec",spawn):
            task=asyncio.create_task(service.run_tool(sys.executable,["-B","-c","import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)"],path,32))
            while child is None:await asyncio.sleep(.01)
            await asyncio.sleep(.1);task.cancel();task.cancel()
            await asyncio.gather(task,return_exceptions=True)
            self.assertIsNotNone(child.returncode)
        with self.assertRaises(service.VideoError):
            await service.run_tool(sys.executable,["-B","-c","import sys; sys.stdout.write('x'*2000000)"],path,10)


if __name__ == "__main__":
    unittest.main()
