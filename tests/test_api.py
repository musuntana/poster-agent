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
