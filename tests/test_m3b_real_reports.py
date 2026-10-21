"""An actual PNG + simulated OCR report and explicit test labels, no real model claims."""
import csv
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import storage
from evals.real_reports import evaluate_real_reports, load_labels

client = TestClient(app)


def make_poster(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.delenv("POSTER_BRAND_RULES_FILE", raising=False)
    response = client.post("/api/posters",
        files={"product": ("product.png", product_bytes, "image/png")},
        data={"title": "双十一优惠", "price": "199", "subtitle": "",
              "cta": "立即抢购", "background_mode": "local"})
    assert response.status_code == 201, response.text
    return response.json()["poster_id"]


def write_labels(path, records):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle,
            fieldnames=["poster_id","version","field","defective","image_sha256","reviewer"])
        writer.writeheader()
        writer.writerows(records)


def test_manual_annotation_pipeline_checks_hash_and_report(tmp_path, monkeypatch, product_bytes):
    pid = make_poster(tmp_path, monkeypatch, product_bytes)
    monkeypatch.setattr("app.acceptance.recognize", lambda _: [
        {"text": value, "score": 0.9}
        for value in ["双十一优惠", "¥199", "立即抢购"]
    ])
    result = client.post(
        f"/api/posters/{pid}/versions/1/acceptance", json={"ocr_mode":"paddle"}
    )
    assert result.status_code == 200
    sha = result.json()["image_sha256"]
    path = tmp_path / "annotations.csv"
    write_labels(path, [{
        "poster_id":pid, "version":1,"field":"price","defective":"false",
        "image_sha256":sha,"reviewer":"test-annotator",
    }])
    report = evaluate_real_reports(path, tmp_path / "outputs")
    assert report["metrics"]["confusion"]["TN"] == 1
    assert report["field_labels"] == 1
    assert (tmp_path / "outputs/real_cases.csv").is_file()
    assert (tmp_path / "outputs/real_summary.json").is_file()


def test_stale_image_hash_rejected(tmp_path, monkeypatch, product_bytes):
    pid = make_poster(tmp_path, monkeypatch, product_bytes)
    monkeypatch.setattr("app.acceptance.recognize", lambda _: [
        {"text":"¥199","score":0.99}])
    resp = client.post(f"/api/posters/{pid}/versions/1/acceptance",
                       json={"ocr_mode":"paddle"})
    assert resp.status_code == 200
    labels = tmp_path / "annotations.csv"
    write_labels(labels, [{
        "poster_id":pid,"version":1,"field":"price","defective":"false",
        "image_sha256":"0"*64,"reviewer":"test-annotator",
    }])
    with pytest.raises(ValueError, match="SHA-256"):
        evaluate_real_reports(labels, tmp_path / "outputs")


def test_missing_report_is_not_silently_skipped(tmp_path, monkeypatch, product_bytes):
    pid = make_poster(tmp_path, monkeypatch, product_bytes)
    import hashlib
    image = storage.version_path(pid, 1)
    sha = hashlib.sha256(image.read_bytes()).hexdigest()
    labels = tmp_path / "annotations.csv"
    write_labels(labels, [{
        "poster_id":pid,"version":1,"field":"price","defective":"false",
        "image_sha256":sha,"reviewer":"test-annotator",
    }])
    with pytest.raises(ValueError, match="还没有"):
        evaluate_real_reports(labels, tmp_path / "outputs")


def test_duplicate_annotation_and_invalid_label_rejected(tmp_path):
    rows = [{
        "poster_id":"0"*32,"version":1,"field":"price","defective":"false",
        "image_sha256":"f"*64,"reviewer":"reviewer",
    }]
    path = tmp_path / "annotations.csv"
    write_labels(path, rows+rows)
    with pytest.raises(ValueError, match="第 3 行"):
        load_labels(path)
    write_labels(path, [{**rows[0], "defective":"maybe"}])
    with pytest.raises(ValueError, match="第 2 行"):
        load_labels(path)
