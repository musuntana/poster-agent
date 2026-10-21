"""M5f OCR adapter telemetry is diagnostic, never proof of real inference."""
from __future__ import annotations

from app import ocr_provider
from app.ocr_provider import OCRLines


class _Result:
    json = {
        "res": {
            "rec_texts": ["活动价 ¥199"],
            "rec_scores": [0.98],
            "rec_boxes": [[70, 880, 300, 980]],
        }
    }


class _Engine:
    def __init__(self):
        self.seen_paths = []

    def predict(self, path: str):
        self.seen_paths.append(path)
        return [_Result()]


def test_adapter_returns_backward_compatible_list_with_unattested_runtime(
    tmp_path, monkeypatch,
):
    engine = _Engine()
    monkeypatch.setattr(ocr_provider, "_engine", lambda: engine)
    versions = {"paddleocr": "3.3.0", "paddlepaddle": "3.2.0"}
    monkeypatch.setattr(
        ocr_provider, "_package_version", lambda name: versions.get(name)
    )
    file = tmp_path / "poster.png"
    file.write_bytes(b"placeholder-bytes-not-decoded-by-fake-engine")
    detected = ocr_provider.recognize(file)
    assert isinstance(detected, list)
    assert isinstance(detected, OCRLines)
    assert detected == [{"text": "活动价 ¥199", "score": .98,
                         "bbox": [70, 880, 300, 980]}]
    assert engine.seen_paths == [str(file)]
    runtime = detected.runtime
    assert runtime["source"] == "paddleocr_adapter"
    assert runtime["paddleocr_package_version"] == "3.3.0"
    assert runtime["paddlepaddle_package_version"] == "3.2.0"
    assert runtime["line_count"] == 1
    assert runtime["machine"]
    assert runtime["attested"] is False


def test_native_report_keeps_runtime_evidence_without_declaring_attestation(
    tmp_path, monkeypatch, product_bytes,
):
    from fastapi.testclient import TestClient
    from app.main import app
    from app import storage
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    client = TestClient(app)
    create = client.post(
        "/api/posters",
        files={"product": ("item.png", product_bytes, "image/png")},
        data={"title": "电动牙刷促销", "price": "199",
              "original_price": "399", "background_mode": "local"},
    )
    assert create.status_code == 201, create.text
    pid = create.json()["poster_id"]
    runtime = {
        "source": "paddleocr_adapter", "paddleocr_package_version": "3.3.0",
        "paddlepaddle_package_version": "3.2.0", "machine": "test-runner",
        "attested": False, "line_count": 0,
    }
    monkeypatch.setattr(
        "app.native_acceptance.recognize",
        lambda _: OCRLines([], runtime),
    )
    r = client.post(
        f"/api/posters/{pid}/versions/1/exports/feed_4x5_native/acceptance",
        json={"ocr_mode": "paddle"},
    )
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["ocr_runtime"] == runtime
    assert report["ocr_runtime_evidence"] == "RECORDED"
    saved = storage.poster_dir(pid) / "versions" / "v1.feed_4x5_native.acceptance.paddle.json"
    assert saved.exists()
    assert client.get(
        f"/api/posters/{pid}/versions/1/exports/feed_4x5_native/acceptance"
        "?ocr_mode=paddle"
    ).json()["ocr_runtime"]["attested"] is False
