"""M7: enforce the local-only request boundary for the app, not only its launcher."""
from fastapi.testclient import TestClient

from app.main import app


def test_loopback_ipv4_peer_can_access_health():
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50001))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_loopback_ipv6_peer_can_access_health():
    client = TestClient(app, base_url="http://[::1]:8000", client=("::1", 50002))

    response = client.get("/health")

    assert response.status_code == 200


def test_ipv4_mapped_loopback_peer_can_access_health():
    client = TestClient(app, base_url="http://127.0.0.1:8000", client=("::ffff:127.0.0.1", 50003))

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


def test_untrusted_host_is_rejected_even_from_loopback_peer():
    client = TestClient(
        app,
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 50007),
    )

    response = client.get("/health", headers={"host": "attacker.example"})

    assert response.status_code == 403
    assert "Host" in response.json()["detail"]


def test_cross_origin_post_is_rejected_before_route_execution():
    client = TestClient(
        app,
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 50008),
    )

    response = client.post(
        "/api/batches",
        headers={
            "Origin": "https://attacker.example",
            "Sec-Fetch-Site": "cross-site",
        },
    )

    assert response.status_code == 403
    assert "跨站请求" in response.json()["detail"]


def test_origin_mismatch_is_rejected_without_fetch_metadata():
    client = TestClient(
        app,
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 50009),
    )

    response = client.post(
        "/api/batches",
        headers={"Origin": "https://attacker.example"},
    )

    assert response.status_code == 403
    assert "Origin" in response.json()["detail"]


def test_same_origin_browser_write_reaches_endpoint_validation():
    client = TestClient(
        app,
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 50010),
    )

    response = client.post(
        "/api/batches",
        headers={
            "Origin": "http://127.0.0.1:8000",
            "Sec-Fetch-Site": "same-origin",
        },
    )

    # Missing multipart fields are rejected by FastAPI, proving the local-origin
    # request passed the security middleware instead of being blocked.
    assert response.status_code == 422


def test_localhost_origin_is_accepted_for_local_browser_writes():
    client = TestClient(
        app,
        base_url="http://localhost:8000",
        client=("127.0.0.1", 50011),
    )

    response = client.post(
        "/api/batches",
        headers={
            "Origin": "http://localhost:8000",
            "Sec-Fetch-Site": "same-origin",
        },
    )

    assert response.status_code == 422


def test_cross_origin_get_is_rejected_before_route_execution():
    client = TestClient(
        app,
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 50012),
    )

    response = client.get(
        "/health",
        headers={
            "Origin": "https://attacker.example",
            "Sec-Fetch-Site": "cross-site",
        },
    )

    assert response.status_code == 403
    assert "跨站请求" in response.json()["detail"]


def test_origin_mismatch_on_read_is_rejected_without_fetch_metadata():
    client = TestClient(
        app,
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 50013),
    )

    response = client.get(
        "/health",
        headers={"Origin": "https://attacker.example"},
    )

    assert response.status_code == 403
    assert "Origin" in response.json()["detail"]
