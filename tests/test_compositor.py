from io import BytesIO
from PIL import Image
from app.compositor import compose, local_background
from app.schemas import PosterSpec


def test_render_is_png_and_square():
    product = Image.new('RGBA', (300, 500), (100, 180, 200, 255))
    spec = PosterSpec(title='双十一', price='199', original_price='399')
    result = compose(spec, product, None, local_background('蓝色科技风'))
    image = Image.open(BytesIO(result))
    assert image.format == 'PNG'
    assert image.size == (1024, 1024)


def test_price_edit_changes_only_composition():
    product = Image.new('RGBA', (300, 500), (100, 180, 200, 255))
    bg = local_background('蓝色科技风')
    first = compose(PosterSpec(title='双十一', price='199'), product, None, bg)
    second = compose(PosterSpec(title='双十一', price='189'), product, None, bg)
    assert first != second
    assert bg.getpixel((0, 0)) == local_background('蓝色科技风').getpixel((0, 0))


def test_local_background_color_presets_are_distinct_and_deterministic():
    styles = ["蓝色科技风", "粉色浪漫", "绿色自然", "橙色暖阳", "紫色梦幻"]
    samples = [local_background(style).getpixel((0, 0)) for style in styles]
    assert len(set(samples)) == len(styles)
    for style, sample in zip(styles, samples):
        assert local_background(style).size == (1024, 1024)
        assert local_background(style).getpixel((0, 0)) == sample


def test_local_background_uses_free_preset_without_network(monkeypatch):
    def forbid_network(*args, **kwargs):
        raise AssertionError("Local style rendering must not call paid providers")
    monkeypatch.setattr("app.image_provider.generate_ai_background", forbid_network)
    assert local_background("橙色暖阳").mode == "RGB"
    assert local_background("紫色梦幻").mode == "RGB"


def test_square_rejects_overlong_subtitle_without_silent_clipping():
    import pytest

    product = Image.new("RGBA", (300, 500), (100, 180, 200, 255))
    spec = PosterSpec(title="双十一", subtitle="超长副标题" * 9, price="199")
    with pytest.raises(ValueError, match="副标题"):
        compose(spec, product, None, local_background("蓝色科技风"))


def test_square_rejects_unfittable_cta(monkeypatch):
    import pytest
    from app import compositor

    product = Image.new("RGBA", (300, 500), (100, 180, 200, 255))
    spec = PosterSpec(title="双十一", price="199", cta="立即购买")
    original = compositor._fit_text

    def too_wide(draw, text, max_width, max_size=64, min_size=23):
        if text == spec.cta:
            return compositor.get_font(105)
        return original(draw, text, max_width, max_size, min_size)

    monkeypatch.setattr(compositor, "_fit_text", too_wide)
    with pytest.raises(ValueError, match="CTA"):
        compose(spec, product, None, local_background("蓝色科技风"))
