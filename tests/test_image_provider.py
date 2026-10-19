import pytest
import asyncio
import httpx
from app.image_provider import generate_ai_background, ImageProviderError, make_prompt


def test_provider_prompt_no_text():
    prompt = make_prompt('蓝色科技风')
    assert 'No text' in prompt
    assert '蓝色科技风' in prompt


def test_bad_domain_rejected(monkeypatch):
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'some-key')
    monkeypatch.setenv('QWEN_IMAGE_ENDPOINT', 'http://127.0.0.1/images/generations')
    with pytest.raises(ImageProviderError):
        asyncio.run(generate_ai_background('简约'))


def test_qwen_compatible_response_with_mock_http(monkeypatch):
    """不消耗额度，但走官方 compatible-mode 协议及结果下载解析。"""
    import io
    from PIL import Image
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'test-key')
    monkeypatch.setenv('QWEN_IMAGE_ENDPOINT',
                       'https://demo.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/images/generations')
    png = io.BytesIO()
    Image.new('RGB', (1024, 1024), '#204060').save(png, 'PNG')
    original = httpx.AsyncClient
    def handler(request: httpx.Request):
        if request.method == 'POST':
            assert request.headers['Authorization'] == 'Bearer test-key'
            import json
            data = json.loads(request.content)
            assert data['model'] == 'qwen-image-3.0'
            assert data['size'] == '1024x1024'
            assert 'No text' in data['prompt']
            return httpx.Response(200, json={'data': [{'url':
                'https://dashscope-result-sz.oss-cn-shenzhen.aliyuncs.com/demo.png'}]})
        return httpx.Response(200, content=png.getvalue(), headers={'content-type': 'image/png'})
    def mock_client(*args, **kwargs):
        return original(*args, transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr('app.image_provider.httpx.AsyncClient', mock_client)
    result = asyncio.run(generate_ai_background('蓝色科技风'))
    assert result.size == (1024, 1024)
