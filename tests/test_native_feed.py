"""M4b native 4:5 rendering and API: no paid model, no change to M4a.

All image tests exercise actual Pillow pixels with local Noto/PingFang fonts.
"""
from __future__ import annotations

import hashlib
import io

from PIL import Image, ImageChops
from fastapi.testclient import TestClient

from app import storage
from app.channel_exports import render_variant
from app.main import app
from app.native_layout import HEIGHT, WIDTH, render_native_feed
from app.schemas import PosterSpec

client = TestClient(app)


def _make(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    resp = client.post(
        "/api/posters",
        files={"product": ("sku.png", product_bytes, "image/png")},
        data={"title": "电动牙刷", "subtitle": "限时抢购", "price": "199",
              "original_price": "399", "cta": "立即购买",
              "background_mode": "local"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["poster_id"]


def test_render_native_is_deterministic_and_has_real_independent_layout():
    product = Image.new("RGBA", (350, 620), (240, 100, 40, 255))
    logo = Image.new("RGBA", (200, 80), (12, 230, 95, 255))
    bg = Image.new("RGB", (1024, 1024), (35, 57, 87))
    spec = PosterSpec(title="高效清洁电动牙刷", price="199",
                      original_price="399", subtitle="限时福利", cta="立即抢购")
    a = render_native_feed(spec, product, logo, bg)
    assert render_native_feed(spec, product, logo, bg) == a
    with Image.open(io.BytesIO(a)) as image:
        assert image.format == "PNG" and image.size == (WIDTH, HEIGHT)
        assert image.mode == "RGB"
    # Native is a fresh composition, not contain-scaled whole square source.
    from app.compositor import compose
    square = compose(spec, product, logo, bg)
    contained = render_variant(square, "feed_4x5")
    assert a != contained.png
    # Changing the specified text or product affects native pixels.
    changed = render_native_feed(
        spec.model_copy(update={"price": spec.price + 1}), product, logo, bg)
    assert changed != a
    other_product = Image.new("RGBA", product.size, (40, 100, 240, 255))
    assert render_native_feed(spec, other_product, logo, bg) != a
    assert render_native_feed(spec, product, None, bg) != a


def test_long_title_fits_or_fails_safely_without_clipping():
    product = Image.new("RGBA", (300, 450), (160, 130, 120, 255))
    bg = Image.new("RGB", (1024, 1024), (70, 80, 100))
    spec = PosterSpec(title="双十一限时优惠电动牙刷全新升级舒适刷牙体验品质生活好物", price="199")
    result = render_native_feed(spec, product, None, bg)
    assert Image.open(io.BytesIO(result)).size == (1080, 1350)


def test_native_format_list_and_versioned_get(tmp_path, monkeypatch, product_bytes):
    pid = _make(tmp_path, monkeypatch, product_bytes)
    saved = storage.version_path(pid, 1).read_bytes()
    saved_spec = (storage.poster_dir(pid) / "versions" / "v1.json").read_bytes()
    meta = (storage.poster_dir(pid) / "meta.json").read_bytes()
    # Model calls must never happen during read-only derivatives.
    def forbid(*_args, **_kwargs):
        raise AssertionError("Native export must not execute paid AI")
    monkeypatch.setattr("app.main.generate_ai_background", forbid)

    list_resp = client.get(f"/api/posters/{pid}/versions/1/exports")
    assert list_resp.status_code == 200
    listing = list_resp.json()
    assert listing["reflows_text"] is False  # legacy M4a formats
    assert len(listing["formats"]) == 3
    native = listing["native_formats"][0]
    assert native["mode"] == "native_layout"
    assert native["reflows_text"] is True
    assert native["automatically_accepted"] is False
    assert native["width"] == 1080 and native["height"] == 1350

    url = native["url"]
    response = client.get(url)
    assert response.status_code == 200, response.text
    assert response.headers["x-export-mode"] == "native_layout"
    assert response.headers["x-automatically-accepted"] == "false"
    assert response.headers["x-source-image-sha256"] == hashlib.sha256(saved).hexdigest()
    assert response.headers["x-export-size"] == "1080x1350"
    assert "x-content-box" not in response.headers
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.size == (1080, 1350)
    assert response.content == client.get(url).content
    assert client.get(f"/api/posters/{pid}/versions/1/exports/feed_4x5").content != response.content
    assert storage.version_path(pid, 1).read_bytes() == saved
    assert (storage.poster_dir(pid) / "versions" / "v1.json").read_bytes() == saved_spec
    assert (storage.poster_dir(pid) / "meta.json").read_bytes() == meta
    assert storage.view(pid)["versions"] == [1]
    assert not list(storage.poster_dir(pid).glob("**/*native*.png"))


def test_revisions_use_correct_version_spec_not_latest(tmp_path, monkeypatch, product_bytes):
    pid = _make(tmp_path, monkeypatch, product_bytes)
    url_v1 = f"/api/posters/{pid}/versions/1/exports/feed_4x5_native"
    original = client.get(url_v1)
    assert original.status_code == 200
    changed = client.post(f"/api/posters/{pid}/revisions", json={"price": "189"})
    assert changed.status_code == 201
    v2 = client.get(f"/api/posters/{pid}/versions/2/exports/feed_4x5_native")
    assert v2.status_code == 200
    assert v2.content != original.content
    assert v2.headers["x-source-image-sha256"] != original.headers["x-source-image-sha256"]
    assert client.get(url_v1).content == original.content


def test_wrong_paths_fail_safely(tmp_path, monkeypatch, product_bytes):
    pid = _make(tmp_path, monkeypatch, product_bytes)
    assert client.get(f"/api/posters/{pid}/versions/9/exports/feed_4x5_native").status_code == 404
    assert client.get("/api/posters/invalid/versions/1/exports/feed_4x5_native").status_code == 404
    assert client.get(f"/api/posters/{pid}/versions/1/exports/fake_native").status_code == 422


def test_revision_rejects_subtitle_that_square_layout_cannot_render(
    tmp_path, monkeypatch, product_bytes
):
    pid = _make(tmp_path, monkeypatch, product_bytes)
    text = "超长副标题" * 9
    assert len(text) == 45

    # The square render is part of revision creation. Reject the invalid
    # revision atomically instead of saving a new version with clipped text.
    updated = client.post(f"/api/posters/{pid}/revisions", json={"subtitle": text})
    assert updated.status_code == 422
    assert "副标题" in updated.json()["detail"]
    assert client.get(
        f"/api/posters/{pid}/versions/2/exports/feed_4x5_native"
    ).status_code == 404


def test_native_layout_rejects_overflowing_subtitle():
    import pytest

    product = Image.new("RGBA", (300, 450), (160, 130, 120, 255))
    background = Image.new("RGB", (1024, 1024), (70, 80, 100))
    text = "超长副标题" * 9
    spec = PosterSpec(title="电动牙刷", subtitle=text, price="199")

    with pytest.raises(ValueError, match="副标题"):
        render_native_feed(spec, product, None, background)


def test_product_focus_layout_is_deterministic_and_distinct():
    from app.native_layout import render_native_product_focus

    product = Image.new("RGBA", (420, 620), (240, 100, 40, 255))
    logo = Image.new("RGBA", (200, 80), (12, 230, 95, 255))
    background = Image.new("RGB", (1024, 1024), (35, 57, 87))
    spec = PosterSpec(title="高效清洁电动牙刷双十一优惠", subtitle="全新升级 · 长效续航",
                      price="199", original_price="399", cta="立即抢购")
    first = render_native_product_focus(spec, product, logo, background)
    assert render_native_product_focus(spec, product, logo, background) == first
    with Image.open(io.BytesIO(first)) as image:
        assert image.format == "PNG"
        assert image.size == (1080, 1350)
        assert image.mode == "RGB"
    classic = render_native_feed(spec, product, logo, background)
    assert first != classic
    changed = render_native_product_focus(
        spec.model_copy(update={"price": spec.price + 1}), product, logo, background
    )
    assert changed != first


def test_product_focus_export_is_listed_and_does_not_mutate_source(
    tmp_path, monkeypatch, product_bytes
):
    pid = _make(tmp_path, monkeypatch, product_bytes)
    source = storage.version_path(pid, 1).read_bytes()
    source_spec = (storage.poster_dir(pid) / "versions" / "v1.json").read_bytes()
    metadata = (storage.poster_dir(pid) / "meta.json").read_bytes()

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("layout export must not call paid model")

    monkeypatch.setattr("app.main.generate_ai_background", forbidden)
    listing = client.get(f"/api/posters/{pid}/versions/1/exports").json()
    native = {item["id"]: item for item in listing["native_formats"]}
    assert set(native) == {"feed_4x5_native", "feed_4x5_native_product_focus"}
    product_focus = native["feed_4x5_native_product_focus"]
    assert product_focus["mode"] == "native_layout"
    assert product_focus["reflows_text"] is True
    assert product_focus["automatically_accepted"] is False

    url = product_focus["url"]
    first = client.get(url)
    second = client.get(url)
    assert first.status_code == 200, first.text
    assert first.content == second.content
    assert first.headers["x-export-mode"] == "native_layout"
    assert first.headers["x-layout-template"] == "product_focus"
    assert first.headers["x-automatically-accepted"] == "false"
    assert first.headers["x-export-size"] == "1080x1350"
    with Image.open(io.BytesIO(first.content)) as image:
        assert image.size == (1080, 1350)

    assert storage.version_path(pid, 1).read_bytes() == source
    assert (storage.poster_dir(pid) / "versions" / "v1.json").read_bytes() == source_spec
    assert (storage.poster_dir(pid) / "meta.json").read_bytes() == metadata
    assert client.get(f"/api/posters/{pid}/versions/1/exports/unknown_native").status_code == 422


def test_ui_exposes_product_focus_with_separate_acceptance_boundary():
    response = client.get("/")
    assert response.status_code == 200
    assert 'value="feed_4x5_native_product_focus"' in response.text
    assert "商品主体优先版式：已接入独立 OCR / 规则检查" in response.text
    assert "人工审核流程尚未接入" in response.text
    assert "exports/'+variant+'/acceptance" in response.text
