"""M4c acceptance of the actual 4:5 native pixels (OCR calls mocked in CI)."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app import storage
from app.main import app
from app.ocr_provider import OCRUnavailable
from app.native_acceptance import NATIVE_ZONES

client = TestClient(app)


def _create(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.delenv("POSTER_BRAND_RULES_FILE", raising=False)
    response = client.post("/api/posters",
        files={"product": ("sku.png", product_bytes, "image/png")},
        data={
            "title": "电动牙刷促销", "price": "199", "original_price": "399",
            "subtitle": "限时福利", "cta": "立即购买", "background_mode": "local",
        })
    assert response.status_code == 201, response.text
    return response.json()["poster_id"]


def _endpoint(pid, version=1):
    return f"/api/posters/{pid}/versions/{version}/exports/feed_4x5_native/acceptance"


def _ocr(_path):
    with Image.open(_path) as picture:
        assert picture.format == "PNG"
        assert picture.size == (1080, 1350)
    return [
        {"text": "电动牙刷促销", "score": .99, "bbox": [68, 285, 410, 345]},
        {"text": "限时福利", "score": .99, "bbox": [68, 360, 245, 392]},
        {"text": "¥199", "score": .99, "bbox": [65, 886, 291, 975]},
        {"text": "原价 ¥399", "score": .99, "bbox": [69, 1025, 319, 1065]},
        {"text": "立即购买", "score": .99, "bbox": [88, 1115, 365, 1158]},
    ]


def _by_name(report):
    return {c["name"]: c for c in report["checks"]}


def test_offline_native_gate_refuses_to_inherit_square_pass(tmp_path, monkeypatch, product_bytes):
    pid = _create(tmp_path, monkeypatch, product_bytes)
    res = client.post(_endpoint(pid), json={"ocr_mode": "none"})
    assert res.status_code == 200, res.text
    report = res.json()
    assert report["variant_id"] == "feed_4x5_native"
    assert report["image_dimensions"] == [1080, 1350]
    assert report["ocr_status"] == "NOT_RUN"
    assert report["auto_checks"] == "INCOMPLETE"
    assert report["verdict"] == "REVIEW_REQUIRED"
    assert report["inherits_square_acceptance"] is False
    assert report["requires_human_review"] is True
    checks = _by_name(report)
    assert checks["native_canvas_dimensions"]["status"] == "PASS"
    assert checks["ocr_price"]["status"] == "UNVERIFIED"
    assert checks["ocr_price_position"]["status"] == "UNVERIFIED"
    assert checks["ocr_price_position"]["evidence"]["template_size"] == [1080, 1350]
    assert checks["ocr_price_position"]["evidence"]["zone_xyxy"] == list(NATIVE_ZONES["price"])
    assert client.get(_endpoint(pid)).json()["native_image_sha256"] == report["native_image_sha256"]
    assert storage.view(pid)["versions"] == [1]


def test_paddle_mode_reads_real_native_pixels_and_checks_prices(tmp_path, monkeypatch, product_bytes):
    pid = _create(tmp_path, monkeypatch, product_bytes)
    monkeypatch.setattr("app.native_acceptance.recognize", _ocr)
    response = client.post(_endpoint(pid), json={"ocr_mode": "paddle"})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["ocr_status"] == "OK"
    assert len(report["native_image_sha256"]) == 64
    checks = _by_name(report)
    assert checks["ocr_price"]["status"] == "PASS"
    assert checks["ocr_price_position"]["status"] == "PASS"
    assert checks["ocr_original_price_position"]["status"] == "PASS"
    assert checks["ocr_title"]["status"] == "PASS"
    assert checks["ocr_cta"]["status"] == "PASS"
    # Missing optional brand policy or source logo remains unverified,
    # even when OCR succeeded.
    assert report["verdict"] == "REVIEW_REQUIRED"
    def must_not_rerun(_):
        raise AssertionError("Reading saved report may not re-run OCR")
    monkeypatch.setattr("app.native_acceptance.recognize", must_not_rerun)
    fetched = client.get(_endpoint(pid) + "?ocr_mode=paddle")
    assert fetched.status_code == 200
    assert fetched.json()["native_image_sha256"] == report["native_image_sha256"]


def test_swapped_prices_are_blocked_in_native_not_square_coordinates(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _create(tmp_path, monkeypatch, product_bytes)
    def swapped(path):
        rows = _ocr(path)
        rows[2]["text"] = "¥399"
        rows[3]["text"] = "原价 ¥199"
        return rows
    monkeypatch.setattr("app.native_acceptance.recognize", swapped)
    report = client.post(_endpoint(pid), json={"ocr_mode": "paddle"}).json()
    checks = _by_name(report)
    assert checks["ocr_price"]["status"] == "PASS"  # global text is not enough
    assert checks["ocr_original_price"]["status"] == "PASS"
    assert checks["ocr_price_position"]["status"] == "FAIL"
    assert checks["ocr_original_price_position"]["status"] == "FAIL"
    assert report["auto_checks"] == "FAIL"
    assert report["verdict"] == "BLOCKED"


def test_real_ocr_unavailable_never_passes(tmp_path, monkeypatch, product_bytes):
    pid = _create(tmp_path, monkeypatch, product_bytes)
    def unavailable(_):
        raise OCRUnavailable("not installed")
    monkeypatch.setattr("app.native_acceptance.recognize", unavailable)
    report = client.post(_endpoint(pid), json={"ocr_mode": "paddle"}).json()
    assert report["ocr_status"] == "UNAVAILABLE"
    assert report["auto_checks"] == "INCOMPLETE"
    assert _by_name(report)["ocr_price_position"]["status"] == "UNVERIFIED"


def test_quality_report_hashes_and_version_isolation(tmp_path, monkeypatch, product_bytes):
    pid = _create(tmp_path, monkeypatch, product_bytes)
    original_square = storage.version_path(pid, 1).read_bytes()
    source_spec = storage.poster_dir(pid) / "versions" / "v1.json"
    original_spec = source_spec.read_bytes()
    before_meta = (storage.poster_dir(pid) / "meta.json").read_bytes()
    monkeypatch.setattr("app.main.generate_ai_background",
                        lambda *args: (_ for _ in ()).throw(AssertionError("paid model called")))
    response = client.post(_endpoint(pid), json={"ocr_mode": "none"})
    assert response.status_code == 200
    report = response.json()
    assert report["source_image_sha256"] == hashlib.sha256(original_square).hexdigest()
    assert report["spec_sha256"] == hashlib.sha256(original_spec).hexdigest()
    assert storage.version_path(pid, 1).read_bytes() == original_square
    assert source_spec.read_bytes() == original_spec
    assert (storage.poster_dir(pid) / "meta.json").read_bytes() == before_meta
    assert storage.view(pid)["versions"] == [1]

    rev = client.post(f"/api/posters/{pid}/revisions", json={"price": "189"})
    assert rev.status_code == 201, rev.text
    assert client.get(_endpoint(pid, 2)).status_code == 404
    v2 = client.post(_endpoint(pid, 2), json={"ocr_mode": "none"}).json()
    assert v2["native_image_sha256"] != report["native_image_sha256"]
    assert client.get(_endpoint(pid, 1)).json()["native_image_sha256"] == report["native_image_sha256"]


def test_stale_spec_and_asset_are_not_accepted(tmp_path, monkeypatch, product_bytes):
    pid = _create(tmp_path, monkeypatch, product_bytes)
    assert client.post(_endpoint(pid), json={"ocr_mode": "none"}).status_code == 200
    spec = storage.poster_dir(pid) / "versions" / "v1.json"
    original = spec.read_bytes()
    spec.write_bytes(original + b"\n")
    assert client.get(_endpoint(pid)).status_code == 409
    spec.write_bytes(original)
    asset = storage.poster_dir(pid) / "assets" / "background.png"
    raw = asset.read_bytes()
    asset.write_bytes(raw + b"changed")
    assert client.get(_endpoint(pid)).status_code == 409
    asset.write_bytes(raw)
    assert client.get(_endpoint(pid)).status_code == 200


def test_invalid_mode_or_version_rejected(tmp_path, monkeypatch, product_bytes):
    pid = _create(tmp_path, monkeypatch, product_bytes)
    assert client.post(_endpoint(pid), json={"ocr_mode": "invented"}).status_code == 422
    assert client.get(_endpoint(pid)+"?ocr_mode=bad").status_code == 422
    assert client.get(_endpoint(pid, 99)).status_code == 404
    assert client.post(_endpoint(pid, 99), json={"ocr_mode": "none"}).status_code == 404
