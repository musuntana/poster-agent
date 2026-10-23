"""M7: enforce the local-only network boundary on every HTTP route."""
from fastapi.testclient import TestClient

from app.main import app


def test_loopback_ipv4_peer_can_access_health():
    client = TestClient(app, client=("127.0.0.1", 50001))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_loopback_ipv6_peer_can_access_health():
    client = TestClient(app, client=("::1", 50002))

    response = client.get("/health")

    assert response.status_code == 200


def test_ipv4_mapped_loopback_peer_can_access_health():
    client = TestClient(app, client=("::ffff:127.0.0.1", 50003))

    response = client.get("/health")

    assert response.status_code == 200


def test_non_loopback_peer_is_rejected_before_route_execution():
    client = TestClient(app, client=("203.0.113.10", 50004))

    response = client.get("/health")

    assert response.status_code == 403
    assert "仅允许本机直连" in response.json()["detail"]


def test_non_loopback_peer_is_rejected_for_api_routes_too():
    client = TestClient(app, client=("198.51.100.20", 50005))

    response = client.get("/api/annotations/native-4x5/export.csv?reviewer=test-user")

    assert response.status_code == 403


def test_unparseable_non_test_peer_is_rejected():
    client = TestClient(app, client=("untrusted-host", 50006))

    response = client.get("/health")

    assert response.status_code == 403
