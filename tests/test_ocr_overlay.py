"""M3f overlay is derived from existing immutable evidence, not a new OCR run.

All OCR values here are MOCKED. No real PaddleOCR model is loaded in CI.
"""
from __future__ import annotations

import hashlib
import io
from PIL import Image
from fastapi.testclient import TestClient

from app.main import app
from app import storage
from app.ocr_overlay import MAX_DRAWN_BOXES, _valid_box

client = TestClient(app)


def _make_poster(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.delenv("POSTER_BRAND_RULES_FILE", raising=False)
    response = client.post(
        "/api/posters",
        data={
            "title": "双十一电动牙刷", "subtitle": "品质生活",
            "price": "199", "original_price": "399",
            "cta": "立即抢购", "background_mode": "local",
        },
        files={"product": ("sku.png", product_bytes, "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()["poster_id"]


def _report_url(pid, version=1, mode="paddle"):
    return f"/api/posters/{pid}/versions/{version}/acceptance?ocr_mode={mode}"


def _overlay_url(pid, version=1, mode="paddle"):
    return f"/api/posters/{pid}/versions/{version}/acceptance/overlay?ocr_mode={mode}"


def _fake_ocr(_):
    return [
        {"text": "双十一电动牙刷", "score": .98, "bbox": [80, 270, 425, 335]},
        {"text": "¥199", "score": .99, "bbox": [75, 673, 289, 758]},
        {"text": "原价 ¥399", "score": .98, "bbox": [77, 803, 343, 845]},
        {"text": "立即抢购", "score": .97, "bbox": [90, 878, 373, 926]},
        {"text": "品质生活", "score": .98, "bbox": [80, 378, 309, 409]},
    ]


def test_overlay_requires_saved_report_and_never_runs_ocr(tmp_path, monkeypatch, product_bytes):
    pid = _make_poster(tmp_path, monkeypatch, product_bytes)
    assert client.get(_overlay_url(pid)).status_code == 404
    monkeypatch.setattr("app.acceptance.recognize", _fake_ocr)
    response = client.post(_report_url(pid).split("?")[0], json={"ocr_mode": "paddle"})
    assert response.status_code == 200, response.text
    # After report creation GET overlay MUST read only stored evidence.
    def fail_if_called(_):
        raise AssertionError("GET overlay must not execute OCR")
    monkeypatch.setattr("app.acceptance.recognize", fail_if_called)
    before_png = storage.version_path(pid, 1).read_bytes()
    response = client.get(_overlay_url(pid))
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-ocr-status"] == "OK"
    assert response.headers["x-ocr-boxes-drawn"] == "5"
    assert response.headers["x-source-image-sha256"] == hashlib.sha256(before_png).hexdigest()
    overlay = Image.open(io.BytesIO(response.content))
    assert overlay.size == (1024, 1024)
    assert overlay.format == "PNG"
    assert response.content != before_png
    assert storage.version_path(pid, 1).read_bytes() == before_png
    # Overlaid diagnostic zone border must differ from original image.
    original = Image.open(io.BytesIO(before_png)).convert("RGB")
    assert overlay.getpixel((45, 645)) != original.getpixel((45, 645))
    assert not list(storage.poster_dir(pid).glob("**/*overlay*.png"))


def test_offline_mode_overlay_marks_no_ocr_and_does_not_fake_boxes(tmp_path, monkeypatch, product_bytes):
    pid = _make_poster(tmp_path, monkeypatch, product_bytes)
    response = client.post(_report_url(pid, mode="none").split("?")[0],
                           json={"ocr_mode": "none"})
    assert response.status_code == 200
    result = client.get(_overlay_url(pid, mode="none"))
    assert result.status_code == 200
    assert result.headers["x-ocr-status"] == "NOT_RUN"
    assert result.headers["x-ocr-boxes-drawn"] == "0"
    assert Image.open(io.BytesIO(result.content)).size == (1024, 1024)


def test_missing_ocr_boxes_remain_missing_not_inferred(tmp_path, monkeypatch, product_bytes):
    pid = _make_poster(tmp_path, monkeypatch, product_bytes)
    monkeypatch.setattr("app.acceptance.recognize", lambda _: [
        {"text": "¥199", "score": 0.99, "bbox": None},
        {"text": "原价 ¥399", "score": 0.99, "bbox": [80, 809, 290, 837]},
    ])
    assert client.post(_report_url(pid).split("?")[0], json={"ocr_mode": "paddle"}).status_code == 200
    response = client.get(_overlay_url(pid))
    assert response.status_code == 200
    assert response.headers["x-ocr-boxes-drawn"] == "1"
    assert Image.open(io.BytesIO(response.content)).format == "PNG"


def test_overlay_rejects_tampered_image_and_spec_hashes(tmp_path, monkeypatch, product_bytes):
    pid = _make_poster(tmp_path, monkeypatch, product_bytes)
    monkeypatch.setattr("app.acceptance.recognize", _fake_ocr)
    assert client.post(_report_url(pid).split("?")[0], json={"ocr_mode": "paddle"}).status_code == 200

    img_path = storage.version_path(pid, 1)
    source = img_path.read_bytes()
    img_path.write_bytes(source + b"modified")
    assert client.get(_overlay_url(pid)).status_code == 409
    img_path.write_bytes(source)
    spec_path = storage.poster_dir(pid) / "versions" / "v1.json"
    spec = spec_path.read_bytes()
    spec_path.write_bytes(spec + b"\n")
    assert client.get(_overlay_url(pid)).status_code == 409
    spec_path.write_bytes(spec)
    assert client.get(_overlay_url(pid)).status_code == 200


def test_overlay_rejects_wrong_version_bad_id_or_mode(tmp_path, monkeypatch, product_bytes):
    pid = _make_poster(tmp_path, monkeypatch, product_bytes)
    assert client.get(_overlay_url("not-a-poster")).status_code == 404
    assert client.get(_overlay_url(pid, version=999)).status_code == 404
    assert client.get(_overlay_url(pid, mode="invalid")).status_code == 422


def test_invalid_bbox_and_input_limits_are_explicit():
    assert _valid_box([1, 2, 20, 30], (1024, 1024)) == (1, 2, 20, 30)
    assert _valid_box([1, 2, 20], (1024, 1024)) is None
    assert _valid_box([0, 0, 20000, 30], (1024, 1024)) is None
    assert _valid_box([0, 0, float("nan"), 30], (1024, 1024)) is None
    assert _valid_box([0, 0, True, 30], (1024, 1024)) is None
    assert MAX_DRAWN_BOXES == 35


def test_overlay_different_versions_use_correct_png(tmp_path, monkeypatch, product_bytes):
    pid = _make_poster(tmp_path, monkeypatch, product_bytes)
    monkeypatch.setattr("app.acceptance.recognize", _fake_ocr)
    assert client.post(_report_url(pid).split("?")[0], json={"ocr_mode": "paddle"}).status_code == 200
    response = client.post(f"/api/posters/{pid}/revisions", json={"price": "189"})
    assert response.status_code == 201
    assert client.get(_overlay_url(pid, 2)).status_code == 404
    assert client.get(_overlay_url(pid, 1)).status_code == 200
    assert client.post(_report_url(pid, 2).split("?")[0],
                       json={"ocr_mode": "paddle"}).status_code == 200
    first, second = client.get(_overlay_url(pid, 1)), client.get(_overlay_url(pid, 2))
    assert first.headers["x-source-image-sha256"] != second.headers["x-source-image-sha256"]
    assert first.content != second.content
