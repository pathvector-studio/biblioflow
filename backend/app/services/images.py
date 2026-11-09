"""Validate and re-encode the cropped image of ``POST /papers/{id}/explain-region`` (Phase 3d).

The client cuts the region out of the PDF page on a canvas. The server trusts nothing about it: the data URL is
parsed strictly, the magic bytes must match the declared type, size and pixel count are bounded, Pillow opens it,
and the image is re-encoded as PNG (so EXIF and other metadata are dropped) at no more than ``MAX_SIDE`` px on its
long side. Errors carry a fixed message; the input is never echoed.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import warnings

from PIL import Image

MAX_BYTES = 1_572_864  # 1.5 MiB after base64 decoding
MAX_PIXELS = 25_000_000
MAX_SIDE = 2048  # long side after downscaling
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_PREFIXES = {
    "data:image/png;base64,": _PNG_MAGIC,
    "data:image/jpeg;base64,": _JPEG_MAGIC,
}
# the base64 text of a MAX_BYTES image is at most this long (4 chars per 3 bytes, padded)
_MAX_B64_CHARS = (MAX_BYTES + 2) // 3 * 4

Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class InvalidImage(ValueError):
    """The image is not acceptable. The message is fixed text and never contains the input."""

    def __init__(self) -> None:
        super().__init__("invalid image")


def _decode(data_url: str) -> bytes:
    for prefix, magic in _PREFIXES.items():
        if data_url.startswith(prefix):
            payload = data_url[len(prefix):]
            break
    else:
        raise InvalidImage
    if len(payload) > _MAX_B64_CHARS:
        raise InvalidImage
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise InvalidImage from None
    if len(raw) > MAX_BYTES or not raw.startswith(magic):
        raise InvalidImage
    return raw


def _reencode(raw: bytes) -> bytes:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)  # a warning (over the limit) counts as refusal
        try:
            with Image.open(io.BytesIO(raw)) as probe:
                probe.verify()  # structure check only; the image object is unusable afterwards
            with Image.open(io.BytesIO(raw)) as img:
                img.load()
                if img.width < 1 or img.height < 1:
                    raise InvalidImage
                if max(img.size) > MAX_SIDE:
                    img.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
                has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
                clean = img.convert("RGBA" if has_alpha else "RGB")  # fresh image: no EXIF / ICC / text chunks
                out = io.BytesIO()
                clean.save(out, format="PNG", optimize=True)
        except InvalidImage:
            raise
        except Exception:  # broken data, bombs, truncated files, unsupported modes: all the same refusal
            raise InvalidImage from None
    return out.getvalue()


def prepare_image_sync(data_url: str) -> bytes:
    """data URL -> clean PNG bytes (CPU bound; call through :func:`prepare_image`)."""
    return _reencode(_decode(data_url))


async def prepare_image(data_url: str) -> str:
    """data URL -> ``data:image/png;base64,...`` of the validated, downscaled, re-encoded image.
    Raises :class:`InvalidImage`."""
    png = await asyncio.to_thread(prepare_image_sync, data_url)
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
