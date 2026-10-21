"""M4a multi-format export: real Pillow pixels and FastAPI endpoints.

No network calls, OCR engine or AI model is invoked in these regressions.
"""
from __future__ import annotations

import hashlib
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageChops, ImageOps

from app import storage
from app.channel_exports import MARGIN, PRESETS, render_variant
from app.main import app

client = TestClient(app)


def _source_png() -> bytes:
    img = Image.new("RGB", (1024, 1024), "white")
    # Distinct corner pixels make unexpected cropping detectable by comparisons.
    for xy, color in [
        ((0, 0), (255, 0, 0)), ((1023, 0), (0, 255, 0)),
        ((0, 1023), (0, 0, 255)), ((1023, 1023), (255, 255, 0)),
    ]:
        img.putpixel(xy, color)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


@pytest.mark.parametrize("preset,size", list(PRESETS.items()))
def test_export_size_determinism_and_complete_poster(preset, size):
    source = _source_png()
    first = render_variant(source, preset)
    second = render_variant(source, preset)
    assert first.png == second.png
    assert (first.width, first.height) == size
    assert first.source_sha256 == hashlib.sha256(source).hexdigest()
    with Image.open(io.BytesIO(first.png)) as img:
        assert img.size == size
        assert img.format == "PNG"
        exported = img.convert("RGB")
    x1, y1, x2, y2 = first.content_box
    assert 0 <= x1 < x2 <= size[0]
    assert 0 <= y1 < y2 <= size[1]
    assert x1 >= MARGIN or y1 >= MARGIN
    original = Image.open(io.BytesIO(source)).convert("RGB")
    expected = ImageOps.contain(
        original, (size[0] - 2*MARGIN, size[1] - 2*MARGIN),
        method=Image.Resampling.LANCZOS,
    )
    assert expected.size == (x2 - x1, y2 - y1)
    # Every pixel of the entire square poster is represented in the contain
    # layer. Only background margins are extended, not foreground content.
    diff = ImageChops.difference(exported.crop(first.content_box), expected)
    assert diff.getbbox() is None


def test_rejects_arbitrary_size_and_corrupted_source():
    with pytest.raises(ValueError, match="不支持"):
        render_variant(_source_png(), "9999x9999")
    with pytest.raises(ValueError, match="1024"):
        bad = io.BytesIO()
        Image.new("RGB", (500, 500)).save(bad, "PNG")
        render_variant(bad.getvalue(), "feed_4x5")
    with pytest.raises(OSError):
        render_variant(b"fake-not-image", "feed_4x5")


def _create_poster(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    response = client.post(
        "/api/posters",
        files={"product": ("sku.png", product_bytes, "image/png")},
        data={"title": "电动牙刷促销", "price": "199",
              "original_price": "399", "background_mode": "local"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_exports_use_saved_version_only_without_ai_or_new_version(tmp_path, monkeypatch, product_bytes):
    poster = _create_poster(tmp_path, monkeypatch, product_bytes)
    pid = poster["poster_id"]
    def forbidden(*args, **kwargs):
        raise AssertionError("channel derivative must not call AI")
    monkeypatch.setattr("app.main.generate_ai_background", forbidden)
    source_path = storage.version_path(pid, 1)
    source = source_path.read_bytes()
    initial_meta = (storage.poster_dir(pid) / "meta.json").read_bytes()
    listing = client.get(f"/api/posters/{pid}/versions/1/exports")
    assert listing.status_code == 200
    info = listing.json()
    assert info["reflows_text"] is False
    assert info["automatically_accepted"] is False
    assert {f["id"] for f in info["formats"]} == set(PRESETS)

    for preset, size in PRESETS.items():
        result = client.get(f"/api/posters/{pid}/versions/1/exports/{preset}")
        assert result.status_code == 200
        assert result.headers["content-type"] == "image/png"
        assert result.headers["x-export-mode"] == "contain"
        assert result.headers["x-source-image-sha256"] == hashlib.sha256(source).hexdigest()
        assert result.headers["x-export-size"] == f"{size[0]}x{size[1]}"
        assert "attachment" in result.headers["content-disposition"]
        assert Image.open(io.BytesIO(result.content)).size == size
        assert client.get(f"/api/posters/{pid}/versions/1/exports/{preset}").content == result.content

    assert source_path.read_bytes() == source
    assert (storage.poster_dir(pid) / "meta.json").read_bytes() == initial_meta
    assert storage.view(pid)["versions"] == [1]
    assert not list(storage.poster_dir(pid).glob("**/*export*.png"))


def test_old_version_exports_remain_stable_after_edit(tmp_path, monkeypatch, product_bytes):
    data = _create_poster(tmp_path, monkeypatch, product_bytes)
    pid = data["poster_id"]
    url = f"/api/posters/{pid}/versions/1/exports/feed_4x5"
    first = client.get(url)
    revision = client.post(f"/api/posters/{pid}/revisions", json={"price": "189"})
    assert revision.status_code == 201
    newer = client.get(f"/api/posters/{pid}/versions/2/exports/feed_4x5")
    assert newer.status_code == 200
    assert first.content != newer.content
    assert first.headers["x-source-image-sha256"] != newer.headers["x-source-image-sha256"]
    assert client.get(url).content == first.content
    assert storage.view(pid)["versions"] == [1, 2]


def test_invalid_preset_poster_and_version(tmp_path, monkeypatch, product_bytes):
    data = _create_poster(tmp_path, monkeypatch, product_bytes)
    pid = data["poster_id"]
    assert client.get(f"/api/posters/{pid}/versions/1/exports/super_huge").status_code == 422
    assert client.get(f"/api/posters/{pid}/versions/2/exports/feed_4x5").status_code == 404
    assert client.get("/api/posters/not-valid/versions/1/exports/story_9x16").status_code == 404
    assert client.get("/api/posters/not-valid/versions/1/exports").status_code == 404
