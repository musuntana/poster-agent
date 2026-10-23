import csv
import hashlib

import pytest

from scripts.import_abo_sample import _existing_sample, _synthetic_prices, _title, _category



def _write_valid_existing_sample(output_dir, count=2):
    output_dir.mkdir(parents=True, exist_ok=True)
    image_dir = output_dir / "images"
    image_dir.mkdir(exist_ok=True)
    rows = []
    attribution_rows = []
    for index in range(1, count + 1):
        item_id = f"item-{index}"
        sku = f"ABO-{item_id}"
        image_name = f"{sku}.jpg"
        image_bytes = f"fixture-image-{index}".encode()
        (image_dir / image_name).write_bytes(image_bytes)
        image_sha = hashlib.sha256(image_bytes).hexdigest()
        rows.append({
            "sku_id": sku,
            "product_image": f"images/{image_name}",
            "title": f"Product {index}",
            "price": "19.90",
        })
        attribution_rows.append(
            f"| {item_id} | Product {index} | Home | image-{index} | {image_sha} |"
        )

    manifest = output_dir / "real_skus.csv"
    with manifest.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=["sku_id", "product_image", "title", "price"]
        )
        writer.writeheader()
        writer.writerows(rows)
    manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
    attribution = output_dir / "PUBLIC_DATASET_ATTRIBUTION.md"
    attribution.write_text(
        "# Dataset attribution and limitations\n\n"
        f"Manifest SHA-256: `{manifest_sha}`\n\n"
        "| Item ID | Title | Category | Main image ID | Local image SHA-256 |\n"
        "|---|---|---|---|---|\n"
        + "\n".join(attribution_rows)
        + "\n",
        encoding="utf-8",
    )
    return manifest, attribution

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
    manifest, attribution = _write_valid_existing_sample(tmp_path, count=2)
    original_attribution = attribution.read_text(encoding="utf-8")
    attribution.unlink()
    assert _existing_sample(tmp_path) is None
    attribution.write_text(original_attribution, encoding="utf-8")

    sample = _existing_sample(tmp_path)
    assert sample is not None
    assert sample["count"] == 2
    assert sample["reused_existing_sample"] is True
    assert sample["manifest"] == str(manifest.resolve())
    assert sample["manifest_sha256"] == hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert sample["image_hashes_recorded"] == 2


def test_existing_sample_rejects_empty_or_oversized_manifest(tmp_path):
    manifest = tmp_path / "real_skus.csv"
    manifest.write_text("sku_id,product_image,title,price\n", encoding="utf-8")
    (tmp_path / "PUBLIC_DATASET_ATTRIBUTION.md").write_text("attribution", encoding="utf-8")
    with pytest.raises(ValueError, match="1..100"):
        _existing_sample(tmp_path)

    with manifest.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=["sku_id", "product_image", "title", "price"]
        )
        writer.writeheader()
        writer.writerows({
            "sku_id": f"ABO-item-{index}",
            "product_image": f"images/ABO-item-{index}.jpg",
            "title": f"Product {index}",
            "price": "19.90",
        } for index in range(101))
    with pytest.raises(ValueError, match="1..100"):
        _existing_sample(tmp_path)


def test_existing_sample_rejects_manifest_tampering(tmp_path):
    manifest, _ = _write_valid_existing_sample(tmp_path, count=2)
    content = manifest.read_text(encoding="utf-8")
    manifest.write_text(content.replace("19.90", "1.00", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest does not match its attribution digest"):
        _existing_sample(tmp_path)


def test_existing_sample_rejects_source_image_tampering(tmp_path):
    _, _ = _write_valid_existing_sample(tmp_path, count=2)
    (tmp_path / "images" / "ABO-item-1.jpg").write_bytes(b"changed image bytes")
    with pytest.raises(ValueError, match="source image SHA-256 differs from attribution"):
        _existing_sample(tmp_path)


def test_existing_sample_rejects_image_path_escape(tmp_path):
    manifest, attribution = _write_valid_existing_sample(tmp_path, count=2)
    rows = []
    with manifest.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    rows[0]["product_image"] = "../../outside.jpg"
    with manifest.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=["sku_id", "product_image", "title", "price"]
        )
        writer.writeheader()
        writer.writerows(rows)
    new_digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    attribution_text = attribution.read_text(encoding="utf-8")
    old_digest = attribution_text.split("Manifest SHA-256: `", 1)[1].split("`", 1)[0]
    attribution.write_text(
        attribution_text.replace(
            f"Manifest SHA-256: `{old_digest}`",
            f"Manifest SHA-256: `{new_digest}`",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing or escapes"):
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
        raw = b"fixture-image"
        destination.write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    monkeypatch.setattr(importer, "_download", fake_download)
    output = tmp_path / "cohort"
    result = importer.import_sample(output, limit=1)

    assert output.is_dir()
    assert (output / "images" / "ABO-item-1.jpg").read_bytes() == b"fixture-image"
    assert (output / "real_skus.csv").is_file()
    assert (output / "PUBLIC_DATASET_ATTRIBUTION.md").is_file()
    assert "Manifest SHA-256: `" in (output / "PUBLIC_DATASET_ATTRIBUTION.md").read_text(encoding="utf-8")
    assert result["manifest_sha256"] == hashlib.sha256((output / "real_skus.csv").read_bytes()).hexdigest()
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
    _write_valid_existing_sample(tmp_path, count=2)

    # Omitting --limit reuses the cohort's actual size.
    assert _existing_sample(tmp_path)["count"] == 2
    # Explicitly asking for a different cohort size must not silently reuse it.
    with pytest.raises(ValueError, match="has 2 SKUs but --limit 20 was requested"):
        _existing_sample(tmp_path, expected_limit=20)
