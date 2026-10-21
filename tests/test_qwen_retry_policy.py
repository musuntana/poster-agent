"""Only rate-limited Qwen Brief extraction is retried (one additional attempt)."""
import asyncio
import json

import httpx
import pytest

from app.brief_compiler import _qwen_extract, BriefCompilerError


def test_429_retries_exactly_once(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-key")
    monkeypatch.setenv(
        "QWEN_CHAT_ENDPOINT",
        "https://demo.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions",
    )
    hits = {"n": 0}
    async def fake_sleep(delay):
        assert 0 < delay <= 1
    monkeypatch.setattr("app.brief_compiler.asyncio.sleep", fake_sleep)
    def handler(request):
        hits["n"] += 1
        if hits["n"] == 1:
            return httpx.Response(429, json={"message": "rate limited"})
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps({"title": "科技优惠", "price": None}, ensure_ascii=False)
        }}]})
    base_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.brief_compiler.httpx.AsyncClient",
        lambda **kwargs: base_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    assert asyncio.run(_qwen_extract("给商品生成海报，主标题是科技优惠"))["title"] == "科技优惠"
    assert hits["n"] == 2


def test_server_error_is_not_automatically_retried(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-key")
    monkeypatch.setenv(
        "QWEN_CHAT_ENDPOINT",
        "https://demo.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions",
    )
    hits = {"n": 0}
    def handler(request):
        hits["n"] += 1
        return httpx.Response(503, json={"message": "unavailable"})
    base_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.brief_compiler.httpx.AsyncClient",
        lambda **kwargs: base_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    with pytest.raises(BriefCompilerError):
        asyncio.run(_qwen_extract("给商品生成海报，主标题是科技优惠"))
    assert hits["n"] == 1
