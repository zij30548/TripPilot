#!/usr/bin/env python3
"""Explicit local-only Linux FFmpeg compatibility check; never changes app limits."""
import argparse
import asyncio
import base64
from io import BytesIO
import json
import os
from pathlib import Path
import platform
import re
import sys
import tempfile
from typing import Any


class CheckError(Exception):
    """Fixed safe diagnostic; no decoder stderr or private paths."""


def linux_only() -> None:
    if sys.platform != "linux" or platform.machine() not in ("x86_64", "amd64"):
        raise CheckError("linux_x64_required")


def parse_version(raw: bytes, expected: str) -> str:
    first = raw.decode("utf-8", errors="replace").splitlines()[0] if raw else ""
    match = re.match(rf"^{expected} version ([A-Za-z0-9.+:_-]{{1,80}})(?:\s|$)", first)
    if not match:
        raise CheckError("unrecognized_executable_version")
    return match[1]


def has_input_fd(raw: bytes) -> bool:
    # Output support alone is not enough: production opens inherited fd: input.
    text = raw.decode("utf-8", errors="replace")
    if "Input:" not in text or "Output:" not in text:
        return False
    inputs = text.split("Input:", 1)[1].split("Output:", 1)[0]
    return "fd" in {line.strip() for line in inputs.splitlines()}


async def preflight(service: Any, root: Path) -> dict[str, Any]:
    try:
        binaries = service.executables()
    except service.VideoError:
        raise CheckError("video_dependencies_unavailable") from None
    empty = root / "empty-input"
    empty.touch(mode=0o600, exist_ok=False)
    values: dict[str, Any] = {}
    for name, executable in zip(("ffprobe", "ffmpeg"), binaries):
        # Fixed production executable selection; no PATH/env/user-supplied binary.
        async with asyncio.timeout(5):
            version = await service.run_tool(executable, ["-hide_banner", "-version"], empty, 65536)
            protocols = await service.run_tool(executable, ["-hide_banner", "-protocols"], empty, 65536)
            demuxer = await service.run_tool(executable, ["-hide_banner", "-h", "demuxer=mov"], empty, 262144)
        if not has_input_fd(protocols):
            raise CheckError("fd_input_protocol_unavailable_video_must_remain_unavailable")
        if any(option not in demuxer for option in (b"enable_drefs", b"use_absolute_path")):
            raise CheckError("required_mov_security_options_unavailable")
        values[name] = {"path": executable, "version": parse_version(version, name), "fd_input": True}
    return values


async def synthetic(service: Any, root: Path) -> dict[str, Any]:
    """Known synthetic colors exercise the exact app validation/fd/decoder path."""
    from PIL import Image

    binaries = service.executables()
    empty = root / "generator-input"
    empty.touch(mode=0o600, exist_ok=False)
    clip = root / "controlled.mp4"
    arguments = [
        "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "1",
        "-protocol_whitelist", "file,pipe", "-f", "lavfi", "-i",
        "color=red:size=320x180:rate=2:duration=12", "-vf",
        "drawbox=color=green:t=fill:enable='gte(t,4)',drawbox=color=blue:t=fill:enable='gte(t,8)'",
        "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", "-fs", "1048576", str(clip),
    ]
    async with asyncio.timeout(10):
        await service.run_tool(binaries[1], arguments, empty, 65536)
    if not clip.is_file() or not 0 < clip.stat().st_size <= 1048576:
        raise CheckError("synthetic_input_invalid")
    os.chmod(clip, 0o600)
    service.validate_container(clip.read_bytes())
    # Same process deadline and exact input flags as production, never a pipe/file
    # fallback when fd is unsupported. No HTTP, quota change or business database.
    async with asyncio.timeout(service.PROCESS_SECONDS):
        result = await service.extract_frames(clip, "deployment-compatibility", binaries)
    if (result.width, result.height, result.rotation_degrees) != (320, 180, 0):
        raise CheckError("synthetic_geometry_mismatch")
    if [frame.target_seconds for frame in result.frames] != [2.0, 6.0, 10.0]:
        raise CheckError("synthetic_sampling_mismatch")
    channels: list[int] = []
    for frame in result.frames:
        data = base64.b64decode(frame.jpeg_base64, validate=True)
        if len(data) > service.MAX_FRAME:
            raise CheckError("synthetic_frame_limit_exceeded")
        with Image.open(BytesIO(data)) as image:
            pixel = image.getpixel((10, 10))
            channels.append(max(range(3), key=lambda index: pixel[index]))
            if image.getexif():
                raise CheckError("synthetic_metadata_not_removed")
    if channels != [0, 1, 2] or len(result.model_dump_json().encode()) > service.MAX_RESPONSE:
        raise CheckError("synthetic_content_mismatch")
    return {"fixture": "synthetic-three-colors", "sample_targets_seconds": [2, 6, 10],
            "frames": 3, "production_extraction": "passed", "external_calls": 0}


async def check(action: str) -> dict[str, Any]:
    linux_only()
    backend = Path(__file__).resolve().parent.parent / "backend"
    sys.path.insert(0, str(backend))
    # This module imports no Settings/.env and does not initialize business data.
    from tooling.travel import video as service

    with tempfile.TemporaryDirectory(prefix="trippilot-video-check-") as folder:
        root = Path(folder)
        os.chmod(root, 0o700)
        dependencies = await preflight(service, root)
        receipt = {"check": "linux-video-compatibility-v1", "platform": "linux-x86_64",
                   "dependencies": dependencies, "external_calls": 0,
                   "scope": "dependency/security-option preflight; not a production upload test"}
        if action == "synthetic":
            receipt["synthetic"] = await synthetic(service, root)
            receipt["scope"] = "synthetic local extraction using unchanged production decoder"
        return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "synthetic"))
    args = parser.parse_args()
    try:
        receipt = asyncio.run(check(args.action))
    except (CheckError, ImportError) as error:
        reason = str(error) if isinstance(error, CheckError) else "python_dependencies_unavailable"
        print(json.dumps({"video": "unavailable", "reason": reason,
                          "other_features": "unaffected", "external_calls": 0}), file=sys.stderr)
        return 3
    except BaseException:
        print(json.dumps({"video": "unavailable", "reason": "compatibility_check_failed",
                          "other_features": "unaffected", "external_calls": 0}), file=sys.stderr)
        return 3
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
