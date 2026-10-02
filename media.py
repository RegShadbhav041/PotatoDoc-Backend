"""Image normalisation for profile photos and notice galleries.

Pillow only — no torch, no FastAPI app imports — so routers and unit tests can
use it without loading the ML weights. Every upload is re-encoded to JPEG:
that strips EXIF (including GPS location), guarantees one known format and
bounds how much a phone camera dump can bloat the SQLite file.
"""
import base64
import io

from fastapi import HTTPException
from PIL import Image, ImageOps

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_TYPES = ("image/jpeg", "image/jpg", "image/png", "image/webp")


def normalise(raw: bytes, content_type: str, *, max_edge: int, quality: int):
    """Validate and resize one uploaded image -> (jpeg_bytes, "image/jpeg").

    `max_edge` is the longest side in pixels (profile 512, notice 1280).
    Raises HTTPException 400/422 with a plain-string detail, matching the
    error convention the mobile app renders directly.
    """
    if not raw:
        raise HTTPException(400, "No image received.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(422, "That image is too large (max 5 MB).")
    if (content_type or "").strip().lower() not in ALLOWED_TYPES:
        raise HTTPException(400, "Send a JPEG, PNG or WebP image.")
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        raise HTTPException(400, "That file is not a valid image.")
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue(), "image/jpeg"


def to_data_uri(data: bytes, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode()
