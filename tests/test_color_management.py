"""M5b ICC color tests use actual Pillow/LittleCMS transforms, no AI or OCR."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageCms

from app import storage
from app.color_management import InvalidColorProfile, MAX_ICC_BYTES, normalize_upload_color
from app.main import app

client = TestClient(app)
FORM = {"title": "品牌商品", "price": "199", "background_mode": "local"}


def _srgb() -> bytes:
    # Official Pillow-generated ICC bytes; never external binary/font fixture.
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _image_png(*, profile: bytes | None, mode: str = "RGBA") -> bytes:
    image = Image.new(mode, (128, 96), (47, 115, 207, 180) if mode == "RGBA"
                      else (47, 115, 207))
    image.putpixel((0, 0), (230, 75, 10, 0) if mode == "RGBA" else (230, 75, 10))
    image.putpixel((64, 32), (15, 225, 90, 97) if mode == "RGBA" else (15, 225, 90))
    buf = io.BytesIO()
    settings = {"format": "PNG"}
    if profile is not None:
        settings["icc_profile"] = profile
    image.save(buf, **settings)
    return buf.getvalue()


def _stored_image(tmp_path, monkeypatch, product: bytes, logo: bytes | None = None):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path))
    files = {"product": ("sku.png", product, "image/png")}
    if logo is not None:
        files["logo"] = ("logo.png", logo, "image/png")
    res = client.post("/api/posters", files=files, data=FORM)
    assert res.status_code == 201, res.text
    return storage.poster_dir(res.json()["poster_id"]) / "assets"


def test_tagged_srgb_and_untagged_look_the_same_after_normalization(
    tmp_path, monkeypatch,
):
    tagged = _image_png(profile=_srgb())
    untagged = _image_png(profile=None)
    directory = _stored_image(tmp_path, monkeypatch, tagged, untagged)
    product = Image.open(directory / "product.png")
    logo = Image.open(directory / "logo.png")
    assert product.mode == "RGBA" and logo.mode == "RGBA"
    assert product.size == logo.size == (128, 96)
    assert "icc_profile" not in product.info
    assert "icc_profile" not in logo.info
    assert "exif" not in product.info
    # Source is tagged sRGB, destination is sRGB; color shifts should be
    # at most a couple of 8-bit rounding levels.
    for (x, y) in ((0, 0), (64, 32), (127, 95)):
        p, l = product.getpixel((x, y)), logo.getpixel((x, y))
        assert p[3] == l[3]
        assert max(abs(a-b) for a, b in zip(p[:3], l[:3])) <= 3
    assert product.getpixel((0, 0))[3] == 0
    assert product.getpixel((64, 32))[3] == 97


def test_invalid_icc_is_rejected_before_paid_background(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path))
    async def forbidden(_):
        raise AssertionError("Never call paid model on an invalid ICC upload")
    monkeypatch.setattr("app.main.generate_ai_background", forbidden)
    damaged = _image_png(profile=b"not-a-valid-icc-profile")
    response = client.post(
        "/api/posters",
        data={**FORM, "background_mode": "ai"},
        files={"product": ("bad-color.png", damaged, "image/png")},
    )
    assert response.status_code == 422, response.text
    assert "ICC" in response.json()["detail"]
    assert not list(tmp_path.iterdir())


def test_invalid_logo_icc_rejects_entire_request(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path))
    valid = _image_png(profile=_srgb())
    invalid = _image_png(profile=b"broken-logo-icc")
    response = client.post(
        "/api/posters",
        data=FORM,
        files={
            "product": ("sku.png", valid, "image/png"),
            "logo": ("logo.png", invalid, "image/png"),
        },
    )
    assert response.status_code == 422
    assert not list(tmp_path.iterdir())


def test_rejects_oversized_icc_without_attempting_color_transform(monkeypatch):
    image = Image.new("RGB", (128, 96), (10, 20, 30))
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Oversize ICC must be rejected before LittleCMS")
    monkeypatch.setattr("app.color_management.ImageCms.profileToProfile", forbidden)
    with pytest.raises(InvalidColorProfile, match="过大"):
        normalize_upload_color(image, b"x" * (MAX_ICC_BYTES + 1))


def test_unknown_profile_is_not_silently_ignored():
    image = Image.new("RGB", (128, 96), (10, 20, 30))
    with pytest.raises(InvalidColorProfile, match="为空"):
        normalize_upload_color(image, b"")
    with pytest.raises(InvalidColorProfile, match="损坏"):
        normalize_upload_color(image, b"not-a-profile")


def test_untagged_rgb_remains_supported_and_metadata_free():
    image = Image.new("RGBA", (128, 96), (99, 73, 22, 103))
    image.info["private"] = "should-not-survive"
    result = normalize_upload_color(image, None)
    assert result.mode == "RGBA"
    assert result.getpixel((5, 5)) == (99, 73, 22, 103)
    assert result.info == {}


def test_rgb_image_with_mismatched_profile_fails_closed():
    image = Image.new("RGB", (128, 96), (20, 55, 90))
    # LAB profile declares an incompatible pixel color space for RGB data.
    wrong = ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB")).tobytes()
    with pytest.raises(InvalidColorProfile, match="不匹配"):
        normalize_upload_color(image, wrong)
