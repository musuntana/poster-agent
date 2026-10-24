"""Local web batch import: full-cohort preflight, progress, and safe resume."""
from __future__ import annotations

import csv
import io
import json
from importlib import import_module

from fastapi.testclient import TestClient
from PIL import Image

from app import storage
from app.main import app

client = TestClient(app)


def _png(color):
    stream = io.BytesIO()
    Image.new("RGBA", (128, 160), color).save(stream, "PNG")
    return stream.getvalue()


def _csv(rows):
    stream = io.StringIO(newline="")
    fields = [
        "sku_id", "product_image", "title", "price",
        "original_price", "subtitle", "cta", "style", "logo_image",
    ]
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _rows():
    return [
        {
            "sku_id": "sku-one", "product_image": "sku_one.png",
            "title": "电动牙刷促销", "price": "199", "original_price": "399",
            "subtitle": "限时福利", "cta": "立即购买", "style": "蓝色科技风",
            "logo_image": "logo.png",
        },
        {
            "sku_id": "sku-two", "product_image": "sku_two.png",
            "title": "智能生活好物", "price": "89", "original_price": "129",
            "subtitle": "品质生活", "cta": "立即抢购", "style": "蓝色科技风",
            "logo_image": "",
        },
    ]


def _upload_batch(monkeypatch, tmp_path, rows=None):
    monkeypatch.setenv("POSTER_BATCH_DIR", str(tmp_path / "batch-root"))
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    images = {
        "sku_one.png": _png((60, 110, 195, 255)),
        "sku_two.png": _png((195, 70, 45, 255)),
        "logo.png": _png((50, 200, 100, 180)),
    }
    files = [
        ("manifest", ("real_skus.csv", _csv(rows or _rows()), "text/csv")),
        *[
            ("images", (name, data, "image/png"))
            for name, data in images.items()
        ],
    ]
    response = client.post("/api/batches", files=files)
    return response


def test_web_preflight_validates_entire_cohort_without_creating_posters(
    monkeypatch, tmp_path,
):
    response = _upload_batch(monkeypatch, tmp_path)
    assert response.status_code == 201, response.text
    state = response.json()
    assert state["status"] == "READY"
    assert state["total"] == 2
    assert state["processed"] == 0
    assert state["plan"]["no_paid_model"] is True
    assert state["plan"]["unique_source_files"] == 2
    assert [row["status"] for row in state["rows"]] == ["PENDING", "PENDING"]
    assert not storage.root().exists()
    assert not (tmp_path / "executions.sqlite3").exists()
    staged = tmp_path / "batch-root" / state["batch_id"]
    assert (staged / "manifest.csv").is_file()
    assert (staged / "batch_preflight.json").is_file()


def test_web_apply_reports_progress_and_reuses_existing_m5g_journal(
    monkeypatch, tmp_path,
):
    response = _upload_batch(monkeypatch, tmp_path)
    assert response.status_code == 201, response.text
    batch_id = response.json()["batch_id"]

    def forbid_paid_provider(*_args, **_kwargs):
        raise AssertionError("web batch import must never call Qwen")

    monkeypatch.setattr("app.main.generate_ai_background", forbid_paid_provider)
    started = client.post(f"/api/batches/{batch_id}/apply")
    assert started.status_code == 202, started.text
    assert started.json()["status"] == "RUNNING"

    finished = client.get(f"/api/batches/{batch_id}")
    assert finished.status_code == 200
    state = finished.json()
    assert state["status"] == "COMPLETED"
    assert state["processed"] == state["total"] == 2
    assert all(row["status"] == "COMPLETED" for row in state["rows"])
    poster_ids = {row["sku_id"]: row["poster_id"] for row in state["rows"]}
    assert len(set(poster_ids.values())) == 2

    batch_dir = tmp_path / "batch-root" / batch_id
    journal = json.loads((batch_dir / "batch_import_results.json").read_text())
    assert journal["completed"] is True
    assert journal["expected_sku_ids"] == ["sku-one", "sku-two"]
    assert journal["rows"]["sku-one"]["poster_id"] == poster_ids["sku-one"]
    assert all(
        storage.view(poster_id)["spec"]["background_mode"] == "local"
        for poster_id in poster_ids.values()
    )

    again = client.post(f"/api/batches/{batch_id}/apply")
    assert again.status_code == 202
    after = client.get(f"/api/batches/{batch_id}").json()
    assert after["status"] == "COMPLETED"
    assert {row["sku_id"]: row["poster_id"] for row in after["rows"]} == poster_ids
    assert len(list(storage.root().iterdir())) == 2


def test_failed_web_batch_can_resume_without_duplicate_posters(monkeypatch, tmp_path):
    response = _upload_batch(monkeypatch, tmp_path)
    assert response.status_code == 201, response.text
    batch_id = response.json()["batch_id"]
    batch = import_module("scripts.real_sku_batch")
    original = batch._file_parts
    calls = {"count": 0}

    def fail_second_row(item):
        calls["count"] += 1
        if calls["count"] == 2:
            raise batch.BatchError("simulated operator interruption")
        return original(item)

    monkeypatch.setattr(batch, "_file_parts", fail_second_row)
    assert client.post(f"/api/batches/{batch_id}/apply").status_code == 202
    failed = client.get(f"/api/batches/{batch_id}").json()
    assert failed["status"] == "FAILED"
    assert failed["processed"] == 1
    first_id = next(row["poster_id"] for row in failed["rows"] if row["sku_id"] == "sku-one")
    assert next(row for row in failed["rows"] if row["sku_id"] == "sku-two")["status"] == "FAILED"
    assert len(list(storage.root().iterdir())) == 1

    monkeypatch.setattr(batch, "_file_parts", original)
    assert client.post(f"/api/batches/{batch_id}/apply").status_code == 202
    complete = client.get(f"/api/batches/{batch_id}").json()
    assert complete["status"] == "COMPLETED"
    assert complete["processed"] == 2
    assert next(row for row in complete["rows"] if row["sku_id"] == "sku-one")["poster_id"] == first_id
    assert len(list(storage.root().iterdir())) == 2


def test_web_preflight_rejects_path_traversal_before_any_poster(
    monkeypatch, tmp_path,
):
    rows = _rows()[:1]
    rows[0]["product_image"] = "../outside.png"
    monkeypatch.setenv("POSTER_BATCH_DIR", str(tmp_path / "batch-root"))
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    files = [
        ("manifest", ("manifest.csv", _csv(rows), "text/csv")),
        ("images", ("outside.png", _png((1, 2, 3, 255)), "image/png")),
        ("images", ("logo.png", _png((4, 5, 6, 255)), "image/png")),
    ]
    response = client.post("/api/batches", files=files)
    assert response.status_code == 422
    assert "目录之外" in response.json()["detail"]
    assert not storage.root().exists()
    assert not (tmp_path / "executions.sqlite3").exists()
    assert list((tmp_path / "batch-root").iterdir()) == []


def test_web_preflight_rejects_unused_images_and_duplicate_names(monkeypatch, tmp_path):
    monkeypatch.setenv("POSTER_BATCH_DIR", str(tmp_path / "batch-root"))
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    files = [
        ("manifest", ("manifest.csv", _csv(_rows()[:1]), "text/csv")),
        ("images", ("sku_one.png", _png((1, 2, 3, 255)), "image/png")),
        ("images", ("logo.png", _png((4, 5, 6, 255)), "image/png")),
        ("images", ("unused.png", _png((7, 8, 9, 255)), "image/png")),
    ]
    response = client.post("/api/batches", files=files)
    assert response.status_code == 422
    assert "未被 CSV 引用" in response.json()["detail"]
    assert list((tmp_path / "batch-root").iterdir()) == []

    duplicate = [
        ("manifest", ("manifest.csv", _csv(_rows()[:1]), "text/csv")),
        ("images", ("sku_one.png", _png((1, 2, 3, 255)), "image/png")),
        ("images", ("SKU_ONE.PNG", _png((4, 5, 6, 255)), "image/png")),
    ]
    response = client.post("/api/batches", files=duplicate)
    assert response.status_code == 422
    assert "文件名重复" in response.json()["detail"]


def test_web_batch_ui_exposes_explicit_preflight_apply_and_progress_controls():
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    for element in (
        'id="batchManifest"', 'id="batchImages"', 'id="preflightBatch"',
        'id="applyBatch"', 'id="batchProgress"', 'id="batchRows"',
    ):
        assert element in html
    assert "/api/batches" in html
    assert "restoreLastBatch" in html
    assert "scheduleBatchPoll" in html
    assert "只有点击生成后才开始处理" in html
    assert "不调用 Qwen 生图或 OCR" in html

def test_batch_status_does_not_overwrite_completion_from_a_stale_running_snapshot(
    monkeypatch, tmp_path,
):
    response = _upload_batch(monkeypatch, tmp_path)
    assert response.status_code == 201
    batch_id = response.json()["batch_id"]
    batch_dir = tmp_path / "batch-root" / batch_id
    status_path = batch_dir / "batch_status.json"
    state = json.loads(status_path.read_text(encoding="utf-8"))
    state["status"] = "COMPLETED"
    status_path.write_text(json.dumps(state), encoding="utf-8")

    routes = import_module("app.batch_import_routes")
    original_read_state = routes._read_state
    calls = {"count": 0}

    def stale_first_read(path):
        calls["count"] += 1
        actual = original_read_state(path)
        return {**actual, "status": "RUNNING"} if calls["count"] == 1 else actual

    monkeypatch.setattr(routes, "_read_state", stale_first_read)
    response = client.get(f"/api/batches/{batch_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"
    assert json.loads(status_path.read_text(encoding="utf-8"))["status"] == "COMPLETED"




def test_web_batch_review_scope_rejects_unfinished_and_returns_exact_completed_cohort(
    monkeypatch, tmp_path,
):
    staged = _upload_batch(monkeypatch, tmp_path)
    assert staged.status_code == 201, staged.text
    batch_id = staged.json()["batch_id"]
    url = f"/api/batches/{batch_id}/review-scope"

    premature = client.get(url)
    assert premature.status_code == 409
    assert not storage.root().exists()

    started = client.post(f"/api/batches/{batch_id}/apply")
    assert started.status_code == 202, started.text

    completed = client.get(url)
    assert completed.status_code == 200, completed.text
    body = completed.json()
    assert body["batch_id"] == batch_id
    assert body["completed"] is True
    assert body["total_skus"] == 2
    assert len(body["manifest_sha256"]) == 64
    assert [row["sku_id"] for row in body["rows"]] == ["sku-one", "sku-two"]
    assert len({row["poster_id"] for row in body["rows"]}) == 2
    assert all(len(row["poster_id"]) == 32 for row in body["rows"])
    assert "request_sha256" not in completed.text
    assert "product_sha256" not in completed.text
    assert "idempotency_key" not in completed.text
    assert str(tmp_path) not in completed.text

    reviewed_ids = {row["poster_id"] for row in body["rows"]}
    queue = client.get("/api/trials/review-queue?reviewer=local-operator")
    assert queue.status_code == 200
    assert reviewed_ids.issubset({
        item["poster_id"] for item in queue.json()["items"]
    })


def test_web_batch_review_scope_rejects_corrupt_or_inconsistent_journal(
    monkeypatch, tmp_path,
):
    staged = _upload_batch(monkeypatch, tmp_path)
    assert staged.status_code == 201, staged.text
    batch_id = staged.json()["batch_id"]
    assert client.post(f"/api/batches/{batch_id}/apply").status_code == 202
    batch_dir = tmp_path / "batch-root" / batch_id
    journal = batch_dir / "batch_import_results.json"
    original = json.loads(journal.read_text(encoding="utf-8"))

    mismatches = []
    missing_row = json.loads(json.dumps(original))
    del missing_row["rows"]["sku-two"]
    mismatches.append(missing_row)
    duplicate_poster = json.loads(json.dumps(original))
    duplicate_poster["rows"]["sku-two"]["poster_id"] = (
        duplicate_poster["rows"]["sku-one"]["poster_id"]
    )
    mismatches.append(duplicate_poster)
    changed_manifest = json.loads(json.dumps(original))
    changed_manifest["manifest_sha256"] = "f" * 64
    mismatches.append(changed_manifest)
    bad_version = json.loads(json.dumps(original))
    bad_version["rows"]["sku-one"]["version"] = 999
    mismatches.append(bad_version)

    for invalid in mismatches:
        journal.write_text(json.dumps(invalid), encoding="utf-8")
        assert client.get(f"/api/batches/{batch_id}/review-scope").status_code == 409

    journal.write_text("{broken", encoding="utf-8")
    assert client.get(f"/api/batches/{batch_id}/review-scope").status_code == 409
    journal.write_text(json.dumps(original), encoding="utf-8")
    assert client.get(f"/api/batches/{batch_id}/review-scope").status_code == 200


def test_web_batch_handoff_ui_reuses_existing_review_queue():
    page = client.get("/")
    assert page.status_code == 200
    html = page.text
    assert 'id="reviewCurrentBatch"' in html
    assert 'id="batchReviewScopeStatus"' in html
    assert "/review-scope" in html
    assert "batchReviewScope={ids:" in html
    assert "loadReviewQueue" in html
    assert "当前批次已完成" in html


def test_web_batch_history_listing_filters_pages_without_exposing_inputs(
    tmp_path, monkeypatch,
):
    first = _upload_batch(monkeypatch, tmp_path)
    second = _upload_batch(monkeypatch, tmp_path)
    third = _upload_batch(monkeypatch, tmp_path)
    assert all(r.status_code == 201 for r in (first, second, third))
    ids = [r.json()["batch_id"] for r in (first, second, third)]
    root = tmp_path / "batch-root"
    for index, batch_id in enumerate(ids):
        status_path = root / batch_id / "batch_status.json"
        state = json.loads(status_path.read_text(encoding="utf-8"))
        state["updated_at"] = f"2026-10-24T0{index}:00:00+00:00"
        state["status"] = ["READY", "COMPLETED", "FAILED"][index]
        state["processed"] = index
        status_path.write_text(json.dumps(state), encoding="utf-8")

    response = client.get("/api/batches?limit=2")
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["limit"] == 2 and page["offset"] == 0
    assert page["has_more"] is True
    assert [item["batch_id"] for item in page["items"]] == ids[::-1][:2]
    assert [item["status"] for item in page["items"]] == ["FAILED", "COMPLETED"]
    assert all(
        set(item) == {
            "batch_id", "status", "created_at", "updated_at", "processed", "total"
        } for item in page["items"]
    )
    assert "sku-one" not in response.text
    assert "manifest_sha256" not in response.text
    assert str(tmp_path) not in response.text

    last = client.get("/api/batches?limit=2&offset=2").json()
    assert last["has_more"] is False
    assert [item["batch_id"] for item in last["items"]] == ids[:1]
    only_completed = client.get("/api/batches?status=COMPLETED").json()
    assert [item["batch_id"] for item in only_completed["items"]] == ids[1:2]
    assert only_completed["has_more"] is False


def test_web_batch_history_skips_corrupt_and_symlinked_directories(
    tmp_path, monkeypatch,
):
    first = _upload_batch(monkeypatch, tmp_path)
    assert first.status_code == 201
    root = tmp_path / "batch-root"
    valid = first.json()["batch_id"]
    broken = root / ("f" * 32)
    broken.mkdir()
    (broken / "batch_status.json").write_text("{broken", encoding="utf-8")
    wrong_type = root / ("d" * 32)
    wrong_type.mkdir()
    state = json.loads((root / valid / "batch_status.json").read_text(encoding="utf-8"))
    state["batch_id"] = wrong_type.name
    state["status"] = []
    (wrong_type / "batch_status.json").write_text(json.dumps(state), encoding="utf-8")
    outside = tmp_path / "unrelated"
    outside.mkdir()
    (outside / "private.txt").write_text("do not read", encoding="utf-8")
    (root / ("e" * 32)).symlink_to(outside, target_is_directory=True)
    (root / "notes.txt").write_text("unrelated", encoding="utf-8")

    response = client.get("/api/batches")
    assert response.status_code == 200, response.text
    data = response.json()
    assert [item["batch_id"] for item in data["items"]] == [valid]
    assert data["skipped_invalid"] == 2
    assert "private.txt" not in response.text
    assert (outside / "private.txt").read_text() == "do not read"


def test_web_batch_history_reports_abandoned_running_without_mutating_disk(
    tmp_path, monkeypatch,
):
    response = _upload_batch(monkeypatch, tmp_path)
    assert response.status_code == 201
    batch_id = response.json()["batch_id"]
    status_path = tmp_path / "batch-root" / batch_id / "batch_status.json"
    original = json.loads(status_path.read_text(encoding="utf-8"))
    original["status"] = "RUNNING"
    status_path.write_text(json.dumps(original), encoding="utf-8")
    before = status_path.read_bytes()

    listing = client.get("/api/batches?status=INTERRUPTED")
    assert listing.status_code == 200
    assert [(x["batch_id"], x["status"]) for x in listing.json()["items"]] == [
        (batch_id, "INTERRUPTED")
    ]
    assert status_path.read_bytes() == before
    assert client.get("/api/batches?status=RUNNING").json()["items"] == []


def test_web_batch_history_validates_page_and_status_and_does_not_create_root(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("POSTER_BATCH_DIR", str(tmp_path / "not-created"))
    good = client.get("/api/batches")
    assert good.status_code == 200
    assert good.json()["items"] == []
    assert good.json()["has_more"] is False
    assert not (tmp_path / "not-created").exists()
    for params in (
        "limit=0", "limit=51", "offset=-1", "offset=10001",
        "status=UNKNOWN", "status=FAILED%27%20OR%201%3D1",
    ):
        response = client.get("/api/batches?" + params)
        assert response.status_code == 422, (params, response.text)


def test_web_batch_history_ui_can_switch_to_older_batches_without_deletion():
    page = client.get("/")
    assert page.status_code == 200
    html = page.text
    for marker in (
        'id="batchHistoryFilter"', 'id="refreshBatchHistory"',
        'id="batchHistoryPrev"', 'id="batchHistoryNext"',
        'id="batchHistoryItems"', 'id="batchHistoryInfo"',
    ):
        assert marker in html
    assert "function selectHistoricalBatch" in html
    assert "async function loadBatchHistory" in html
    assert "batchReviewScope=null" in html
    assert "不会自动删除原图或评测账本" in html
