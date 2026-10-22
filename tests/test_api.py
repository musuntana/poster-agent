from fastapi.testclient import TestClient
from app.main import app
from app import storage
from app import image_provider
import pytest

client = TestClient(app)


def test_local_create_edit_and_versions(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv('POSTER_DATA_DIR', str(tmp_path))
    called = {'n': 0}
    async def fake_ai(style):
        called['n'] += 1
        raise AssertionError('修改价格绝不应调用模型')
    monkeypatch.setattr('app.main.generate_ai_background', fake_ai)
    response = client.post('/api/posters', files={'product': ('sku.png', product_bytes, 'image/png')},
                           data={'title': '电动牙刷', 'price': '199', 'original_price': '399', 'background_mode': 'local'})
    assert response.status_code == 201, response.text
    data = response.json()
    assert data['version'] == 1
    image = client.get(data['image_url'])
    assert image.status_code == 200
    assert image.content[:8] == b'\x89PNG\r\n\x1a\n'
    response = client.post('/api/posters/'+data['poster_id']+'/revisions', json={'price': '189'})
    assert response.status_code == 201, response.text
    updated = response.json()
    assert updated['version'] == 2
    assert updated['spec']['price'] == '189'
    assert updated['versions'] == [1, 2]
    assert client.get(data['image_url']).status_code == 200
    assert client.get(updated['image_url']).content != image.content
    assert called['n'] == 0


def test_invalid_image_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv('POSTER_DATA_DIR', str(tmp_path))
    response = client.post('/api/posters', files={'product': ('bad.png', b'no image', 'image/png')},
                           data={'title': '电动牙刷', 'price': '199'})
    assert response.status_code == 422


def test_ai_without_key_errors_and_does_not_fall_back(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv('POSTER_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('DASHSCOPE_API_KEY', raising=False)
    monkeypatch.delenv('QWEN_IMAGE_ENDPOINT', raising=False)
    response = client.post('/api/posters', files={'product': ('sku.png', product_bytes, 'image/png')},
                           data={'title': '电动牙刷', 'price': '199', 'background_mode': 'ai'})
    assert response.status_code == 502
    assert len(list(tmp_path.iterdir())) == 0


def test_no_path_traversal():
    response = client.get('/api/posters/not-a-valid-id')
    assert response.status_code == 404


def test_full_copy_revision_and_stale_version_conflict(tmp_path, monkeypatch, product_bytes):
    """Editing subtitle/CTA must preserve assets and never overwrite a newer revision."""
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path))
    response = client.post(
        "/api/posters",
        files={"product": ("sku.png", product_bytes, "image/png")},
        data={
            "title": "电动牙刷", "subtitle": "旧副标题", "price": "199",
            "original_price": "399", "cta": "立即抢购", "background_mode": "local",
        },
    )
    assert response.status_code == 201, response.text
    v1 = response.json()
    pid = v1["poster_id"]
    original_png = client.get(v1["image_url"]).content
    original_assets = [
        (storage.poster_dir(pid) / "assets" / name).read_bytes()
        for name in ("product.png", "background.png")
    ]

    change = client.post(
        f"/api/posters/{pid}/revisions",
        json={
            "expected_version": 1,
            "subtitle": "", "cta": "查看详情",
            "original_price": None,
        },
    )
    assert change.status_code == 201, change.text
    v2 = change.json()
    assert v2["version"] == 2
    assert v2["spec"]["subtitle"] == ""
    assert v2["spec"]["cta"] == "查看详情"
    assert v2["spec"]["original_price"] is None
    assert client.get(v2["image_url"]).content != original_png
    assert client.get(v1["image_url"]).content == original_png
    assert [
        (storage.poster_dir(pid) / "assets" / name).read_bytes()
        for name in ("product.png", "background.png")
    ] == original_assets

    # A second browser tab still editing v1 must not clobber the newer content.
    stale = client.post(
        f"/api/posters/{pid}/revisions",
        json={"expected_version": 1, "title": "过期的标题"},
    )
    assert stale.status_code == 409, stale.text
    assert "最新版本" in stale.json()["detail"]
    assert storage.view(pid)["version"] == 2
    assert storage.view(pid)["spec"]["title"] == "电动牙刷"

    # Request with guard but no changed business field is not an edit.
    assert client.post(
        f"/api/posters/{pid}/revisions", json={"expected_version": 2}
    ).status_code == 422
    assert client.post(
        f"/api/posters/{pid}/revisions",
        json={"expected_version": 0, "title": "非法版本"},
    ).status_code == 422

    # Existing clients may omit expected_version for compatibility.
    legacy = client.post(
        f"/api/posters/{pid}/revisions", json={"cta": "立即购买"}
    )
    assert legacy.status_code == 201, legacy.text
    assert legacy.json()["version"] == 3


def test_editor_exposes_full_copy_and_version_reload():
    html = client.get("/")
    assert html.status_code == 200
    for element in ('id="editTitle"', 'id="editSubtitle"', 'id="editPrice"',
                    'id="editOriginal"', 'id="editCta"', 'id="reloadPoster"'):
        assert element in html.text
    assert "expected_version:version" in html.text
    assert "当前海报已有更新版本" in html.text
