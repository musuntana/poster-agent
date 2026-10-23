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
