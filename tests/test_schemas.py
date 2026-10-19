import pytest
from pydantic import ValidationError
from app.schemas import PosterSpec, price_text


def test_price_and_fields():
    spec = PosterSpec(title='双十一', price='199.00', original_price='399.00')
    assert price_text(spec.price) == '199'
    assert spec.background_mode == 'local'


def test_reject_wrong_prices():
    with pytest.raises(ValidationError):
        PosterSpec(title='促销', price='199', original_price='99')


def test_reject_empty_title_and_invalid_mode():
    with pytest.raises(ValidationError):
        PosterSpec(title='', price='99')
    with pytest.raises(ValidationError):
        PosterSpec(title='促销', price='99', background_mode='unknown')
