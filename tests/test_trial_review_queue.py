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



def test_review_queue_keeps_classic_and_product_focus_statuses_separate(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _create(tmp_path, monkeypatch, product_bytes)
    classic_id = "feed_4x5_native"
    focus_id = "feed_4x5_native_product_focus"
    url = "/api/trials/review-queue"

    before = client.get(url, params={"reviewer": REVIEWER, "variant": focus_id})
    assert before.status_code == 200, before.text
    assert before.json()["variant_id"] == focus_id
    assert before.json()["latest_status_counts"] == {"NEEDS_REVIEW": 1}
    assert before.json()["items"][0]["native_image_url"].endswith(
        "/exports/" + focus_id
    )

    focus_review = (
        f"/api/posters/{pid}/versions/1/exports/{focus_id}/human-review"
    )
    context = client.get(focus_review, params={"reviewer": REVIEWER})
    assert context.status_code == 200, context.text
    info = context.json()
    payload = {
        "reviewer": REVIEWER,
        "image_sha256": info["image_sha256"],
        "source_image_sha256": info["source_image_sha256"],
        "spec_sha256": info["spec_sha256"],
        "asset_sha256": info["asset_sha256"],
        "fields": {
            field: {"judgment": "correct", "reason": ""}
            for field in info["fields_to_review"]
        },
    }
    saved = client.post(focus_review, json=payload)
    assert saved.status_code == 201, saved.text

    focus_queue = client.get(
        url, params={"reviewer": REVIEWER, "variant": focus_id}
    ).json()
    classic_queue = client.get(
        url, params={"reviewer": REVIEWER, "variant": classic_id}
    ).json()
    default_queue = _queue().json()
    assert focus_queue["latest_status_counts"] == {"OCR_MISSING": 1}
    assert classic_queue["latest_status_counts"] == {"NEEDS_REVIEW": 1}
    assert default_queue["variant_id"] == classic_id
    assert default_queue["latest_status_counts"] == {"NEEDS_REVIEW": 1}
    assert focus_queue["automatically_approved"] is False

    rev = client.post(f"/api/posters/{pid}/revisions", json={"price": "189"})
    assert rev.status_code == 201, rev.text
    after = client.get(
        url, params={"reviewer": REVIEWER, "variant": focus_id}
    ).json()
    assert after["items"][0]["version"] == 2
    assert after["latest_status_counts"] == {"NEEDS_REVIEW": 1}


def test_variant_aware_queue_rejects_unknown_variants_and_wires_editor():
    response = client.get(
        "/api/trials/review-queue",
        params={"reviewer": REVIEWER, "variant": "random_square"},
    )
    assert response.status_code == 422

    page = client.get("/")
    assert page.status_code == 200
    html = page.text
    assert 'id="reviewQueueVariant"' in html
    assert 'value="feed_4x5_native_product_focus"' in html
    assert "variant='+encodeURIComponent(variant)" in html
    assert "$('channelPreset').value=variant;display(poster)" in html
