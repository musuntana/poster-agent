import csv

import pytest

from scripts.import_abo_sample import _existing_sample, _synthetic_prices, _title, _category

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


def test_existing_sample_is_reused_only_with_manifest_and_attribution(tmp_path):
    manifest = tmp_path / "real_skus.csv"
    with manifest.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["sku_id"])
        writer.writeheader()
        writer.writerow({"sku_id": "ABO-1"})
        writer.writerow({"sku_id": "ABO-2"})
    assert _existing_sample(tmp_path) is None
    (tmp_path / "PUBLIC_DATASET_ATTRIBUTION.md").write_text("attribution", encoding="utf-8")
    sample = _existing_sample(tmp_path)
    assert sample is not None
    assert sample["count"] == 2
    assert sample["reused_existing_sample"] is True
    assert sample["manifest"] == str(manifest.resolve())


def test_existing_sample_rejects_empty_or_oversized_manifest(tmp_path):
    manifest = tmp_path / "real_skus.csv"
    manifest.write_text("sku_id\\n", encoding="utf-8")
    (tmp_path / "PUBLIC_DATASET_ATTRIBUTION.md").write_text("attribution", encoding="utf-8")
    with pytest.raises(ValueError, match="1..100"):
        _existing_sample(tmp_path)
