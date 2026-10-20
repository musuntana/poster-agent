from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_brief_demo_endpoint_is_review_only():
    response = client.post("/api/briefs/compile", json={
        "brief": "主标题：电动牙刷双十一，活动价199元，原价399元，风格：科技风",
        "compiler": "demo",
    })
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "WAITING_CONFIRMATION"
    assert payload["draft"]["fields"]["price"] == "199"
    assert payload["draft"]["requires_confirmation"] is True


def test_brief_missing_required_field_waits_for_input():
    response = client.post("/api/briefs/compile", json={
        "brief": "主标题：电动牙刷双十一，整体想做成科技风海报",
        "compiler": "demo",
    })
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "WAITING_INPUT"
    assert "price" in response.json()["draft"]["missing_fields"]


def test_brief_empty_input_rejected():
    response = client.post("/api/briefs/compile", json={"brief": "很短", "compiler": "demo"})
    assert response.status_code == 422
