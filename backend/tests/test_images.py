"""Image checks for explain-region: strict data URL, magic bytes, size and pixel limits, downscale, re-encode."""

import base64
import io
import os

import pytest
from PIL import Image

from app.services import images
from app.services.images import InvalidImage, prepare_image, prepare_image_sync


def encode(raw: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


def make(fmt: str = "PNG", size=(40, 30), mode="RGB", **save) -> bytes:
    out = io.BytesIO()
    Image.new(mode, size, "white" if mode != "L" else 255).save(out, format=fmt, **save)
    return out.getvalue()


def reopen(png: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(png))
    img.load()
    return img


def test_valid_png_and_jpeg_become_png():
    for fmt, mime in (("PNG", "image/png"), ("JPEG", "image/jpeg")):
        out = prepare_image_sync(encode(make(fmt), mime))
        assert out.startswith(b"\x89PNG\r\n\x1a\n")
        assert reopen(out).size == (40, 30)


async def test_prepare_image_returns_a_png_data_url():
    url = await prepare_image(encode(make("JPEG"), "image/jpeg"))
    assert url.startswith("data:image/png;base64,")
    assert reopen(base64.b64decode(url.split(",", 1)[1])).format == "PNG"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not a data url",
        "data:image/gif;base64," + base64.b64encode(make("GIF")).decode(),
        "data:image/png;base64",
        "data:image/png,abc",
        "data:image/png;base64,@@@@",  # not base64
        "data:image/png;base64," + base64.b64encode(make()).decode() + "!",
        "data:image/png;base64,iVBOR w0KGgo=",  # whitespace inside is not strict base64
        "http://example.com/a.png",
        "DATA:image/png;base64," + base64.b64encode(make()).decode(),
    ],
)
def test_bad_data_urls(bad):
    with pytest.raises(InvalidImage):
        prepare_image_sync(bad)


def test_declared_type_must_match_the_magic_bytes():
    with pytest.raises(InvalidImage):
        prepare_image_sync(encode(make("JPEG"), "image/png"))
    with pytest.raises(InvalidImage):
        prepare_image_sync(encode(make("PNG"), "image/jpeg"))


def test_non_image_and_broken_images_are_refused():
    with pytest.raises(InvalidImage):
        prepare_image_sync(encode(b"%PDF-1.7 hello"))
    with pytest.raises(InvalidImage):
        prepare_image_sync(encode(b"\x89PNG\r\n\x1a\n" + b"garbage" * 20))
    with pytest.raises(InvalidImage):
        prepare_image_sync(encode(b"\xff\xd8\xff" + b"garbage" * 20, "image/jpeg"))
    good = make("PNG", size=(200, 200))
    with pytest.raises(InvalidImage):
        prepare_image_sync(encode(good[: len(good) // 2]))  # truncated


def test_oversized_is_refused_before_pillow_sees_it():
    raw = b"\x89PNG\r\n\x1a\n" + os.urandom(images.MAX_BYTES)  # just over the limit once the header counts
    with pytest.raises(InvalidImage):
        prepare_image_sync(encode(raw))
    with pytest.raises(InvalidImage):
        prepare_image_sync("data:image/png;base64," + "A" * (images._MAX_B64_CHARS + 4))


def test_size_limit_is_on_the_decoded_bytes():
    header = b"\x89PNG\r\n\x1a\n"
    over = encode(header + b"\0" * (images.MAX_BYTES - len(header) + 1))
    with pytest.raises(InvalidImage):  # refused for size, and (being junk) would fail later anyway
        prepare_image_sync(over)


@pytest.mark.parametrize("size", [(6000, 6000), (9000, 7000)])
def test_too_many_pixels_is_refused(size):
    raw = make("PNG", size=size, mode="L")
    assert len(raw) < images.MAX_BYTES  # small file, big canvas: a decompression bomb
    with pytest.raises(InvalidImage):
        prepare_image_sync(encode(raw))


def test_large_image_is_downscaled_and_exif_dropped():
    exif = Image.Exif()
    exif[0x010E] = "SECRET-DESCRIPTION"
    raw = make("JPEG", size=(4000, 1000), exif=exif.tobytes())
    assert b"SECRET-DESCRIPTION" in raw  # the input does carry metadata
    out = prepare_image_sync(encode(raw, "image/jpeg"))
    img = reopen(out)
    assert out.startswith(b"\x89PNG\r\n\x1a\n") and img.format == "PNG"
    assert img.size == (2048, 512)  # aspect ratio kept, long side 2048
    assert b"SECRET-DESCRIPTION" not in out and not img.getexif() and "exif" not in img.info


def test_tall_image_and_small_image_sizes():
    assert reopen(prepare_image_sync(encode(make(size=(500, 3000))))).size[1] == 2048
    assert reopen(prepare_image_sync(encode(make(size=(2048, 100))))).size == (2048, 100)  # not enlarged or touched


def test_png_text_chunks_and_transparency():
    from PIL.PngImagePlugin import PngInfo

    meta = PngInfo()
    meta.add_text("Comment", "SECRET-COMMENT")
    out = io.BytesIO()
    Image.new("RGBA", (30, 30), (255, 0, 0, 128)).save(out, format="PNG", pnginfo=meta)
    assert b"SECRET-COMMENT" in out.getvalue()
    clean = prepare_image_sync(encode(out.getvalue()))
    assert b"SECRET-COMMENT" not in clean
    assert reopen(clean).mode == "RGBA" and reopen(clean).getpixel((0, 0)) == (255, 0, 0, 128)


def test_error_message_is_fixed():
    with pytest.raises(InvalidImage) as exc:
        prepare_image_sync("data:image/png;base64,SECRETPAYLOAD==")
    assert str(exc.value) == "invalid image" and "SECRET" not in repr(exc.value)
