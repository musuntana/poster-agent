"""M5n local style picker: free presets reuse the existing form/API contract."""
from fastapi.testclient import TestClient
from PIL import Image
from io import BytesIO

from app.main import app


client = TestClient(app)


def test_style_picker_is_accessible_and_keeps_manual_style_input():
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    for name in ("蓝色科技风", "粉色浪漫", "绿色自然", "橙色暖阳", "紫色梦幻"):
        assert f'data-style-preset="{name}"' in html
    assert html.count('data-style-preset="') == 5
    assert 'role="group" aria-labelledby="stylePresetLabel"' in html
    assert 'aria-pressed="true"' in html
    assert 'name="style" maxlength="80"' in html
    assert 'syncStylePresetPicker();' in html
    assert "activeReviewRunId && activeReviewApproved" in html


def test_new_local_presets_create_distinct_real_pngs_without_ai(
    tmp_path, monkeypatch, product_bytes
):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path))

    async def forbid_paid_provider(*_args, **_kwargs):
        raise AssertionError("No provider call in local mode")

    monkeypatch.setattr("app.main.generate_ai_background", forbid_paid_provider)
    images = []
    for style in ("橙色暖阳", "紫色梦幻"):
        response = client.post(
            "/api/posters",
            files={"product": ("sku.png", product_bytes, "image/png")},
            data={"title": "新品特惠", "price": "199", "style": style,
                  "background_mode": "local"},
        )
        assert response.status_code == 201, response.text
        poster = response.json()
        assert poster["spec"]["style"] == style
        image = client.get(poster["image_url"])
        assert image.status_code == 200
        with Image.open(BytesIO(image.content)) as png:
            assert png.format == "PNG"
            assert png.size == (1024, 1024)
        images.append(image.content)
    assert images[0] != images[1]
