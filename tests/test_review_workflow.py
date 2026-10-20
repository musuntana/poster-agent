"""M2b: checkpoint persistence, missing fields, idempotent approval, and rejection."""
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
BRIEF = "主标题：双十一电动牙刷，活动价199元，原价399元，风格：科技蓝色"
SPEC = {
    "title": "双十一电动牙刷",
    "subtitle": "品质生活 · 限时优惠",
    "price": "199",
    "original_price": "399",
    "style": "科技蓝色",
    "cta": "立即抢购",
    "background_mode": "local",
}


def create(tmp_path, monkeypatch, brief=BRIEF):
    monkeypatch.setenv("POSTER_CHECKPOINT_DB", str(tmp_path / "checkpoints.sqlite3"))
    response = client.post("/api/briefs/runs", json={"brief": brief, "compiler": "demo"})
    assert response.status_code == 201, response.text
    return response.json()


def test_checkpoint_survives_reopening_and_human_approval(tmp_path, monkeypatch):
    created = create(tmp_path, monkeypatch)
    assert created["status"] == "WAITING_CONFIRMATION"
    assert created["pending_approval"]
    assert created["background_generation_started"] is False
    assert (tmp_path / "checkpoints.sqlite3").exists()
    run_id = created["run_id"]
    assert len(run_id) == 32
    # Two independent HTTP requests each reopen a fresh SQLite connection.
    got = client.get(f"/api/briefs/runs/{run_id}")
    assert got.status_code == 200
    assert got.json()["draft"]["fields"]["price"] == "199"
    approved = client.post(f"/api/briefs/runs/{run_id}/decision",
                           json={"approved": True, "spec": SPEC})
    assert approved.status_code == 200, approved.text
    result = approved.json()
    assert result["status"] == "APPROVED"
    assert result["pending_approval"] is False
    assert result["confirmed_spec"]["price"] == "199"
    reloaded = client.get(f"/api/briefs/runs/{run_id}")
    assert reloaded.json()["confirmed_spec"] == result["confirmed_spec"]
    identical = client.post(f"/api/briefs/runs/{run_id}/decision",
                            json={"approved": True, "spec": SPEC})
    assert identical.status_code == 200  # idempotent duplicate
    modified = {**SPEC, "price": "189"}
    conflict = client.post(f"/api/briefs/runs/{run_id}/decision",
                           json={"approved": True, "spec": modified})
    assert conflict.status_code == 409
    assert client.get(f"/api/briefs/runs/{run_id}").json()["confirmed_spec"]["price"] == "199"


def test_missing_price_waits_for_manual_completion(tmp_path, monkeypatch):
    created = create(tmp_path, monkeypatch, "主标题：双十一电动牙刷，请设计有高级感的促销海报")
    assert created["status"] == "WAITING_INPUT"
    assert "price" in created["draft"]["missing_fields"]
    assert client.post(f"/api/briefs/runs/{created['run_id']}/decision",
                       json={"approved": True}).status_code == 422
    completed = client.post(f"/api/briefs/runs/{created['run_id']}/decision",
                            json={"approved": True, "spec": SPEC})
    assert completed.status_code == 200
    assert completed.json()["status"] == "APPROVED"


def test_rejection_is_terminal(tmp_path, monkeypatch):
    created = create(tmp_path, monkeypatch)
    url = f"/api/briefs/runs/{created['run_id']}/decision"
    response = client.post(url, json={"approved": False})
    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"
    assert client.post(url, json={"approved": False}).status_code == 200
    assert client.post(url, json={"approved": True, "spec": SPEC}).status_code == 409


def test_unrecognized_run_returns_404(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_CHECKPOINT_DB", str(tmp_path / "checkpoints.sqlite3"))
    assert client.get("/api/briefs/runs/invalid-id").status_code == 404
    assert client.post("/api/briefs/runs/" + "0"*32 + "/decision",
                       json={"approved": False}).status_code == 404


def test_confirmed_spec_must_respect_business_price_constraints(tmp_path, monkeypatch):
    created = create(tmp_path, monkeypatch)
    invalid = {**SPEC, "original_price": "99"}
    response = client.post(f"/api/briefs/runs/{created['run_id']}/decision",
                           json={"approved": True, "spec": invalid})
    assert response.status_code == 422
    assert client.get(f"/api/briefs/runs/{created['run_id']}").json()["pending_approval"]


def test_review_gate_blocks_unapproved_or_modified_generation(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    created = create(tmp_path, monkeypatch)
    run_id = created["run_id"]
    url = "/api/posters"
    files = {"product": ("product.png", product_bytes, "image/png")}
    data = {
        "title": SPEC["title"], "subtitle": SPEC["subtitle"], "price": SPEC["price"],
        "original_price": SPEC["original_price"], "style": SPEC["style"],
        "cta": SPEC["cta"], "background_mode": "local", "review_run_id": run_id,
    }
    pending = client.post(url, files=files, data=data)
    assert pending.status_code == 409  # before any paid model operation
    approval = client.post(f"/api/briefs/runs/{run_id}/decision",
                           json={"approved": True, "spec": SPEC})
    assert approval.status_code == 200
    changed = client.post(url, files=files, data={**data, "price": "188"})
    assert changed.status_code == 409  # confirmed spec cannot be silently modified
    generated = client.post(url, files=files, data=data)
    assert generated.status_code == 201, generated.text
    assert generated.json()["spec"]["price"] == "199"
