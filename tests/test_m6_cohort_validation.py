"""M6 local evidence gate: deterministic fixtures only, no real SKU data."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from app import storage
from scripts import real_sku_batch as batch
from scripts.validate_m6_cohort import (
    M6ValidationError,
    _safe_report_destination,
    validate_bundle,
)


REGISTRY_FIELDS = [
    "cohort_id", "sku_id", "product_category", "product_family_id",
    "variant_or_packaging_condition", "source_asset_authorization_status",
    "authorization_evidence_ref", "logo_authorization_status",
    "source_image_sha256", "distinct_sku_verified",
    "duplicate_source_image_reviewed", "reviewer_alias", "notes",
]
REVIEW_FIELDS = [
    "cohort_id", "sku_id", "poster_id", "source_version", "variant_id",
    "output_image_path", "source_product_sha256", "request_sha256",
    "spec_sha256", "output_image_sha256", "source_square_image_sha256",
    "reviewer_alias", "sku_integrity", "text_legibility", "title",
    "subtitle", "price", "original_price", "price_position",
    "original_price_position", "cta", "composition", "overall_accept",
    "overall_reason", "reviewed_at_utc",
]
NATIVE_MANIFEST_FIELDS = [
    "poster_id", "version", "sku_id", "status", "definite_labels",
    "ocr_text_labels", "unsure_labels", "product_sha256",
    "native_image_sha256", "action",
]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _png(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, format="PNG")


def _bundle(tmp_path, monkeypatch, count: int = 2) -> dict:
    store = tmp_path / "poster_store"
    monkeypatch.setenv("POSTER_DATA_DIR", str(store))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    manifest_rows = []
    for i in range(count):
        sku = f"sku-{i + 1:02d}"
        _png(source_dir / f"{sku}.png", (128, 160), (30 + i, 80, 120))
        manifest_rows.append({
            "sku_id": sku,
            "product_image": f"{sku}.png",
            "title": f"测试商品{i + 1}",
            "price": str(99 + i),
            "original_price": "",
            "subtitle": "",
            "cta": "立即抢购",
            "style": "蓝色科技风",
            "logo_image": "",
        })
    manifest = source_dir / "real_skus.csv"
    _write_csv(
        manifest,
        ["sku_id", "product_image", "title", "price", "original_price",
         "subtitle", "cta", "style", "logo_image"],
        manifest_rows,
    )
    output_dir = tmp_path / "m5g"
    batch.run_batch(manifest, output_dir, apply=True)
    journal_path = output_dir / "batch_import_results.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    cohort_id = "m6-" + _sha(journal_path)[:12]

    registry_rows = []
    review_rows = []
    native_manifest_rows = []
    for i, item in enumerate(manifest_rows):
        sku = item["sku_id"]
        journal_row = journal["rows"][sku]
        poster_id = journal_row["poster_id"]
        version = 1
        square_path = storage.version_path(poster_id, version)
        square_sha = _sha(square_path)
        spec_path = storage.poster_dir(poster_id) / "versions" / "v1.json"
        spec_sha = _sha(spec_path)
        native_path = tmp_path / "native" / f"{sku}.png"
        _png(native_path, (1080, 1350), (120, 50 + i, 90))
        native_sha = _sha(native_path)
        native_manifest_rows.append({
            "poster_id": poster_id,
            "version": str(version),
            "sku_id": sku,
            "status": "READY",
            "definite_labels": "8",
            "ocr_text_labels": "5",
            "unsure_labels": "0",
            "product_sha256": journal_row["product_sha256"],
            "native_image_sha256": native_sha,
            "action": "synthetic test fixture",
        })
        registry_rows.append({
            "cohort_id": cohort_id,
            "sku_id": sku,
            "product_category": "care" if i % 2 == 0 else "home",
            "product_family_id": f"family-{i + 1}",
            "variant_or_packaging_condition": "plain-background-test",
            "source_asset_authorization_status": "AUTHORIZED",
            "authorization_evidence_ref": "tests-only-fixture",
            "logo_authorization_status": "NOT_APPLICABLE",
            "source_image_sha256": journal_row["product_sha256"],
            "distinct_sku_verified": "YES",
            "duplicate_source_image_reviewed": "YES",
            "reviewer_alias": "test-reviewer",
            "notes": "",
        })
        common = {
            "cohort_id": cohort_id,
            "sku_id": sku,
            "poster_id": poster_id,
            "source_version": str(version),
            "source_product_sha256": journal_row["product_sha256"],
            "request_sha256": journal_row["request_sha256"],
            "spec_sha256": spec_sha,
            "source_square_image_sha256": square_sha,
            "reviewer_alias": "test-reviewer",
            "sku_integrity": "PASS",
            "text_legibility": "PASS",
            "title": "PASS",
            "subtitle": "PASS",
            "price": "PASS",
            "original_price": "N/A",
            "price_position": "PASS",
            "original_price_position": "N/A",
            "cta": "PASS",
            "composition": "PASS",
            "overall_accept": "PASS",
            "overall_reason": "",
            "reviewed_at_utc": "2026-10-23T12:00:00Z",
        }
        review_rows.append({
            **common,
            "variant_id": "square",
            "output_image_path": str(square_path),
            "output_image_sha256": square_sha,
        })
        review_rows.append({
            **common,
            "variant_id": "feed_4x5_native",
            "output_image_path": str(native_path),
            "output_image_sha256": native_sha,
        })

    registry_path = tmp_path / "m6_cohort_registry.csv"
    reviews_path = tmp_path / "m6_output_reviews.csv"
    native_manifest_path = tmp_path / "native_trial_manifest.csv"
    _write_csv(registry_path, REGISTRY_FIELDS, registry_rows)
    _write_csv(reviews_path, REVIEW_FIELDS, review_rows)
    _write_csv(native_manifest_path, NATIVE_MANIFEST_FIELDS, native_manifest_rows)
    readiness_path = tmp_path / "native_trial_readiness.json"
    readiness = {
        "schema_version": "1.0",
        "variant_id": "feed_4x5_native",
        "can_score": True,
        "scope_mode": "batch_latest_per_sku",
        "source_batch_journal_sha256": _sha(journal_path),
        "source_manifest_sha256": journal["manifest_sha256"],
        "batch_sku_count": count,
        "total_versions": count,
        "ready_versions": count,
        "ocr_runtime_attested": False,
    }
    readiness_path.write_text(json.dumps(readiness), encoding="utf-8")
    return {
        "manifest": manifest,
        "registry": registry_path,
        "journal": journal_path,
        "reviews": reviews_path,
        "native_manifest": native_manifest_path,
        "readiness": readiness_path,
        "registry_rows": registry_rows,
        "review_rows": review_rows,
        "native_manifest_rows": native_manifest_rows,
        "readiness_data": readiness,
    }


def _validate(bundle: dict, *, min_skus: int = 2) -> dict:
    return validate_bundle(
        bundle["manifest"], bundle["registry"], bundle["journal"],
        bundle["reviews"], bundle["native_manifest"], bundle["readiness"],
        min_skus=min_skus,
    )


def test_valid_synthetic_bundle_passes_without_quality_score(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path, monkeypatch)
    report = _validate(bundle)
    assert report["status"] == "PASS"
    assert report["expected_sku_count"] == 2
    assert report["expected_output_count"] == 4
    assert report["present_output_count"] == 4
    assert report["missing_output_count"] == 0
    assert report["product_quality_score_computed"] is False
    assert report["ocr_inference_performed"] is False
    assert report["paid_model_calls"] == 0
    assert report["input_sha256"]["batch_journal"] == _sha(bundle["journal"])


def test_cohort_id_must_bind_current_journal_sha(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path, monkeypatch)
    wrong_id = "m6-" + "0" * 12
    for row in bundle["registry_rows"]:
        row["cohort_id"] = wrong_id
    for row in bundle["review_rows"]:
        row["cohort_id"] = wrong_id
    _write_csv(bundle["registry"], REGISTRY_FIELDS, bundle["registry_rows"])
    _write_csv(bundle["reviews"], REVIEW_FIELDS, bundle["review_rows"])

    report = _validate(bundle)
    assert report["status"] == "BLOCKED"
    assert any(
        "cohort_id 必须由当前 M5g journal SHA-256 派生" in error
        for error in report["errors"]
    )


def test_registry_authorization_must_be_explicit(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path, monkeypatch)
    bundle["registry_rows"][0]["source_asset_authorization_status"] = "PENDING"
    _write_csv(bundle["registry"], REGISTRY_FIELDS, bundle["registry_rows"])
    report = _validate(bundle)
    assert report["status"] == "BLOCKED"
    assert any("授权状态不是 AUTHORIZED" in error for error in report["errors"])


def test_output_hash_mismatch_blocks_evidence(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path, monkeypatch)
    bundle["review_rows"][0]["output_image_sha256"] = "0" * 64
    _write_csv(bundle["reviews"], REVIEW_FIELDS, bundle["review_rows"])
    report = _validate(bundle)
    assert report["status"] == "BLOCKED"
    assert any("output_image_sha256 与实际文件不一致" in error for error in report["errors"])


def test_missing_output_is_retained_in_denominator_and_blocks_gate(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path, monkeypatch)
    native = next(row for row in bundle["review_rows"] if row["variant_id"] == "feed_4x5_native")
    native["output_image_path"] = ""
    native["output_image_sha256"] = ""
    native["overall_accept"] = "UNVERIFIED"
    native["overall_reason"] = "OUTPUT_MISSING"
    _write_csv(bundle["reviews"], REVIEW_FIELDS, bundle["review_rows"])
    report = _validate(bundle)
    assert report["status"] == "BLOCKED"
    assert report["expected_output_count"] == 4
    assert report["missing_output_count"] == 1
    assert any("保留在分母" in error for error in report["errors"])


def test_readiness_must_bind_exact_batch_journal(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path, monkeypatch)
    readiness = bundle["readiness_data"]
    readiness["source_batch_journal_sha256"] = "0" * 64
    bundle["readiness"].write_text(json.dumps(readiness), encoding="utf-8")
    report = _validate(bundle)
    assert report["status"] == "BLOCKED"
    assert any("journal_sha256" in error for error in report["errors"])

def test_report_path_cannot_overwrite_any_input(tmp_path):
    manifest = tmp_path / "real_skus.csv"
    manifest.write_text("sku_id\n", encoding="utf-8")
    with pytest.raises(M6ValidationError, match="不能覆盖"):
        _safe_report_destination(manifest, (manifest,))


def test_report_path_does_not_overwrite_previous_evidence(tmp_path):
    report = tmp_path / "m6_report.json"
    report.write_text('{"status":"PASS"}', encoding="utf-8")
    with pytest.raises(M6ValidationError, match="已存在"):
        _safe_report_destination(report, (tmp_path / "manifest.csv",))

def test_native_manifest_hash_must_match_reviewed_native_png(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path, monkeypatch)
    bundle["native_manifest_rows"][0]["native_image_sha256"] = "0" * 64
    _write_csv(
        bundle["native_manifest"], NATIVE_MANIFEST_FIELDS, bundle["native_manifest_rows"]
    )
    report = _validate(bundle)
    assert report["status"] == "BLOCKED"
    assert any("M5j native_image_sha256 不一致" in error for error in report["errors"])
