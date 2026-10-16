"""Bounded local MP4 decoding. No URL input, model, business data or shell."""
import asyncio
import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import signal
from typing import Awaitable, TypeVar

from PIL import Image

from app.schemas.video import VideoFrame, VideoResponse

MAX_UPLOAD = 20 * 1024 * 1024
MAX_FRAME = 512 * 1024
MAX_RESPONSE = 5 * 1024 * 1024
PROCESS_SECONDS = 20
T = TypeVar("T")


class VideoError(Exception):
    def __init__(self, code: str, status: int, message: str):
        super().__init__(message)
        self.code, self.status = code, status


def invalid() -> VideoError:
    return VideoError("invalid_video", 422, "无法读取有效的单视频流 MP4 / H.264；请检查格式、时长、尺寸及文件是否完整。")


async def settled(work: Awaitable[T]) -> T:
    """Repeated cancellation cannot abandon a worker, spawn or cleanup."""
    task = asyncio.ensure_future(work)
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
    if interrupted:
        # Retrieve errors, but preserve cancellation and don't publish a result.
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


def executables() -> tuple[str, str]:
    # Trusted, fixed installation locations only. No client/env/PATH executable.
    for directory in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
        paths = tuple(str(Path(directory, name)) for name in ("ffprobe", "ffmpeg"))
        if all(Path(p).is_file() and os.access(p, os.X_OK) for p in paths):
            return paths  # type: ignore[return-value]
    raise VideoError("video_unavailable", 503, "视频处理依赖未就绪，其他规划功能仍可使用。请安装受支持的 FFmpeg 和 ffprobe。")


def validate_container(data: bytes | bytearray) -> None:
    """MP4 brand + bounded box walk; reject external data references outright."""
    if len(data) < 24 or data[4:8] != b"ftyp" or data[8:12] not in (
        b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42", b"avc1",
    ):
        raise invalid()
    count = 0

    def boxes(start: int, end: int, depth: int = 0) -> None:
        nonlocal count
        if depth > 8:
            raise invalid()
        while start < end:
            count += 1
            if count > 4096 or end - start < 8:
                raise invalid()
            size = int.from_bytes(data[start:start+4], "big")
            kind = data[start+4:start+8]
            header = 8
            if size == 1:
                if end-start < 16:
                    raise invalid()
                size, header = int.from_bytes(data[start+8:start+16], "big"), 16
            if size == 0:
                size = end-start
            stop = start + size
            if size < header or stop > end:
                raise invalid()
            payload = start + header
            if kind in (b"moov", b"trak", b"mdia", b"minf", b"dinf"):
                boxes(payload, stop, depth+1)
            elif kind == b"dref":
                # Exactly one self-contained url entry, never a filename/URL.
                if (stop-payload != 20 or data[payload:payload+4] != b"\0\0\0\0"
                        or int.from_bytes(data[payload+4:payload+8], "big") != 1
                        or data[payload+8:stop] != b"\0\0\0\x0curl \0\0\0\x01"):
                    raise VideoError("external_reference", 422, "不支持包含外部媒体引用的视频。")
            start = stop
    boxes(0, len(data))


async def run_tool(executable: str, arguments: list[str], path: Path, limit: int) -> bytes:
    """Decoder can read only inherited fd 0, not network or other file paths."""
    with path.open("rb") as source:
        spawn = asyncio.create_task(asyncio.create_subprocess_exec(
            executable, *arguments, stdin=source, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, cwd=path.parent, start_new_session=True,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}, limit=65536,
        ))
        process: asyncio.subprocess.Process | None = None

        async def cleanup() -> None:
            try:
                child = await spawn
            except (OSError, asyncio.CancelledError):
                return
            async def drain() -> None:
                if child.stdout:
                    while await child.stdout.read(65536):
                        pass
            drain_task = asyncio.create_task(drain())
            if child.returncode is None:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(child.wait(), .3)
                except TimeoutError:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await child.wait()
            await drain_task

        try:
            process = await asyncio.shield(spawn)
            assert process.stdout is not None
            output = bytearray()
            while chunk := await process.stdout.read(65536):
                if len(output) + len(chunk) > limit:
                    raise VideoError("output_limit", 422, "视频处理输出超过安全上限，未截断为成功结果。")
                output.extend(chunk)
            if await process.wait() != 0:
                raise invalid()
            return bytes(output)
        except OSError:
            raise VideoError("video_unavailable", 503, "视频处理依赖暂不可用，其他功能不受影响。") from None
        finally:
            await settled(cleanup())


def parse_probe(raw: bytes) -> tuple[float, int, int, int]:
    try:
        value = json.loads(raw)
        streams = value["streams"]
        if not isinstance(streams, list) or len(streams) > 8:
            raise invalid()
        videos = [s for s in streams if s.get("codec_type") == "video"]
        if len(videos) != 1:
            raise invalid()
        video = videos[0]
        if video["codec_name"] != "h264" or video.get("disposition", {}).get("attached_pic", 0):
            raise invalid()
        width, height = video["width"], video["height"]
        if (type(width) is not int or type(height) is not int or min(width, height) <= 0
                or max(width, height) > 1920 or width*height > 2073600
                or video.get("sample_aspect_ratio") not in (None, "1:1", "0:1")):
            raise invalid()
        duration = float(video["duration"])
        container_duration = float(value["format"]["duration"])
        if any(not math.isfinite(d) or not 0 < d <= 30 for d in (duration, container_duration)):
            raise invalid()
        if value["format"]["format_name"] != "mov,mp4,m4a,3gp,3g2,mj2":
            raise invalid()
        rotations = [s["rotation"] for s in video.get("side_data_list", []) if "rotation" in s]
        if len(rotations) > 1:
            raise invalid()
        rotation = float(rotations[0] if rotations else video.get("tags", {}).get("rotate", 0))
        if not math.isfinite(rotation) or rotation % 90:
            raise invalid()
        turn = int(rotation) % 360
        return duration, (height if turn % 180 else width), (width if turn % 180 else height), turn
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise invalid() from None


def clean_jpeg(raw: bytes) -> tuple[bytes, int, int, str]:
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != "JPEG" or max(image.size) > 1280:
                raise invalid()
            image.load()
            fresh = Image.new("RGB", image.size)
            fresh.paste(image.convert("RGB"))
            digest = hashlib.sha256(fresh.tobytes()).hexdigest()
            output = io.BytesIO()
            fresh.save(output, format="JPEG", quality=85)
            data = output.getvalue()
            if len(data) > MAX_FRAME:
                raise VideoError("output_limit", 422, "单张画面超过 512 KiB，未返回截断图片。")
            return data, fresh.width, fresh.height, digest
    except (OSError, ValueError):
        raise invalid() from None


INPUT_ARGS = ["-hide_banner", "-loglevel", "error", "-max_alloc", "67108864",
              "-cpucount", "1", "-threads", "1", "-max_pixels", "2073600",
              "-protocol_whitelist", "fd", "-format_whitelist", "mov",
              "-enable_drefs", "0", "-use_absolute_path", "0", "-f", "mov"]


async def extract_frames(path: Path, request_id: str, binaries: tuple[str, str]) -> VideoResponse:
    probe, ffmpeg = binaries
    raw = await run_tool(probe, [*INPUT_ARGS, "-show_entries",
        "format=format_name,duration:stream=codec_type,codec_name,width,height,duration,sample_aspect_ratio:stream_disposition=attached_pic:stream_side_data=rotation:stream_tags=rotate",
        "-of", "json", "-i", "fd:"], path, 65536)
    duration, width, height, rotation = parse_probe(raw)
    count = min(6, math.ceil(duration / 5))
    frames: list[VideoFrame] = []
    hashes: set[str] = set()
    for index in range(1, count+1):
        target = (index-.5)*duration/count
        # Decode actual frames, no fps filter/duplication; relative to first video PTS.
        output = await run_tool(ffmpeg, [*INPUT_ARGS, "-nostdin", "-i", "fd:",
            "-map", "0:v:0", "-an", "-sn", "-dn", "-map_metadata", "-1",
            "-filter_threads", "1", "-vf",
            f"setpts=PTS-STARTPTS,trim=start={target:.9f},scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease,setsar=1",
            "-frames:v", "1", "-fps_mode", "vfr", "-threads", "1", "-c:v", "mjpeg",
            "-pix_fmt", "yuvj420p", "-q:v", "4", "-f", "image2pipe", "pipe:1"], path, MAX_FRAME)
        if not output:
            continue
        jpeg, w, h, digest = await settled(asyncio.to_thread(clean_jpeg, output))
        if digest in hashes:
            continue
        hashes.add(digest)
        frames.append(VideoFrame(index=index, target_seconds=target, width=w, height=h,
                                 jpeg_base64=base64.b64encode(jpeg).decode("ascii")))
    if not frames:
        raise VideoError("no_frames", 422, "未取得可用画面，请选择其他短片；没有复制图片凑数。")
    return VideoResponse(request_id=request_id, duration_seconds=duration, width=width, height=height,
                         rotation_degrees=rotation, sample_count=count, frames=frames)
