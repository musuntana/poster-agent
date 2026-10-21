"""M2c proof: once-only claim, durable event trail, and UNKNOWN on uncertain paid call."""
from __future__ import annotations

from fastapi.testclient import TestClient
from app.main import app
from app import execution_store

client = TestClient(app)

def data(price="199"):
    return {"title": "双十一电动牙刷", "price": price,
            "style": "蓝色科技风", "background_mode": "local"}


def post(product_bytes, key, price="199"):
    return client.post(
        "/api/posters",
        headers={"Idempotency-Key": key},
        files={"product": ("sku.png", product_bytes, "image/png")},
        data=data(price),
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
