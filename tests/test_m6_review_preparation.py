"""M6 offline package: synthetic tests, never real customers or paid models."""
from __future__ import annotations

import csv
import io
import json

import pytest
from PIL import Image

from app import storage
from scripts import real_sku_batch
from scripts.prepare_m6_review import PreparationError, prepare_package


def _fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "poster_store"))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "ledger.sqlite3"))
    source = tmp_path / "source"
    source.mkdir()
    image = Image.new("RGB", (160, 170), (60, 80, 120))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    (source / "product.png").write_bytes(buffer.getvalue())
    manifest = source / "real_skus.csv"
    with manifest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["sku_id", "product_image", "title", "price"],
        )
        writer.writeheader()
        writer.writerow({
            "sku_id": "sku-test", "product_image": "product.png",
            "title": "测试商品", "price": "199",
        })
    batch_dir = tmp_path / "batch"
    real_sku_batch.run_batch(manifest, batch_dir, apply=True)
    return batch_dir / "batch_import_results.json"


def _csv_rows(path):
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_plan_does_not_render_or_create_any_output(tmp_path, monkeypatch):
    journal = _fixture(tmp_path, monkeypatch)
    output = tmp_path / "m6"
    plan = prepare_package(journal, output)
    assert plan["mode"] == "check"
    assert plan["batch_sku_count"] == 1
    assert plan["expected_review_rows"] == 2
    assert plan["cohort_id"].startswith("m6-")
    assert plan["human_review_performed"] is False
    assert plan["paid_model_calls"] == 0
    assert not output.exists()


def test_apply_exports_both_formats_as_unverified(tmp_path, monkeypatch):
    journal = _fixture(tmp_path, monkeypatch)
    output = tmp_path / "m6"
    def unexpected_ai(*args, **kwargs):
        raise AssertionError("M6 export must not call a paid background model")
    monkeypatch.setattr("app.main.generate_ai_background", unexpected_ai)
    report = prepare_package(journal, output, apply=True)
    registry = _csv_rows(output / "m6_cohort_registry.csv")
    reviews = _csv_rows(output / "m6_output_reviews.csv")
    assert report["review_rows_created"] == 2
    assert report["cohort_id"].startswith("m6-")
    assert registry[0]["cohort_id"] == report["cohort_id"]
    assert {row["cohort_id"] for row in reviews} == {report["cohort_id"]}
    assert report["missing_output_count"] == 0
    assert registry[0]["source_asset_authorization_status"] == "PENDING"
    assert registry[0]["distinct_sku_verified"] == "NO"
    assert registry[0]["authorization_evidence_ref"] == ""
    assert {row["variant_id"] for row in reviews} == {
        "square", "feed_4x5_native",
    }
    assert all(row["overall_accept"] == "UNVERIFIED" for row in reviews)
    assert all(row["reviewer_alias"] == "" for row in reviews)
    assert all(row["reviewed_at_utc"] == "" for row in reviews)
    assert all(row["overall_reason"] == "NOT_REVIEWED" for row in reviews)
    for row in reviews:
        img = output / row["output_image_path"]
        with Image.open(img) as opened:
            assert opened.format == "PNG"
            assert opened.size == (
                (1024, 1024) if row["variant_id"] == "square" else (1080, 1350)
            )
    poster_id = reviews[0]["poster_id"]
    assert storage.view(poster_id)["version"] == 1
    assert report["paid_model_calls"] == 0
    assert report["ocr_inference_performed"] is False


def test_native_failure_still_keeps_expected_row(tmp_path, monkeypatch):
    journal = _fixture(tmp_path, monkeypatch)
    def bad_render(*args, **kwargs):
        raise ValueError("synthetic native overflow")
    monkeypatch.setattr("app.channel_export_routes.render_native_feed", bad_render)
    output = tmp_path / "m6"
    report = prepare_package(journal, output, apply=True)
    reviews = _csv_rows(output / "m6_output_reviews.csv")
    assert len(reviews) == 2
    assert report["missing_output_count"] == 1
    square = next(row for row in reviews if row["variant_id"] == "square")
    native = next(row for row in reviews if row["variant_id"] == "feed_4x5_native")
    assert square["output_image_path"]
    assert native["output_image_path"] == ""
    assert native["output_image_sha256"] == ""
    assert native["overall_accept"] == "UNVERIFIED"
    assert native["overall_reason"] == "EXPORT_FAILED:HTTP_422"


def test_apply_never_overwrites_existing_work(tmp_path, monkeypatch):
    journal = _fixture(tmp_path, monkeypatch)
    output = tmp_path / "m6"
    output.mkdir()
    (output / "m6_output_reviews.csv").write_text("human data", encoding="utf-8")
    with pytest.raises(PreparationError, match="已存在"):
        prepare_package(journal, output, apply=True)
    assert (output / "m6_output_reviews.csv").read_text() == "human data"


def test_refuses_incomplete_batch(tmp_path, monkeypatch):
    journal = _fixture(tmp_path, monkeypatch)
    value = json.loads(journal.read_text(encoding="utf-8"))
    value["completed"] = False
    journal.write_text(json.dumps(value), encoding="utf-8")
    output = tmp_path / "m6"
    with pytest.raises(ValueError, match="尚未完成"):
        prepare_package(journal, output, apply=True)
    assert not output.exists()
