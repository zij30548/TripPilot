"""Bounded, memory-only decoding. Never trust MIME/filename or preserve metadata."""
from io import BytesIO
import warnings
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 20_000_000
MAX_SIDE = 2048


class PhotoError(Exception):
    def __init__(self, code: str, status: int, message: str):
        self.code, self.status = code, status
        super().__init__(message)


def normalize_photo(raw: bytes) -> bytes:
    if not raw or len(raw) > MAX_BYTES:
        raise PhotoError("image_size", 413, "请选择一张不超过 5 MiB 的图片。")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw), formats=["JPEG", "PNG", "WEBP"]) as original:
                if original.width * original.height > MAX_PIXELS:
                    raise PhotoError("image_pixels", 413, "图片超过 2000 万像素，请先缩小。")
                if getattr(original, "n_frames", 1) != 1 or getattr(original, "is_animated", False):
                    raise PhotoError("image_animation", 422, "仅支持静态照片，不支持动画。")
                original.verify()
            with Image.open(BytesIO(raw), formats=["JPEG", "PNG", "WEBP"]) as original:
                original.load()  # reject truncated content before conversion
                with ImageOps.exif_transpose(original) as oriented:
                    oriented.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
                    # A fresh RGB canvas prevents EXIF/ICC/comment propagation.
                    with Image.new("RGB", oriented.size, "white") as clean:
                        with oriented.convert("RGBA") as rgba:
                            clean.paste(rgba, mask=rgba.getchannel("A"))
                        with BytesIO() as output:
                            clean.save(output, "JPEG", quality=85)
                            result = output.getvalue()
        if len(result) > MAX_BYTES:
            raise PhotoError("image_size", 413, "处理后的图片过大，请缩小后重试。")
        return result
    except PhotoError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise PhotoError("image_invalid", 422, "无法解码图片；请选择完整的 JPEG、PNG 或静态 WebP。") from None
