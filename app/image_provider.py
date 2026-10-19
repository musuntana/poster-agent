"""Qwen 图像生成功能：只生成背景，不生成价格或 Logo。"""
from __future__ import annotations
import io
import os
from urllib.parse import urlparse
import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_GENERATED_IMAGE_BYTES = 14 * 1024 * 1024


class ImageProviderError(RuntimeError):
    pass


def _allowed_provider_url(url: str) -> bool:
    p = urlparse(url)
    host = p.hostname or ""
    return p.scheme == "https" and (host == "aliyuncs.com" or host.endswith(".aliyuncs.com"))


def make_prompt(style: str) -> str:
    return ("Square luxury ecommerce promotional visual BACKGROUND ONLY. "
            "No text, no numbers, no logos, no product, no typography, no people. "
            "Clear negative space and soft lighting, pleasant gradient, premium polished look. "
            "Avoid busy details. Visual mood: " + style)


async def generate_ai_background(style: str) -> Image.Image:
    key = os.getenv("DASHSCOPE_API_KEY", "")
    endpoint = os.getenv("QWEN_IMAGE_ENDPOINT", "")
    model = os.getenv("QWEN_IMAGE_MODEL", "qwen-image-3.0")
    if not key or not endpoint:
        raise ImageProviderError("AI 模式需要 DASHSCOPE_API_KEY 和 QWEN_IMAGE_ENDPOINT，参见 README")
    if not _allowed_provider_url(endpoint) or not endpoint.endswith("/images/generations"):
        raise ImageProviderError("QWEN_IMAGE_ENDPOINT 必须是阿里云 HTTPS compatible-mode /images/generations 地址")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {"model": model, "prompt": make_prompt(style), "size": "1024x1024", "n": 1}
    timeout = httpx.Timeout(300.0, connect=20.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            img_url = data["data"][0]["url"]
            if not _allowed_provider_url(img_url):
                raise ImageProviderError("模型响应图片 URL 不在允许的阿里云域名范围内")
            async with client.stream("GET", img_url) as img_response:
                img_response.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                async for chunk in img_response.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_GENERATED_IMAGE_BYTES:
                        raise ImageProviderError("模型返回图片过大")
                    chunks.append(chunk)
            with Image.open(io.BytesIO(b"".join(chunks))) as image:
                image.load()
                return ImageOps.exif_transpose(image).convert("RGB")
    except ImageProviderError:
        raise
    except (httpx.HTTPError, KeyError, IndexError, ValueError, UnidentifiedImageError, OSError) as exc:
        # 不回显完整异常、带签名的图片 URL 或 API 密钥
        raise ImageProviderError("调用图像模型失败，请检查 API Key、Workspace 区域、模型名称和网络连接") from exc
