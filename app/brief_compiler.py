"""Brief → 可人工确认的 PosterSpec 草稿。

M2 第一步只抽取事实，不自动生成海报。特别是价格等业务字段：
LLM 的候选值必须经过确定性来源校验，缺字段则要求用户手动填写。
"""
from __future__ import annotations

import json
import os
import re
from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field, ValidationError, model_validator

from .image_provider import _allowed_provider_url


class BriefRequest(BaseModel):
    brief: str = Field(min_length=8, max_length=2000)
    compiler: Literal["demo", "qwen"] = "demo"


class DraftFields(BaseModel):
    title: str | None = Field(default=None, max_length=35)
    subtitle: str | None = Field(default=None, max_length=45)
    price: Decimal | None = Field(default=None, gt=0, le=999999, decimal_places=2)
    original_price: Decimal | None = Field(default=None, gt=0, le=999999, decimal_places=2)
    style: str | None = Field(default=None, max_length=80)
    cta: str | None = Field(default=None, max_length=12)


class BriefDraft(BaseModel):
    fields: DraftFields
    compiler: Literal["demo", "qwen"]
    missing_fields: list[str]
    warnings: list[str]
    requires_confirmation: bool = True


class BriefCompilerError(RuntimeError):
    pass


# 只识别带明确业务标签的价格，避免“买二送一”中的数字被误当成价钱。
_PRICE_PATTERNS = {
    "price": re.compile(
        r"(?:到手价|活动价|优惠价|现价|售价|价格)\s*[:：为是]?\s*[¥￥]?\s*(\d{1,6}(?:\.\d{1,2})?)\s*(?:元|块)?"
    ),
    "original_price": re.compile(
        r"(?:原价|划线价)\s*[:：为是]?\s*[¥￥]?\s*(\d{1,6}(?:\.\d{1,2})?)\s*(?:元|块)?"
    ),
}


def _explicit_prices(brief: str) -> dict[str, Decimal | None]:
    result: dict[str, Decimal | None] = {}
    for field, pattern in _PRICE_PATTERNS.items():
        matched = pattern.search(brief)
        try:
            result[field] = Decimal(matched.group(1)) if matched else None
        except InvalidOperation:
            result[field] = None
    return result


def _demo_extract(brief: str) -> dict:
    """教学模式：规则抽取而非 LLM。不会凭空编造标题或价格。"""
    result: dict = {}
    for name, label in (
        ("title", r"(?:主标题|标题)"),
        ("subtitle", r"(?:副标题)"),
        ("cta", r"(?:按钮文案|行动文案|CTA)"),
        ("style", r"(?:设计风格|整体风格|风格)"),
    ):
        match = re.search(label + r"\s*[:：]\s*([^,，。；;\n]+)", brief)
        if match:
            result[name] = match.group(1).strip()
    return result


def _safe_chat_endpoint(url: str) -> bool:
    parsed = urlparse(url)
    return (
        _allowed_provider_url(url)
        and parsed.username is None
        and parsed.password is None
        and parsed.query == ""
        and parsed.fragment == ""
        and parsed.path.endswith("/compatible-mode/v1/chat/completions")
    )


async def _qwen_extract(brief: str) -> dict:
    key = os.getenv("DASHSCOPE_API_KEY", "")
    endpoint = os.getenv("QWEN_CHAT_ENDPOINT", "")
    model = os.getenv("QWEN_CHAT_MODEL", "qwen-plus")
    if not key or not endpoint:
        raise BriefCompilerError("Qwen 编译模式需要配置 DASHSCOPE_API_KEY 和 QWEN_CHAT_ENDPOINT")
    if not _safe_chat_endpoint(endpoint):
        raise BriefCompilerError("QWEN_CHAT_ENDPOINT 必须是阿里云 HTTPS /compatible-mode/v1/chat/completions 地址")

    system = (
        "你是电商营销海报规格字段提取器。请只输出 JSON 对象，"
        "字段只可有 title,subtitle,price,original_price,style,cta。"
        "仅根据用户提供的文字抽取数据，不要推断或捏造价格、折扣、商品功能、品牌规则。"
        "未提到的字段填 null。price 和 original_price 仅允许数字或 null。"
        "不得在输出里添加 Markdown、解释或额外字段。"
    )
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": brief}],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(40.0, connect=10.0), follow_redirects=False) as client:
            response = await client.post(
                endpoint,
                json=payload,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
            response.raise_for_status()
            message = response.json()["choices"][0]["message"]["content"]
        obj = json.loads(message)
        if not isinstance(obj, dict):
            raise BriefCompilerError("Qwen 返回的 JSON 不是对象")
        return obj
    except BriefCompilerError:
        raise
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise BriefCompilerError("Qwen Brief 编译失败，请检查模型、API Key、区域和网络") from exc


async def compile_brief(request: BriefRequest) -> BriefDraft:
    raw = _demo_extract(request.brief) if request.compiler == "demo" else await _qwen_extract(request.brief)
    if not isinstance(raw, dict):
        raise BriefCompilerError("编译器返回的数据结构无效")
    data = {name: raw.get(name) for name in DraftFields.model_fields if raw.get(name) not in ("",)}
    warnings: list[str] = []
    # Qwen 抽取出的价格不能被视为权威来源：用明确标注的原文价格覆盖。
    authoritative_prices = _explicit_prices(request.brief)
    for name in ("price", "original_price"):
        if data.get(name) is not None and authoritative_prices[name] is None:
            warnings.append(f"{name} 无明确原文价格标签，已清空，请手动核对")
        data[name] = authoritative_prices[name]
    try:
        fields = DraftFields.model_validate(data)
    except ValidationError as exc:
        raise BriefCompilerError("规格草稿格式或字段长度不符合要求，请修改 Brief") from exc

    if fields.price is not None and fields.original_price is not None and fields.original_price < fields.price:
        warnings.append("原价低于活动价，生成前必须修正")
    missing = [name for name in ("title", "price") if getattr(fields, name) is None]
    if request.compiler == "demo":
        warnings.append("当前为规则演示模式，不是大模型智能抽取")
    warnings.append("所有价格、标题和宣传信息均须由运营人员确认，编译结果不会直接投放")
    return BriefDraft(fields=fields, compiler=request.compiler, missing_fields=missing,
                      warnings=warnings, requires_confirmation=True)
