"""M5d: provenance, abstention and price-zone metrics; OCR is MOCKED here.

These are behavioral contract tests, NOT real OCR accuracy experiments.
"""
from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient

from app import storage
from app.main import app
from evals.native_real_reports import evaluate_native_reports, read_native_labels

client = TestClient(app)
REVIEWER = "operator-4x5"


def _poster(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.delenv("POSTER_BRAND_RULES_FILE", raising=False)
    response = client.post(
        "/api/posters",
        files={"product": ("sku.png", product_bytes, "image/png")},
        data={"title": "电动牙刷促销", "subtitle": "限时福利", "price": "199",
              "original_price": "399", "cta": "立即购买", "background_mode": "local"},
    )
    assert response.status_code == 201, response.text
    return response.json()["poster_id"]


def _context(pid, version=1):
    resp = client.get(
        f"/api/posters/{pid}/versions/{version}/exports/feed_4x5_native/human-review"
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _submit(pid, ctx, *, defective=(), unsure=(), reviewer=REVIEWER):
    defective, unsure = set(defective), set(unsure)
    data = {
        "reviewer": reviewer,
        "image_sha256": ctx["image_sha256"],
        "source_image_sha256": ctx["source_image_sha256"],
        "spec_sha256": ctx["spec_sha256"],
        "asset_sha256": ctx["asset_sha256"],
        "fields": {
            name: {
                "judgment": "unsure" if name in unsure
                else "defective" if name in defective else "correct",
                "reason": "",
            }
            for name in ctx["fields_to_review"]
        },
    }
    r = client.post(
        f"/api/posters/{pid}/versions/{ctx['version']}/exports/feed_4x5_native/human-review",
        json=data,
    )
    assert r.status_code == 201, r.text


def _labels(tmp_path, reviewer=REVIEWER):
    response = client.get("/api/annotations/native-4x5/export.csv?reviewer=" + reviewer)
    assert response.status_code == 200, response.text
    file = tmp_path / "native_labels.csv"
    file.write_bytes(response.content)
    return file


def _ocr(_path):
    from PIL import Image
    with Image.open(_path) as img:
        assert img.size == (1080, 1350)
    return [
        {"text": "电动牙刷促销", "score": 0.99, "bbox": [66, 280, 414, 341]},
        {"text": "限时福利", "score": 0.99, "bbox": [68, 355, 252, 386]},
        {"text": "¥199", "score": 0.99, "bbox": [66, 880, 295, 975]},
        {"text": "原价 ¥399", "score": 0.99, "bbox": [70, 1024, 318, 1064]},
        {"text": "立即购买", "score": 0.99, "bbox": [85, 1117, 359, 1157]},
    ]


def _report(pid, monkeypatch, lines=_ocr):
    monkeypatch.setattr("app.native_acceptance.recognize", lines)
    response = client.post(
        f"/api/posters/{pid}/versions/1/exports/feed_4x5_native/acceptance",
        json={"ocr_mode": "paddle"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["ocr_status"] == "OK"


def test_reuses_metrics_with_native_hashes_and_visual_fields_not_predicted(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _poster(tmp_path, monkeypatch, product_bytes)
    ctx = _context(pid)
    _submit(pid, ctx, defective=["sku_integrity"], unsure=["composition"])
    _report(pid, monkeypatch)
    labels = _labels(tmp_path)
    data = evaluate_native_reports(labels, tmp_path / "result")
    assert data["variant_id"] == "feed_4x5_native"
    assert data["ocr_engine_execution"] == "saved_report_only"
    assert data["ocr_text_labels"] == 5
    assert data["visual_labels"] == 2  # composition unsure excluded
    assert data["metrics"]["confusion"]["TN"] == 5
    assert data["metrics"]["confusion"]["TP"] == 0
    assert "sku_integrity" not in data["by_field"]
    assert data["visual_human_only"]["sku_integrity"]["defective"] == 1
    assert data["small_sample_warning"] is True
    assert (tmp_path / "result/native_real_summary.json").is_file()
    assert (tmp_path / "result/native_real_cases.csv").is_file()
    with (tmp_path / "result/native_real_cases.csv").open() as f:
        assert all(r["field"] not in {"sku_integrity", "text_legibility", "composition"}
                   for r in csv.DictReader(f))


def test_price_position_failure_is_not_overridden_by_global_text_match(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _poster(tmp_path, monkeypatch, product_bytes)
    def wrong_places(path):
        rows = _ocr(path)
        rows[2]["text"] = "¥399"
        rows[3]["text"] = "原价 ¥199"
        return rows
    _report(pid, monkeypatch, wrong_places)
    _submit(pid, _context(pid), defective=["price", "original_price"])
    summary = evaluate_native_reports(_labels(tmp_path), tmp_path / "out")
    assert summary["metrics"]["confusion"]["TP"] == 2
    assert summary["by_field"]["price"]["confusion"]["TP"] == 1
    assert summary["by_field"]["original_price"]["confusion"]["TP"] == 1
    assert summary["metrics"]["confusion"]["TN"] == 3


def test_low_confidence_or_missing_box_abstains_not_a_good_prediction(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _poster(tmp_path, monkeypatch, product_bytes)
    def unlocated_price(path):
        rows = _ocr(path)
        rows[2]["bbox"] = None
        return rows
    _report(pid, monkeypatch, unlocated_price)
    _submit(pid, _context(pid), defective=["price"])
    summary = evaluate_native_reports(_labels(tmp_path), tmp_path / "out")
    assert summary["by_field"]["price"]["confusion"]["ABSTAIN_DEFECT"] == 1
    assert summary["by_field"]["price"]["assessed"] == 0
    assert summary["metrics"]["abstained"] >= 1


def test_missing_real_ocr_or_only_rule_checks_fails_closed(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _poster(tmp_path, monkeypatch, product_bytes)
    _submit(pid, _context(pid))
    labels = _labels(tmp_path)
    result_dir = tmp_path / "results"
    with pytest.raises(ValueError, match="PaddleOCR"):
        evaluate_native_reports(labels, result_dir)
    resp = client.post(
        f"/api/posters/{pid}/versions/1/exports/feed_4x5_native/acceptance",
        json={"ocr_mode": "none"},
    )
    assert resp.status_code == 200
    with pytest.raises(ValueError, match="PaddleOCR"):
        evaluate_native_reports(labels, result_dir)
    assert not result_dir.exists()


def test_ocr_unavailable_or_error_not_scored(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _poster(tmp_path, monkeypatch, product_bytes)
    _submit(pid, _context(pid))
    from app.ocr_provider import OCRUnavailable
    monkeypatch.setattr(
        "app.native_acceptance.recognize",
        lambda _: (_ for _ in ()).throw(OCRUnavailable("missing engine")),
    )
    resp = client.post(
        f"/api/posters/{pid}/versions/1/exports/feed_4x5_native/acceptance",
        json={"ocr_mode": "paddle"},
    )
    assert resp.status_code == 200
    assert resp.json()["ocr_status"] == "UNAVAILABLE"
    with pytest.raises(ValueError, match="OCR 未成功"):
        evaluate_native_reports(_labels(tmp_path), tmp_path / "out")


def test_modified_native_assets_make_saved_report_stale(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _poster(tmp_path, monkeypatch, product_bytes)
    _report(pid, monkeypatch)
    _submit(pid, _context(pid))
    labels = _labels(tmp_path)
    asset = storage.poster_dir(pid) / "assets" / "background.png"
    content = asset.read_bytes()
    asset.write_bytes(content + b"changes")
    with pytest.raises(ValueError, match="未过期"):
        evaluate_native_reports(labels, tmp_path / "out")


@pytest.mark.parametrize("change", ["variant_id", "image_sha256", "source_image_sha256", "duplicate", "field", "reviewer"])
def test_refuses_incompatible_or_forged_csv(
    tmp_path, monkeypatch, product_bytes, change,
):
    pid = _poster(tmp_path, monkeypatch, product_bytes)
    _submit(pid, _context(pid))
    file = _labels(tmp_path)
    rows = list(csv.DictReader(io.StringIO(file.read_text(encoding="utf-8-sig"))))
    if change == "duplicate":
        rows.append(dict(rows[0]))
    elif change == "reviewer":
        rows[1]["reviewer"] = "another-reviewer"
    elif change == "variant_id":
        rows[0]["variant_id"] = "square"
    elif change == "field":
        rows[0]["field"] = "invented_field"
    else:
        rows[0][change] = "0" * 64
    with file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError):
        if change == "image_sha256" or change == "source_image_sha256":
            _report(pid, monkeypatch)
            evaluate_native_reports(file, tmp_path / "out")
        else:
            read_native_labels(file)


def test_new_version_never_inherits_v1_ocr_report(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _poster(tmp_path, monkeypatch, product_bytes)
    _report(pid, monkeypatch)
    _submit(pid, _context(pid))
    r = client.post(f"/api/posters/{pid}/revisions", json={"price": "189"})
    assert r.status_code == 201
    _submit(pid, _context(pid, 2))
    with pytest.raises(ValueError, match="PaddleOCR"):
        evaluate_native_reports(_labels(tmp_path), tmp_path / "out")


def test_no_native_labels_rejects_square_only_csv(tmp_path, monkeypatch, product_bytes):
    pid = _poster(tmp_path, monkeypatch, product_bytes)
    ctx = client.get(f"/api/posters/{pid}/versions/1/human-review").json()
    r = client.post(f"/api/posters/{pid}/versions/1/human-review", json={
        "reviewer": REVIEWER, "image_sha256": ctx["image_sha256"],
        "spec_sha256": ctx["spec_sha256"],
        "fields": {field: {"judgment": "correct", "reason": ""}
                   for field in ctx["fields_to_review"]},
    })
    assert r.status_code == 201
    response = client.get("/api/annotations/export.csv?reviewer=" + REVIEWER)
    assert response.status_code == 200
    file = tmp_path / "square.csv"
    file.write_bytes(response.content)
    with pytest.raises(ValueError, match="缺少必要列"):
        read_native_labels(file)
