"""M5h: review queue is a read-only view of M5e states and latest versions."""
from __future__ import annotations

import io
from pathlib import Path

from fastapi.testclient import TestClient

from app import storage
from app.main import app

client = TestClient(app)
REVIEWER = "queue-operator"


def _create(tmp_path, monkeypatch, product_bytes, title="电动牙刷", price="199"):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    response = client.post(
        "/api/posters",
        files={"product": ("sku.png", product_bytes, "image/png")},
        data={"title": title, "price": price, "original_price": "399",
              "subtitle": "限时福利", "cta": "立即购买", "background_mode": "local"},
    )
    assert response.status_code == 201, response.text
    return response.json()["poster_id"]


def _queue(reviewer=REVIEWER):
    return client.get("/api/trials/review-queue", params={"reviewer": reviewer})


def _review(pid, version=1, reviewer=REVIEWER):
    url = f"/api/posters/{pid}/versions/{version}/exports/feed_4x5_native/human-review"
    info = client.get(url, params={"reviewer": reviewer}).json()
    data = {
        "reviewer": reviewer,
        "image_sha256": info["image_sha256"],
        "source_image_sha256": info["source_image_sha256"],
        "spec_sha256": info["spec_sha256"],
        "asset_sha256": info["asset_sha256"],
        "fields": {
            field: {"judgment": "correct", "reason": ""}
            for field in info["fields_to_review"]
        },
    }
    result = client.post(url, json=data)
    assert result.status_code == 201, result.text


def test_queue_empty_and_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    response = _queue()
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total_posters"] == 0
    assert body["total_versions_in_cohort"] == 0
    assert body["read_only"] is True
    assert body["runs_ocr"] is False
    assert body["automatically_approved"] is False
    assert not (tmp_path / "posters").exists()


def test_latest_version_only_and_operator_specific_status(
    tmp_path, monkeypatch, product_bytes,
):
    first = _create(tmp_path, monkeypatch, product_bytes, title="限时牙刷")
    second = _create(tmp_path, monkeypatch, product_bytes, title="健康耳机")
    _review(first, 1)
    revision = client.post(f"/api/posters/{first}/revisions", json={"price": "189"})
    assert revision.status_code == 201, revision.text

    queue = _queue().json()
    assert queue["total_posters"] == 2
    assert queue["total_versions_in_cohort"] == 3
    assert queue["latest_status_counts"] == {"NEEDS_REVIEW": 2}
    item = next(i for i in queue["items"] if i["poster_id"] == first)
    assert item["version"] == 2
    assert item["title"] == "限时牙刷"
    assert item["status"] == "NEEDS_REVIEW"
    assert item["poster_url"] == f"/api/posters/{first}"
    assert item["native_image_url"].endswith("/versions/2/exports/feed_4x5_native")
    assert client.get(item["native_image_url"]).status_code == 200
    assert all(i["version"] == 1 for i in queue["items"] if i["poster_id"] == second)

    _review(first, 2)
    updated = _queue().json()
    assert updated["latest_status_counts"] == {
        "NEEDS_REVIEW": 1, "OCR_MISSING": 1,
    }
    other = _queue("different-reviewer").json()
    assert other["latest_status_counts"] == {"NEEDS_REVIEW": 2}


def test_queue_does_not_run_ocr_or_change_poster_files(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _create(tmp_path, monkeypatch, product_bytes)
    _review(pid)
    poster_root = storage.poster_dir(pid)
    snapshot = {
        str(path.relative_to(poster_root)): path.read_bytes()
        for path in poster_root.rglob("*") if path.is_file()
    }
    def forbid(*args, **kwargs):
        raise AssertionError("Queue must never execute OCR or paid image generation")
    monkeypatch.setattr("app.native_acceptance.recognize", forbid)
    monkeypatch.setattr("app.main.generate_ai_background", forbid)
    queue = _queue().json()
    assert queue["latest_status_counts"] == {"OCR_MISSING": 1}
    assert queue["items"][0]["definite_labels"] > 0
    assert queue["items"][0]["action"]
    assert snapshot == {
        str(path.relative_to(poster_root)): path.read_bytes()
        for path in poster_root.rglob("*") if path.is_file()
    }


def test_queue_uses_existing_ready_state_without_automatic_approval(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _create(tmp_path, monkeypatch, product_bytes)
    _review(pid)
    def mocked_ocr(path):
        return [
            {"text": "电动牙刷", "score": 0.99, "bbox": [70, 280, 310, 340]},
            {"text": "限时福利", "score": 0.99, "bbox": [70, 362, 260, 398]},
            {"text": "¥199", "score": 0.99, "bbox": [70, 881, 303, 971]},
            {"text": "原价 ¥399", "score": 0.99, "bbox": [70, 1027, 337, 1070]},
            {"text": "立即购买", "score": 0.99, "bbox": [85, 1118, 365, 1158]},
        ]
    monkeypatch.setattr("app.native_acceptance.recognize", mocked_ocr)
    result = client.post(
        f"/api/posters/{pid}/versions/1/exports/feed_4x5_native/acceptance",
        json={"ocr_mode": "paddle"},
    )
    assert result.status_code == 200, result.text
    queue = _queue().json()
    assert queue["latest_status_counts"] == {"READY": 1}
    assert queue["items"][0]["status"] == "READY"
    assert queue["automatically_approved"] is False
    assert queue["runs_ocr"] is False


def test_invalid_reviewer_rejected_and_html_controls_use_safe_text_content(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    for invalid in ("../", "x", "bad!alias"):
        assert _queue(invalid).status_code == 422
    home = client.get("/")
    assert home.status_code == 200
    html = home.text
    assert 'id="loadReviewQueue"' in html
    assert 'id="reviewQueueItems"' in html
    assert "/api/trials/review-queue?reviewer=" in html
    assert "label.textContent=(sku?" in html
    assert "item.title+" in html
    assert "hint.textContent=" in html
    assert "display(poster)" in html
    assert "item.version" in html

def test_batch_status_reports_incomplete_and_completed_without_running_work(tmp_path, monkeypatch):
    import json
    from app import batch_status_routes

    journal = tmp_path / "batch_import_results.json"
    monkeypatch.setattr(batch_status_routes, "JOURNAL_PATH", journal)
    missing = client.get("/api/batches/status")
    assert missing.status_code == 200
    assert missing.json()["available"] is False

    journal.write_text(json.dumps({
        "schema_version": "m5g-1", "mode": "apply", "completed": False,
        "manifest_sha256": "a" * 64,
        "expected_sku_ids": ["sku-one", "sku-two"], "expected_sku_count": 2,
        "rows": {"sku-one": {"poster_id": "b" * 32, "version": 1}},
    }), encoding="utf-8")
    partial = client.get("/api/batches/status")
    assert partial.status_code == 200
    assert partial.json()["completed"] is False
    assert partial.json()["completed_sku_count"] == 1
    assert partial.json()["pending_sku_count"] == 1
    assert partial.json()["rows"] == [
        {"sku_id": "sku-one", "status": "COMPLETED"},
        {"sku_id": "sku-two", "status": "PENDING"},
    ]

    state = json.loads(journal.read_text(encoding="utf-8"))
    state["rows"]["sku-two"] = {"poster_id": "c" * 32, "version": 1}
    state["completed"] = True
    journal.write_text(json.dumps(state), encoding="utf-8")
    complete = client.get("/api/batches/status")
    assert complete.status_code == 200
    assert complete.json()["completed"] is True
    assert complete.json()["completed_sku_count"] == 2


def test_batch_status_rejects_forged_or_malformed_journal(tmp_path, monkeypatch):
    import json
    from app import batch_status_routes

    journal = tmp_path / "batch_import_results.json"
    monkeypatch.setattr(batch_status_routes, "JOURNAL_PATH", journal)
    journal.write_text("{broken", encoding="utf-8")
    assert client.get("/api/batches/status").status_code == 422
    journal.write_text(json.dumps({
        "schema_version": "m5g-1", "mode": "apply", "completed": True,
        "expected_sku_ids": ["sku-one"], "expected_sku_count": 1,
        "rows": {"foreign-sku": {"poster_id": "b" * 32, "version": 1}},
    }), encoding="utf-8")
    assert client.get("/api/batches/status").status_code == 422


def test_batch_progress_panel_is_read_only_and_exposes_status_controls():
    response = client.get("/")
    assert response.status_code == 200
    assert 'id="refreshBatchStatus"' in response.text
    assert 'id="batchProgress"' in response.text
    assert "/api/batches/status" in response.text

