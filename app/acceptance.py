"""M3a Acceptance Gate: deterministic rules + optionally REAL OCR on rendered PNG.

Scope is intentionally narrow: no VLM judge, SKU fidelity model, logo-location
verification or ad-law certification. An unverified signal cannot be PASS.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import BaseModel, Field

from app.ocr_provider import OCRExecutionError, OCRUnavailable, recognize
from app.schemas import PosterSpec, price_text
from app import storage

Status = Literal["PASS", "FAIL", "UNVERIFIED"]
OcrMode = Literal["none", "paddle"]
EXPECTED_SIZE = (1024, 1024)


class AcceptanceOptions(BaseModel):
    ocr_mode: OcrMode = "none"


class Check(BaseModel):
    name: str
    status: Status
    detail: str
    evidence: dict = Field(default_factory=dict)


class AcceptanceReport(BaseModel):
    poster_id: str
    version: int
    image_sha256: str
    spec_sha256: str
    ocr_mode: OcrMode
    ocr_status: Literal["NOT_RUN", "OK", "UNAVAILABLE", "ERROR"]
    checks: list[Check]
    auto_checks: Literal["PASS", "FAIL", "INCOMPLETE"]
    verdict: Literal["BLOCKED", "REVIEW_REQUIRED"]
    requires_human_review: bool = True
    generated_at: str


def _norm(value: str) -> str:
    return "".join(value.split()).replace("￥", "¥")


def _has_text(texts: list[str], expected: str) -> bool:
    # Do not substring-search across unrelated fields: "199" alone must not
    # accidentally be matched by a different number elsewhere in the poster.
    target = _norm(expected)
    return any(target == _norm(line) or target in _norm(line) for line in texts)


def _field_checks(spec: PosterSpec, ocr_status: str, lines: list[dict]) -> list[Check]:
    expected = {
        "title": spec.title,
        "price": "¥" + price_text(spec.price),
        "cta": spec.cta,
    }
    if spec.original_price is not None:
        expected["original_price"] = "¥" + price_text(spec.original_price)
    if spec.subtitle:
        expected["subtitle"] = spec.subtitle

    texts = [l["text"] for l in lines if (l.get("score") is None or l["score"] >= 0.40)]
    out: list[Check] = []
    for field, value in expected.items():
        if ocr_status != "OK":
            out.append(Check(name="ocr_" + field, status="UNVERIFIED",
                             detail="尚无可靠的真实 OCR 结果，不能判定文字正确",
                             evidence={"expected": value}))
        else:
            matched = _has_text(texts, value)
            out.append(Check(
                name="ocr_" + field,
                status="PASS" if matched else "FAIL",
                detail="OCR 文本匹配" if matched else "OCR 未匹配到期望文本，需人工复核是否识别误差",
                evidence={"expected": value, "observed": texts[:35]},
            ))
    return out


def _brand_terms() -> list[str] | None:
    path = os.getenv("POSTER_BRAND_RULES_FILE", "").strip()
    if not path:
        return None
    policy = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(policy, dict) or not isinstance(policy.get("forbidden_terms"), list):
        raise ValueError("品牌规则文件必须包含 forbidden_terms 数组")
    if not all(isinstance(term, str) and term.strip() for term in policy["forbidden_terms"]):
        raise ValueError("forbidden_terms 中只能包含非空字符串")
    return policy["forbidden_terms"]


def _rule_checks(spec: PosterSpec, image: Image.Image, logo_present: bool) -> list[Check]:
    checks: list[Check] = []
    checks.append(Check(
        name="image_dimensions",
        status="PASS" if image.size == EXPECTED_SIZE else "FAIL",
        detail="导出像素尺寸检查（不代表视觉构图合格）",
        evidence={"expected": list(EXPECTED_SIZE), "actual": list(image.size)},
    ))
    checks.append(Check(
        name="source_price",
        status="PASS",
        detail="PosterSpec 价格经过 Pydantic 校验；不代表图片 OCR 已核对",
        evidence={"price": price_text(spec.price), "original_price":
                  price_text(spec.original_price) if spec.original_price is not None else None},
    ))
    checks.append(Check(
        name="logo_asset_present",
        status="PASS" if logo_present else "UNVERIFIED",
        detail="仅验证上传的 Logo 原始素材是否存在；并未验证图片上的 Logo 位置、清晰度",
        evidence={"source_logo_present": logo_present},
    ))
    terms = _brand_terms()
    if terms is None:
        checks.append(Check(name="forbidden_terms_in_spec", status="UNVERIFIED",
                            detail="未配置品牌词表，不能声称品牌合规"))
    else:
        copy = "\n".join((spec.title, spec.subtitle, spec.cta))
        hits = [t for t in terms if t in copy]
        checks.append(Check(name="forbidden_terms_in_spec",
                            status="FAIL" if hits else "PASS",
                            detail="只检查结构化文案，不是全面广告合规审查",
                            evidence={"violations": hits}))
    return checks


def evaluate(poster_id: str, version: int, mode: OcrMode) -> dict:
    image_path = storage.version_path(poster_id, version)
    spec_path = storage.poster_dir(poster_id) / "versions" / f"v{version}.json"
    png = image_path.read_bytes()
    raw_spec = spec_path.read_bytes()
    spec = PosterSpec.model_validate_json(raw_spec)
    logo_present = (storage.poster_dir(poster_id) / "assets" / "logo.png").is_file()
    with Image.open(image_path) as img:
        img.load()
        image = img.copy()

    ocr_status = "NOT_RUN"
    lines: list[dict] = []
    if mode == "paddle":
        try:
            lines = recognize(image_path)
            ocr_status = "OK"
        except OCRUnavailable:
            ocr_status = "UNAVAILABLE"
        except OCRExecutionError:
            ocr_status = "ERROR"

    checks = _rule_checks(spec, image, logo_present) + _field_checks(spec, ocr_status, lines)
    states = {c.status for c in checks}
    overall = "FAIL" if "FAIL" in states else "INCOMPLETE" if "UNVERIFIED" in states else "PASS"
    report = AcceptanceReport(
        poster_id=poster_id, version=version,
        image_sha256=hashlib.sha256(png).hexdigest(),
        spec_sha256=hashlib.sha256(raw_spec).hexdigest(),
        ocr_mode=mode, ocr_status=ocr_status, checks=checks,
        auto_checks=overall,
        verdict="BLOCKED" if overall == "FAIL" else "REVIEW_REQUIRED",
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    return report.model_dump(mode="json")
