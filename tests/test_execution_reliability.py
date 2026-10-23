"""M2c proof: once-only claim, durable event trail, and UNKNOWN on uncertain paid call."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient

from app.main import app
from app import execution_store

client = TestClient(app)

def data(price="199", subtitle="品质生活 · 限时优惠"):
    return {"title": "双十一电动牙刷", "price": price, "subtitle": subtitle,
            "style": "蓝色科技风", "background_mode": "local"}


def post(product_bytes, key, price="199", subtitle="品质生活 · 限时优惠"):
    return client.post(
        "/api/posters",
        headers={"Idempotency-Key": key},
        files={"product": ("sku.png", product_bytes, "image/png")},
        data=data(price, subtitle),
    )


def test_same_idempotency_key_returns_same_poster(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    first = post(product_bytes, "m2c-test-key-01")
    assert first.status_code == 201, first.text
    second = post(product_bytes, "m2c-test-key-01")
    assert second.status_code == 201
    assert first.json()["poster_id"] == second.json()["poster_id"]
    assert len(list((tmp_path / "posters").iterdir())) == 1
    view = client.get("/api/executions/m2c-test-key-01")
    assert view.status_code == 200
    assert view.json()["status"] == "COMPLETED"
    assert [e["event_type"] for e in view.json()["events"]] == ["CLAIMED", "COMPLETED"]


def test_same_key_different_input_is_conflict(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    assert post(product_bytes, "m2c-test-key-02").status_code == 201
    assert post(product_bytes, "m2c-test-key-02", price="188").status_code == 409
    assert client.get("/api/executions/m2c-test-key-02").json()["result"]["spec"]["price"] == "199"


def test_unknown_result_never_automatically_retries_paid_model(tmp_path, monkeypatch, product_bytes):
    from app.image_provider import ImageProviderError
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    calls = {"count": 0}
    async def simulated_failure(style):
        calls["count"] += 1
        raise ImageProviderError("模拟超时，计费结果未知")
    monkeypatch.setattr("app.main.generate_ai_background", simulated_failure)
    def paid_post():
        return client.post(
            "/api/posters",
            headers={"Idempotency-Key": "m2c-unknown-key"},
            files={"product": ("sku.png", product_bytes, "image/png")},
            data={**data(), "background_mode": "ai"},
        )
    assert paid_post().status_code == 502
    state = client.get("/api/executions/m2c-unknown-key").json()
    assert state["status"] == "UNKNOWN"
    assert [event["event_type"] for event in state["events"]] == ["CLAIMED", "RESULT_UNKNOWN"]
    assert paid_post().status_code == 409
    assert calls["count"] == 1


def test_running_claim_blocks_duplicate_and_recovery_marks_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    assert execution_store.begin("m2c-running-key", "hash-1") is None
    from pytest import raises
    with raises(execution_store.ExecutionConflict):
        execution_store.begin("m2c-running-key", "hash-1")
    assert execution_store.recover_incomplete() == 1
    assert execution_store.get("m2c-running-key")["status"] == "UNKNOWN"
    assert execution_store.recover_incomplete() == 0


def test_bad_key_rejected_and_missing_execution_404(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    assert post(product_bytes, "bad").status_code == 422
    assert client.get("/api/executions/bad").status_code == 422
    assert client.get("/api/executions/nonexistent-key").status_code == 404


def test_unrenderable_copy_rejected_before_claim_or_paid_model(
    tmp_path, monkeypatch, product_bytes
):
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    calls = {"count": 0}

    async def unexpected_paid_call(style):
        calls["count"] += 1
        raise AssertionError("invalid layout must be rejected before a paid model call")

    monkeypatch.setattr("app.main.generate_ai_background", unexpected_paid_call)
    response = client.post(
        "/api/posters",
        headers={"Idempotency-Key": "m2c-invalid-layout-key"},
        files={"product": ("sku.png", product_bytes, "image/png")},
        data={**data(subtitle="超长副标题" * 9), "background_mode": "ai"},
    )

    assert response.status_code == 422
    assert "副标题" in response.json()["detail"]
    assert calls["count"] == 0
    assert client.get("/api/executions/m2c-invalid-layout-key").status_code == 404


def test_ai_requires_idempotency_key_before_provider_call(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    calls = {"count": 0}

    async def unexpected_paid_call(style):
        calls["count"] += 1
        raise AssertionError("AI mode must reject a missing idempotency key before provider dispatch")

    monkeypatch.setattr("app.main.generate_ai_background", unexpected_paid_call)
    response = client.post(
        "/api/posters",
        files={"product": ("sku.png", product_bytes, "image/png")},
        data={**data(), "background_mode": "ai"},
    )

    assert response.status_code == 428, response.text
    assert "Idempotency-Key" in response.json()["detail"]
    assert calls["count"] == 0
    assert not (tmp_path / "posters").exists()
    assert not (tmp_path / "executions.sqlite3").exists()


def test_concurrent_duplicate_paid_requests_dispatch_provider_once(
    tmp_path, monkeypatch, product_bytes
):
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    provider_started = Event()
    release_provider = Event()
    calls = {"count": 0}

    from app.compositor import local_background

    async def delayed_provider(style):
        calls["count"] += 1
        provider_started.set()
        released = await asyncio.to_thread(release_provider.wait, 8)
        assert released, "test did not release the simulated provider"
        return local_background(style)

    monkeypatch.setattr("app.main.generate_ai_background", delayed_provider)
    first_client = TestClient(app)
    second_client = TestClient(app)

    def paid_post(test_client):
        return test_client.post(
            "/api/posters",
            headers={"Idempotency-Key": "m2c-concurrent-paid-key"},
            files={"product": ("sku.png", product_bytes, "image/png")},
            data={**data(), "background_mode": "ai"},
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        first_future = pool.submit(paid_post, first_client)
        try:
            assert provider_started.wait(5), "first request never reached the provider"
            duplicate = paid_post(second_client)
        finally:
            release_provider.set()
        first = first_future.result(timeout=10)

    assert first.status_code == 201, first.text
    assert duplicate.status_code == 409, duplicate.text
    assert calls["count"] == 1
    status = second_client.get("/api/executions/m2c-concurrent-paid-key")
    assert status.status_code == 200
    assert status.json()["status"] == "COMPLETED"


def test_local_mode_remains_backward_compatible_without_idempotency_key(
    tmp_path, monkeypatch, product_bytes
):
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    response = client.post(
        "/api/posters",
        files={"product": ("sku.png", product_bytes, "image/png")},
        data=data(),
    )

    assert response.status_code == 201, response.text
    assert response.json()["background_mode"] == "local"


def test_recent_execution_history_lists_completed_and_unknown_summaries(
    tmp_path, monkeypatch, product_bytes
):
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))

    completed = post(product_bytes, "m2c-history-done")
    assert completed.status_code == 201, completed.text
    assert execution_store.begin("m2c-history-unknown", "history-hash") is None
    execution_store.mark_unknown("m2c-history-unknown")

    response = client.get("/api/executions?limit=10")

    assert response.status_code == 200
    items = {item["idempotency_key"]: item for item in response.json()["items"]}
    assert items["m2c-history-done"]["status"] == "COMPLETED"
    assert items["m2c-history-done"]["poster_id"] == completed.json()["poster_id"]
    assert items["m2c-history-done"]["version"] == 1
    assert items["m2c-history-unknown"]["status"] == "UNKNOWN"
    assert items["m2c-history-unknown"]["poster_id"] is None
    # Summary endpoint must not disclose full request/result payloads.
    assert "result" not in items["m2c-history-done"]
    assert "spec" not in items["m2c-history-done"]
    assert "image_url" not in items["m2c-history-done"]


def test_recent_execution_history_limit_is_bounded():
    assert client.get("/api/executions?limit=0").status_code == 422
    assert client.get("/api/executions?limit=51").status_code == 422


def test_paid_generation_concurrency_limit_does_not_claim_second_key(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    assert execution_store.begin(
        "m2c-paid-active-01",
        "hash-1",
        paid=True,
        max_paid_per_hour=20,
        max_concurrent_paid=1,
    ) is None

    from pytest import raises
    with raises(execution_store.PaidGenerationLimitExceeded, match="并发上限"):
        execution_store.begin(
            "m2c-paid-active-02",
            "hash-2",
            paid=True,
            max_paid_per_hour=20,
            max_concurrent_paid=1,
        )

    with raises(LookupError):
        execution_store.get("m2c-paid-active-02")


def test_paid_generation_hourly_limit_rejects_before_second_provider_call(
    tmp_path, monkeypatch, product_bytes
):
    from PIL import Image

    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.setenv("POSTER_MAX_AI_GENERATIONS_PER_HOUR", "1")
    monkeypatch.setenv("POSTER_MAX_CONCURRENT_AI_GENERATIONS", "1")
    calls = {"count": 0}

    async def fake_paid_generation(style):
        calls["count"] += 1
        return Image.new("RGB", (1024, 1024), "#183050")

    monkeypatch.setattr("app.main.generate_ai_background", fake_paid_generation)

    def paid_post(key):
        return client.post(
            "/api/posters",
            headers={"Idempotency-Key": key},
            files={"product": ("sku.png", product_bytes, "image/png")},
            data={**data(), "background_mode": "ai"},
        )

    first = paid_post("m2c-paid-hour-01")
    second = paid_post("m2c-paid-hour-02")

    assert first.status_code == 201, first.text
    assert second.status_code == 429
    assert "每小时请求上限" in second.json()["detail"]
    assert calls["count"] == 1
    assert client.get("/api/executions/m2c-paid-hour-02").status_code == 404

def test_ai_mode_rejects_invalid_key_before_upload_decode(tmp_path, monkeypatch):
    execution_db = tmp_path / "executions.sqlite3"
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(execution_db))
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))

    response = client.post(
        "/api/posters",
        headers={"Idempotency-Key": "bad"},
        files={"product": ("broken.png", b"not-an-image", "image/png")},
        data={**data(), "background_mode": "ai"},
    )

    assert response.status_code == 422
    assert "Idempotency-Key" in response.json()["detail"]
    assert not execution_db.exists()
    assert not (tmp_path / "posters").exists()


def test_paid_budget_backfills_recent_completed_legacy_ai_runs(tmp_path, monkeypatch):
    import json
    import sqlite3
    from datetime import datetime, timezone

    execution_db = tmp_path / "executions.sqlite3"
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(execution_db))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    legacy_result = json.dumps({
        "poster_id": "legacy-poster-001",
        "version": 1,
        "background_mode": "ai",
    })

    # Build the pre-guardrail ledger schema: no paid events or migration table.
    with sqlite3.connect(execution_db) as conn:
        conn.execute(
            """CREATE TABLE poster_executions (
                idempotency_key TEXT PRIMARY KEY,
                request_hash TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('RUNNING','COMPLETED','UNKNOWN')),
                result_json TEXT,
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "INSERT INTO poster_executions VALUES (?, ?, 'COMPLETED', ?, ?, ?)",
            ("m2c-legacy-paid-01", "legacy-hash", legacy_result, now, now),
        )

    from pytest import raises
    with raises(execution_store.PaidGenerationLimitExceeded, match="每小时请求上限"):
        execution_store.begin(
            "m2c-paid-after-migration",
            "new-hash",
            paid=True,
            max_paid_per_hour=1,
            max_concurrent_paid=1,
        )

    with raises(LookupError):
        execution_store.get("m2c-paid-after-migration")

def test_manual_reconcile_completed_poster_after_ledger_finish_failure(
    tmp_path, monkeypatch, product_bytes
):
    """A saved poster can be recovered after its SQLite final commit failed."""
    from app import storage

    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    key = "receipt-post-commit-failure"

    def fail_ledger_finish(_key, _result):
        raise RuntimeError("simulated crash window after saving files")

    with monkeypatch.context() as patch:
        patch.setattr(execution_store, "finish", fail_ledger_finish)
        first = post(product_bytes, key)
    assert first.status_code == 500
    assert execution_store.get(key)["status"] == "UNKNOWN"
    ids = [p.name for p in (tmp_path / "posters").iterdir() if p.is_dir()]
    assert len(ids) == 1

    repaired = client.post(f"/api/executions/{key}/reconcile")
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()["status"] == "COMPLETED"
    assert repaired.json()["result"]["poster_id"] == ids[0]
    assert repaired.json()["result"] == storage.view(ids[0])
    assert "RECOVERED_FROM_ARTIFACT" in [
        event["event_type"] for event in repaired.json()["events"]
    ]

    second = post(product_bytes, key)
    assert second.status_code == 201, second.text
    assert second.json()["poster_id"] == ids[0]
    assert len(list((tmp_path / "posters").iterdir())) == 1


def test_startup_recovers_matching_finished_artifact_without_model_call(
    tmp_path, monkeypatch
):
    import io
    from PIL import Image
    from app import storage
    from app.schemas import PosterSpec

    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    key, request_hash = "receipt-startup-01", "matching-request-hash"
    assert execution_store.begin(key, request_hash) is None
    spec = PosterSpec(title="演示商品", price="199", background_mode="local")
    product = Image.new("RGBA", (260, 400), (25, 65, 105, 255))
    background = Image.new("RGB", (1024, 1024), "#123456")
    rendered = io.BytesIO()
    background.save(rendered, format="PNG")
    saved = storage.create(
        spec, product, None, background, rendered.getvalue(),
        idempotency_key=key, request_hash=request_hash,
    )
    assert execution_store.get(key)["status"] == "RUNNING"
    assert execution_store.recover_incomplete() == 1
    recovered = execution_store.get(key)
    assert recovered["status"] == "COMPLETED"
    assert recovered["result"] == saved
    assert [event["event_type"] for event in recovered["events"]] == [
        "CLAIMED", "RECOVERED_FROM_ARTIFACT",
    ]
    assert execution_store.recover_incomplete() == 0


def test_corrupt_artifact_remains_unknown_without_paid_retry(
    tmp_path, monkeypatch, product_bytes
):
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    key = "receipt-corrupt-artifact"

    def fail_ledger_finish(_key, _result):
        raise RuntimeError("simulate interrupted SQLite completion")

    with monkeypatch.context() as patch:
        patch.setattr(execution_store, "finish", fail_ledger_finish)
        assert post(product_bytes, key).status_code == 500

    poster = next((tmp_path / "posters").iterdir())
    (poster / "versions" / "v1.png").write_bytes(b"corrupted-png")
    response = client.post(f"/api/executions/{key}/reconcile")
    assert response.status_code == 200
    assert response.json()["status"] == "UNKNOWN"
    assert response.json()["result"] is None
    assert post(product_bytes, key).status_code == 409


def test_unmatched_request_receipt_never_promoted(tmp_path, monkeypatch):
    import io
    from PIL import Image
    from app import storage
    from app.schemas import PosterSpec

    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    key = "receipt-wrong-request"
    assert execution_store.begin(key, "original-hash") is None
    spec = PosterSpec(title="演示商品", price="199", background_mode="local")
    product = Image.new("RGBA", (260, 400), (25, 65, 105, 255))
    background = Image.new("RGB", (1024, 1024), "#123456")
    png = io.BytesIO()
    background.save(png, format="PNG")
    storage.create(
        spec, product, None, background, png.getvalue(),
        idempotency_key=key, request_hash="another-request",
    )
    assert execution_store.recover_incomplete() == 1
    assert execution_store.get(key)["status"] == "UNKNOWN"
    assert client.post(f"/api/executions/{key}/reconcile").json()["status"] == "UNKNOWN"


def test_reconcile_missing_execution_returns_404(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    response = client.post("/api/executions/receipt-missing-key/reconcile")
    assert response.status_code == 404

def test_paid_generation_unknown_to_completed_e2e_without_second_model_call(
    tmp_path, monkeypatch, product_bytes
):
    """POST AI -> uncertain ledger -> UNKNOWN -> reconcile -> GET -> idempotent retry."""
    from PIL import Image

    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.setenv("POSTER_MAX_AI_GENERATIONS_PER_HOUR", "20")
    monkeypatch.setenv("POSTER_MAX_CONCURRENT_AI_GENERATIONS", "1")
    calls = {"model": 0}

    async def fake_provider(_style):
        calls["model"] += 1
        return Image.new("RGB", (1024, 1024), "#194166")

    monkeypatch.setattr("app.main.generate_ai_background", fake_provider)
    key = "receipt-paid-e2e-01"

    def paid_post():
        return client.post(
            "/api/posters",
            headers={"Idempotency-Key": key},
            files={"product": ("sku.png", product_bytes, "image/png")},
            data={**data(), "background_mode": "ai"},
        )

    def simulated_interrupted_finish(_key, _result):
        raise RuntimeError("simulated interrupted final SQLite commit")

    with monkeypatch.context() as patch:
        patch.setattr(execution_store, "finish", simulated_interrupted_finish)
        first = paid_post()

    assert first.status_code == 500, first.text
    assert client.get(f"/api/executions/{key}").json()["status"] == "UNKNOWN"
    assert paid_post().status_code == 409
    assert calls["model"] == 1

    recovered = client.post(f"/api/executions/{key}/reconcile")
    assert recovered.status_code == 200, recovered.text
    body = recovered.json()
    assert body["status"] == "COMPLETED"
    assert body["result"]["background_mode"] == "ai"
    poster_id = body["result"]["poster_id"]

    image = client.get(body["result"]["image_url"])
    assert image.status_code == 200
    assert image.content.startswith(b"\x89PNG\r\n\x1a\n")
    repeat = paid_post()
    assert repeat.status_code == 201
    assert repeat.json()["poster_id"] == poster_id
    assert calls["model"] == 1

    history = client.get("/api/executions?limit=10")
    assert history.status_code == 200
    assert any(
        item["idempotency_key"] == key and item["status"] == "COMPLETED"
        for item in history.json()["items"]
    )

def test_execution_history_status_filter_and_pagination(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))

    # Create deterministic local execution records without generating any images.
    for i in range(7):
        key = f"history-page-test-{i:02d}"
        assert execution_store.begin(key, f"hash-{i}") is None
        if i < 5:
            execution_store.mark_unknown(key)
        else:
            execution_store.finish(key, {
                "poster_id": "a" * 32, "version": 1, "background_mode": "local"
            })

    first = client.get("/api/executions?limit=2&offset=0&status=UNKNOWN")
    second = client.get("/api/executions?limit=2&offset=2&status=UNKNOWN")
    third = client.get("/api/executions?limit=2&offset=4&status=UNKNOWN")

    assert all(response.status_code == 200 for response in (first, second, third))
    one, two, three = (r.json() for r in (first, second, third))
    assert [len(p["items"]) for p in (one, two, three)] == [2, 2, 1]
    assert [p["has_more"] for p in (one, two, three)] == [True, True, False]
    assert one["offset"] == 0 and two["offset"] == 2 and three["offset"] == 4
    assert all(item["status"] == "UNKNOWN" for p in (one, two, three) for item in p["items"])
    keys = [item["idempotency_key"] for p in (one, two, three) for item in p["items"]]
    assert len(set(keys)) == 5

    completed = client.get("/api/executions?limit=10&status=COMPLETED")
    assert completed.status_code == 200
    assert len(completed.json()["items"]) == 2
    assert not completed.json()["has_more"]
    assert all(item["poster_id"] == "a" * 32 for item in completed.json()["items"])
    assert all(
        "result" not in item and "request_hash" not in item
        for item in completed.json()["items"]
    )


def test_execution_history_rejects_unbounded_or_invalid_filters(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    bad_queries = (
        "limit=0", "limit=51", "offset=-1", "offset=10001",
        "status=FAILED", "status=UNKNOWN%27%20OR%201%3D1",
    )
    assert all(
        client.get("/api/executions?" + params).status_code == 422
        for params in bad_queries
    )
    # The old API contract remains valid for existing callers.
    response = client.get("/api/executions?limit=10")
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["offset"] == 0
    assert response.json()["has_more"] is False

