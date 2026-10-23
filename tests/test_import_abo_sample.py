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
    manifest.write_text("sku_id\n", encoding="utf-8")
    (tmp_path / "PUBLIC_DATASET_ATTRIBUTION.md").write_text("attribution", encoding="utf-8")
    with pytest.raises(ValueError, match="1..100"):
        _existing_sample(tmp_path)



def test_import_sample_publishes_complete_cohort_atomically(tmp_path, monkeypatch):
    from scripts import import_abo_sample as importer

    monkeypatch.setattr(importer, "_select", lambda limit: [{
        "item_id": "item-1",
        "image_id": "image-1",
        "title": "Example product",
        "category": "Home",
    }])
    monkeypatch.setattr(importer, "_paths", lambda ids: {"image-1": "example.jpg"})

    def fake_download(path, destination):
        destination.write_bytes(b"fixture-image")
        return "a" * 64

    monkeypatch.setattr(importer, "_download", fake_download)
    output = tmp_path / "cohort"
    result = importer.import_sample(output, limit=1)

    assert output.is_dir()
    assert (output / "images" / "ABO-item-1.jpg").read_bytes() == b"fixture-image"
    assert (output / "real_skus.csv").is_file()
    assert (output / "PUBLIC_DATASET_ATTRIBUTION.md").is_file()
    assert result["manifest"] == str(output / "real_skus.csv")
    assert result["attribution"] == str(output / "PUBLIC_DATASET_ATTRIBUTION.md")
    assert not list(tmp_path.glob(".abo-sample-*"))


def test_import_sample_failure_leaves_no_partial_cohort(tmp_path, monkeypatch):
    from scripts import import_abo_sample as importer

    monkeypatch.setattr(importer, "_select", lambda limit: [{
        "item_id": "item-1",
        "image_id": "image-1",
        "title": "Example product",
        "category": "Home",
    }])
    monkeypatch.setattr(importer, "_paths", lambda ids: {"image-1": "example.jpg"})

    def fail_download(path, destination):
        destination.write_bytes(b"partial")
        raise RuntimeError("simulated network failure")

    monkeypatch.setattr(importer, "_download", fail_download)
    output = tmp_path / "cohort"

    with pytest.raises(RuntimeError, match="simulated network failure"):
        importer.import_sample(output, limit=1)

    assert not output.exists()
    assert not list(tmp_path.glob(".abo-sample-*"))


def test_import_sample_refuses_nonempty_destination_without_mutating_it(tmp_path):
    from scripts.import_abo_sample import import_sample

    output = tmp_path / "cohort"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="Refusing to overwrite"):
        import_sample(output, limit=1)

    assert sentinel.read_text(encoding="utf-8") == "keep"



def test_existing_sample_rejects_explicit_limit_mismatch(tmp_path):
    import csv

    from scripts.import_abo_sample import _existing_sample

    manifest = tmp_path / "real_skus.csv"
    with manifest.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["sku_id"])
        writer.writeheader()
        writer.writerow({"sku_id": "ABO-1"})
        writer.writerow({"sku_id": "ABO-2"})
    (tmp_path / "PUBLIC_DATASET_ATTRIBUTION.md").write_text("attribution", encoding="utf-8")

    # Omitting --limit reuses the cohort's actual size.
    assert _existing_sample(tmp_path)["count"] == 2
    # Explicitly asking for a different cohort size must not silently reuse it.
    with pytest.raises(ValueError, match="has 2 SKUs but --limit 20 was requested"):
        _existing_sample(tmp_path, expected_limit=20)
