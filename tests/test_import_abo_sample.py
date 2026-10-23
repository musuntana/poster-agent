from scripts.import_abo_sample import _synthetic_prices, _title, _category

def test_synthetic_prices_are_stable_and_original_price_is_higher():
    assert _synthetic_prices("B075X4QMX3") == _synthetic_prices("B075X4QMX3")
    price, original = _synthetic_prices("B075X4QMX3")
    assert float(price) > 0
    assert float(original) > float(price)

def test_titles_are_trimmed_to_poster_contract():
    row = {"item_name": [{"language_tag": "en_US", "value": "A very long public catalog product title " * 3}]}
    assert 1 <= len(_title(row)) <= 35

def test_title_falls_back_to_any_language():
    assert _title({"item_name": [{"language_tag": "zh_CN", "value": "公开商品"}]}) == "公开商品"

def test_category_fallbacks():
    assert _category({"node": [{"node_name": "Home/Furniture"}]}) == "Home/Furniture"
    assert _category({"product_type": [{"value": "SOFA"}]}) == "SOFA"
    assert _category({}) == "unknown"
