"""M5a: fail closed on compressed-image bombs and remove upload metadata.

These tests construct tiny PNG headers that declare huge dimensions so CI
never needs to allocate or decompress a 25-million-pixel image.
"""
from __future__ import annotations

import io
import struct
import zlib

from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin

from app.main import MAX_DECODED_PIXELS, MAX_IMAGE_EDGE, MAX_UPLOAD, app
from app import storage

client = TestClient(app)
INPUT = {"title": "电动牙刷", "price": "199", "background_mode": "local"}


def _fake_png_dimensions(original: bytes, width: int, height: int) -> bytes:
    """Change only the PNG IHDR dimensions and update the IHDR CRC.

    This synthetic file deliberately has no matching IDAT payload; the server
    must reject it based on header dimensions *before* loading pixels.
    """
    raw = bytearray(original)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    assert raw[12:16] == b"IHDR"
    raw[16:24] = struct.pack(">II", width, height)
    raw[29:33] = struct.pack(">I", zlib.crc32(raw[12:29]) & 0xFFFFFFFF)
    return bytes(raw)


def _png_with_metadata() -> bytes:
    info = PngImagePlugin.PngInfo()
    info.add_text("Description", "private-customer-identifier-M5a")
    info.add_text("Campaign", "not-for-export-M5a")
    output = io.BytesIO()
    Image.new("RGB", (128, 96), (30, 70, 110)).save(output, "PNG", pnginfo=info)
    return output.getvalue()


def _create(files, *, data=None):
    return client.post("/api/posters", files=files, data=data or INPUT)


def test_rejects_decompression_bomb_before_ever_invoking_paid_api(
    tmp_path, monkeypatch, product_bytes,
):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path))
    # A 5000x5000 image is only a few hundred bytes here. It exceeds the
    # decoded-pixel limit and can otherwise allocate ~100MB+ when decompressed.
    massive = _fake_png_dimensions(product_bytes, 5000, 5000)
    assert len(massive) < MAX_UPLOAD
    async def forbid(_style):
        raise AssertionError("Must reject invalid source BEFORE any paid API call")
    monkeypatch.setattr("app.main.generate_ai_background", forbid)
    response = _create(
        {"product": ("bomb.png", massive, "image/png")},
        data={**INPUT, "background_mode": "ai"},
    )
    assert response.status_code == 413, response.text
    assert not list(tmp_path.iterdir())


def test_rejects_oversized_edge_even_when_total_pixels_are_safe(
    tmp_path, monkeypatch, product_bytes,
):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path))
    raw = _fake_png_dimensions(product_bytes, MAX_IMAGE_EDGE + 1, 800)
    assert (MAX_IMAGE_EDGE + 1) * 800 < MAX_DECODED_PIXELS
    response = _create({"product": ("banner.png", raw, "image/png")})
    assert response.status_code == 413
    assert not list(tmp_path.iterdir())


def test_logo_has_same_pre_decode_protection(
    tmp_path, monkeypatch, product_bytes,
):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path))
    huge_logo = _fake_png_dimensions(product_bytes, 5000, 5000)
    response = _create({
        "product": ("sku.png", product_bytes, "image/png"),
        "logo": ("logo.png", huge_logo, "image/png"),
    })
    assert response.status_code == 413
    assert not list(tmp_path.iterdir())


def test_retains_original_10mb_compressed_input_cap(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path))
    response = _create({
        "product": ("too-large.png", b"x" * (MAX_UPLOAD + 1), "image/png")
    })
    assert response.status_code == 413
    assert not list(tmp_path.iterdir())


def test_product_and_logo_text_metadata_removed_before_disk_storage(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path))
    raw = _png_with_metadata()
    assert b"private-customer-identifier-M5a" in raw
    response = _create({
        "product": ("private.png", raw, "image/png"),
        "logo": ("private-logo.png", raw, "image/png"),
    })
    assert response.status_code == 201, response.text
    path = storage.poster_dir(response.json()["poster_id"])
    for name in ("product", "logo"):
        output = (path / "assets" / f"{name}.png").read_bytes()
        assert b"private-customer-identifier-M5a" not in output
        assert b"not-for-export-M5a" not in output
        with Image.open(io.BytesIO(output)) as image:
            assert image.size == (128, 96)
            assert "Description" not in image.info
            assert "Campaign" not in image.info


def test_exif_orientation_is_applied_then_removed(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path))
    exif = Image.Exif()
    exif[274] = 6  # rotate 90 degrees clockwise before display
    image = Image.new("RGB", (100, 70), (200, 130, 60))
    stream = io.BytesIO()
    image.save(stream, format="JPEG", exif=exif)
    response = _create({
        "product": ("camera.jpg", stream.getvalue(), "image/jpeg")
    })
    assert response.status_code == 201, response.text
    stored = storage.poster_dir(response.json()["poster_id"]) / "assets" / "product.png"
    with Image.open(stored) as product:
        assert product.size == (70, 100)
        assert product.getexif().get(274) is None
        assert "exif" not in product.info
