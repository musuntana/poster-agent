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
