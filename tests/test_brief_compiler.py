import asyncio

import pytest

from app.brief_compiler import BriefRequest, BriefCompilerError, compile_brief


def compile_demo(brief: str):
    return asyncio.run(compile_brief(BriefRequest(brief=brief, compiler="demo")))


def test_explicit_prices_and_text_fields():
    draft = compile_demo(
        "主标题：双十一限时优惠，活动价199元，原价399元，"
        "副标题：智能声波清洁，风格：蓝色科技风，CTA：立即预定"
    )
    assert draft.fields.title == "双十一限时优惠"
    assert str(draft.fields.price) == "199"
    assert str(draft.fields.original_price) == "399"
    assert draft.fields.cta == "立即预定"
    assert draft.missing_fields == []
    assert draft.requires_confirmation


def test_missing_price_must_not_be_invented():
    draft = compile_demo("主标题：双十一限时优惠，帮我制作一张科技风海报，优惠很多")
    assert draft.fields.price is None
    assert "price" in draft.missing_fields


def test_promo_numbers_not_misread_as_price():
    draft = compile_demo("主标题：双十一商品海报，买二送一，满300减50，无明确商品售价")
    assert draft.fields.price is None


def test_original_price_lower_than_price_warns():
    draft = compile_demo("主标题：新品开售，活动价199元，原价99元，风格：清爽简洁")
    assert any("原价低于活动价" in warning for warning in draft.warnings)


def test_qwen_without_key_fails_cleanly(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("QWEN_CHAT_ENDPOINT", raising=False)
    with pytest.raises(BriefCompilerError, match="API_KEY"):
        asyncio.run(compile_brief(BriefRequest(brief="我要做一张双十一商品海报，活动价199元", compiler="qwen")))


def test_qwen_json_object_schema_mock(monkeypatch):
    async def fake_qwen(brief):
        return {"title": "双十一限时优惠", "price": 888,
                "original_price": 666, "style": "科技蓝"}
    monkeypatch.setattr("app.brief_compiler._qwen_extract", fake_qwen)
    result = asyncio.run(compile_brief(BriefRequest(
        brief="制作活动海报，活动价199元，原价399元，蓝色科技风", compiler="qwen"
    )))
    # 价格以 Brief 原文明确字段为准，不信任模型自报。
    assert result.fields.price == 199
    assert result.fields.original_price == 399
    assert result.fields.style == "科技蓝"
    assert result.requires_confirmation
