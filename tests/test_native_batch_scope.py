"""M5j batch-specific 4:5 scoring: real Pillow and storage, mocked OCR."""
from __future__ import annotations

import csv
import io
import json

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from app import storage
from app.main import app
from evals.native_batch_scope import load_batch_scope
from evals.native_trial import audit_trial, run_trial
from scripts.real_sku_batch import run_batch

client = TestClient(app)
REVIEWER = "batch-operator"


def _png(color):
    image = Image.new("RGB", (128, 128), color)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _import_two(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    directory = tmp_path / "import"
    directory.mkdir()
    (directory / "one.png").write_bytes(_png((20, 80, 145)))
    (directory / "two.png").write_bytes(_png((215, 55, 80)))
    manifest = directory / "skus.csv"
    manifest.write_text(
        "sku_id,product_image,title,price,original_price\n"
        "sku-one,one.png,电动牙刷促销,199,399\n"
        "sku-two,two.png,智能耳机促销,89,169\n", encoding="utf-8"
    )
    result = run_batch(manifest, tmp_path / "batch", apply=True)
    path = tmp_path / "batch" / "batch_import_results.json"
    scope = load_batch_scope(path)
    assert scope["batch_sku_count"] == 2
    return result["poster_ids"], path, scope


def _review(pid, version=1):
    url = f"/api/posters/{pid}/versions/{version}/exports/feed_4x5_native/human-review"
    context = client.get(url).json()
    assert context["version"] == version
    res = client.post(url, json={
        "reviewer": REVIEWER,
        "image_sha256": context["image_sha256"],
        "source_image_sha256": context["source_image_sha256"],
        "spec_sha256": context["spec_sha256"],
        "asset_sha256": context["asset_sha256"],
        "fields": {
            name: {"judgment": "correct", "reason": ""}
            for name in context["fields_to_review"]
        },
    })
    assert res.status_code == 201, res.text


def _ocr(pid, version, monkeypatch):
    # Mock produces valid contract statuses, not real model performance.
    monkeypatch.setattr("app.native_acceptance.recognize", lambda path: [])
    res = client.post(
        f"/api/posters/{pid}/versions/{version}/exports/feed_4x5_native/acceptance",
        json={"ocr_mode": "paddle"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["ocr_status"] == "OK"


def test_applied_journal_only_and_fail_closed_json(tmp_path):
    pid = "a" * 32
    valid = {
        "schema_version": "m5g-1", "mode": "apply",
        "manifest_sha256": "1" * 64,
        "expected_sku_count": 1,
        "expected_sku_ids": ["sku-01"],
        "completed": True,
        "rows": {"sku-01": {
            "poster_id": pid, "version": 1, "request_sha256": "2" * 64,
            "product_sha256": "3" * 64, "logo_sha256": None,
            "idempotency_key": "m5g_" + "4" * 56,
        }},
    }
    path = tmp_path / "batch.json"
    path.write_text(json.dumps(valid), encoding="utf-8")
    assert load_batch_scope(path)["sku_by_id"] == {pid: "sku-01"}
    changes = [
        {"mode": "check"},
        {"schema_version": "wrong"},
        {"completed": False},
        {"completed": "true"},
        {"expected_sku_count": 2},
        {"expected_sku_ids": ["sku-02"]},
        {"expected_sku_ids": ["sku-01", "sku-01"], "expected_sku_count": 2},
        {"rows": {}},
        {"rows": {**valid["rows"], "sku-02": valid["rows"]["sku-01"]}},
        {"rows": {"sku-01": {**valid["rows"]["sku-01"], "request_sha256": "BAD"}}},
        {"rows": {"sku-01": {**valid["rows"]["sku-01"], "version": 2}}},
    ]
    for changed in changes:
        path.write_text(json.dumps({**valid, **changed}), encoding="utf-8")
        with pytest.raises(ValueError):
            load_batch_scope(path)
    # Legacy journals are not silently interpreted as complete. The importer
    # can finalize them with an idempotent --apply rerun.
    legacy = {key: value for key, value in valid.items() if key not in {
        "expected_sku_count", "expected_sku_ids", "completed"
    }}
    path.write_text(json.dumps(legacy), encoding="utf-8")
    with pytest.raises(ValueError, match="尚未完成"):
        load_batch_scope(path)
    path.write_bytes(b"x" * 200001)
    with pytest.raises(ValueError, match="200 KB"):
        load_batch_scope(path)


def test_scoped_batch_blocks_until_all_skus_reviewed_and_ocr_ready(
    tmp_path, monkeypatch,
):
    ids, _path, scope = _import_two(tmp_path, monkeypatch)
    # A review from outside this batch must not enter the denominator.
    outside = client.post("/api/posters",
        files={"product": ("other.png", _png((5, 180, 15)), "image/png")},
        data={"title": "批次之外商品", "price": "55", "background_mode": "local"})
    assert outside.status_code == 201
    outside_pid = outside.json()["poster_id"]
    _review(outside_pid)

    audit, rows = audit_trial(REVIEWER, batch_scope=scope)
    assert audit["scope_mode"] == "batch_latest_per_sku"
    assert audit["total_versions"] == 2
    assert audit["status_counts"] == {"NEEDS_REVIEW": 2}
    assert audit["can_score"] is False
    assert {r["sku_id"] for r in rows} == {"sku-one", "sku-two"}

    _review(ids["sku-one"])
    _ocr(ids["sku-one"], 1, monkeypatch)
    audit, rows = audit_trial(REVIEWER, batch_scope=scope)
    assert audit["status_counts"] == {"NEEDS_REVIEW": 1, "READY": 1}
    assert audit["can_score"] is False

    with pytest.raises(ValueError, match="未完成"):
        run_trial(REVIEWER, tmp_path / "score", score=True, batch_scope=scope)
    assert not (tmp_path / "score/native_real_summary.json").exists()
    assert len(list((tmp_path / "posters").iterdir())) == 3


def test_batch_scoring_uses_exact_skus_and_latest_version_only(
    tmp_path, monkeypatch,
):
    ids, path, scope = _import_two(tmp_path, monkeypatch)
    first = ids["sku-one"]
    second = ids["sku-two"]
    for pid in (first, second):
        _review(pid)
        _ocr(pid, 1, monkeypatch)
    global_audit, _ = audit_trial(REVIEWER)
    assert global_audit["ready_versions"] == 2

    revised = client.post(f"/api/posters/{first}/revisions", json={"price": "179"})
    assert revised.status_code == 201
    incomplete, rows = audit_trial(REVIEWER, batch_scope=scope)
    assert incomplete["can_score"] is False
    assert incomplete["status_counts"] == {"NEEDS_REVIEW": 1, "READY": 1}
    assert {(r["poster_id"], r["version"]) for r in rows} == {(first, 2), (second, 1)}
    _review(first, 2)
    _ocr(first, 2, monkeypatch)
    summary, rows = audit_trial(REVIEWER, batch_scope=scope)
    assert summary["can_score"] is True
    assert summary["batch_sku_count"] == 2
    assert summary["batch_unique_raw_product_sha256_count"] == 2
    assert summary["ready_versions"] == 2

    directory = tmp_path / "result"
    scored = run_trial(REVIEWER, directory, score=True, batch_scope=scope)
    assert scored["scope_mode"] == "batch_latest_per_sku"
    assert scored["poster_versions"] == 2
    assert scored["scored_sku_to_poster_id"] == {"sku-one": first, "sku-two": second}
    assert scored["source_batch_journal_sha256"] == scope["source_batch_journal_sha256"]
    assert json.loads((directory / "native_real_summary.json").read_text())["scope_mode"] == "batch_latest_per_sku"
    assert {(r["poster_id"], r["version"]) for r in csv.DictReader(
        io.StringIO((directory / "native_trial_labels.csv").read_text())
    )} == {(first, "2"), (second, "1")}
    assert set(scored["source_report_sha256"]) == {f"{first}:v2", f"{second}:v1"}
    assert (directory / "native_trial_readiness.json").exists()


def test_missing_batch_id_is_visible_and_never_shrinks_denominator(
    tmp_path, monkeypatch,
):
    ids, _path, scope = _import_two(tmp_path, monkeypatch)
    # Do not silently exclude the missing SKU while scoring the survivor.
    import shutil
    shutil.rmtree(storage.poster_dir(ids["sku-two"]))
    _review(ids["sku-one"])
    _ocr(ids["sku-one"], 1, monkeypatch)
    info, rows = audit_trial(REVIEWER, batch_scope=scope)
    assert info["total_versions"] == 2
    assert info["status_counts"] == {"READY": 1, "SOURCE_INVALID": 1}
    assert info["can_score"] is False
    assert next(r for r in rows if r["status"] == "SOURCE_INVALID")["sku_id"] == "sku-two"


def test_scoped_human_export_ignores_unrelated_stale_reviews(
    tmp_path, monkeypatch,
):
    ids, _path, scope = _import_two(tmp_path, monkeypatch)
    for pid in ids.values():
        _review(pid)
        _ocr(pid, 1, monkeypatch)
    outside = client.post("/api/posters",
        files={"product": ("outside.png", _png((17, 33, 100)), "image/png")},
        data={"title": "外部商品", "price": "66", "background_mode": "local"})
    assert outside.status_code == 201
    other = outside.json()["poster_id"]
    _review(other)
    # Deliberately make the out-of-batch human label stale.
    product = storage.poster_dir(other) / "assets" / "product.png"
    product.write_bytes(product.read_bytes() + b"changed")
    assert run_trial(REVIEWER, tmp_path / "only-batch",
                     score=True, batch_scope=scope)["poster_versions"] == 2
    # Existing unscoped behavior remains strict about stale global labels.
    from app import native_human_reviews as reviews
    with pytest.raises(reviews.NativeReviewConflict):
        reviews.export(REVIEWER)
