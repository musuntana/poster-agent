"""M3e: spatial OCR price validation for the CURRENT 1024px Pillow layout.

A currency token is only attributed to sale/original price if PaddleOCR
provides a usable box with its center in that field's known region.

Do not infer geometry when boxes are absent. The result then is UNVERIFIED.
This is a template-specific *additional* acceptance check; it does not alter
the original source-of-truth PosterSpec or OCR text-level field checks.
"""
from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation

from app.schemas import PosterSpec, price_text

# Layout contracts correspond to compositor.py's sale price (65,668) and
# original-price label (78,800). Do not reuse for arbitrary templates/sizes.
LAYOUT_SIZE = (1024, 1024)
PRICE_ZONES = {
    "price": (45, 645, 512, 790),
    "original_price": (60, 790, 512, 858),
}
PRICE_TOKEN = re.compile(r"[¥￥]\s*([0-9][0-9.,．]*)")
MIN_SCORE = 0.40


def _center(box, layout_size: tuple[int, int] = LAYOUT_SIZE) -> tuple[float, float] | None:
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
           for x in box):
        return None
    x1, y1, x2, y2 = (float(v) for v in box)
    if not (0 <= x1 < x2 <= layout_size[0] and 0 <= y1 < y2 <= layout_size[1]):
        return None
    return (x1 + x2) / 2, (y1 + y2) / 2


def _amount(value: str) -> Decimal | None:
    value = value.replace("．", ".")
    # Allow western thousands groups only when unambiguous, e.g. 1,299.00.
    if "," in value:
        if not re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?", value):
            return None
        value = value.replace(",", "")
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", value):
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _tokens(lines: list[dict], layout_size: tuple[int, int] = LAYOUT_SIZE) -> tuple[list[dict], int]:
    """Return high-confidence price tokens and count ambiguous observations."""
    observations: list[dict] = []
    uncertain = 0
    for line in lines:
        raw = str(line.get("text") or "")
        matches = list(PRICE_TOKEN.finditer(raw))
        for match in matches:
            parsed = _amount(match.group(1))
            score = line.get("score")
            center = _center(line.get("bbox"), layout_size)
            trusted_score = (not isinstance(score, bool)
                             and isinstance(score, (int, float))
                             and math.isfinite(score)
                             and MIN_SCORE <= score <= 1.0)
            if parsed is None or not trusted_score or center is None:
                uncertain += 1
                continue
            observations.append({
                "amount": parsed,
                "text": raw[:160],
                "bbox": list(line["bbox"]),
                "center": center,
                "score": score,
            })
    return observations, uncertain


def assess_price_zones(
    spec: PosterSpec, ocr_status: str, lines: list[dict], *,
    zones: dict[str, tuple[int, int, int, int]] | None = None,
    layout_size: tuple[int, int] = LAYOUT_SIZE,
) -> list[dict]:
    """Template-aware price checks; original 1024px behavior remains default."""
    zones = PRICE_ZONES if zones is None else zones
    checks = []
    observations, uncertain = _tokens(lines, layout_size) if ocr_status == "OK" else ([], 0)
    for field in ("price", "original_price"):
        expected = getattr(spec, field)
        if expected is None:
            continue
        zone = zones[field]
        located = [o for o in observations
                   if zone[0] <= o["center"][0] <= zone[2]
                   and zone[1] <= o["center"][1] < zone[3]]
        conflicting = [o for o in located if o["amount"] != expected]
        correct = [o for o in located if o["amount"] == expected]
        outside = [o for o in observations if o["amount"] == expected and o not in located]

        if ocr_status != "OK":
            status, reason = "UNVERIFIED", "未执行 OCR 或 OCR 不可用，无法判断价格所在区域"
        elif conflicting:
            status, reason = "FAIL", "价格区域识别到与规格不一致的金额（即使同时出现正确金额也阻断）"
        elif correct and uncertain == 0:
            status, reason = "PASS", "OCR 识别到匹配金额，且坐标在预期模板区域内"
        elif correct:
            status, reason = "UNVERIFIED", "金额位置匹配，但部分金额识别缺少可信坐标/置信度"
        elif located:
            status, reason = "FAIL", "预期价格区域存在非匹配金额"
        elif outside:
            status, reason = "FAIL", "OCR 识别到预期金额，但它只出现在错误的价格区域"
        else:
            status, reason = "UNVERIFIED", "没有足够的带坐标金额证据核对该价格区域"

        checks.append({
            "name": "ocr_" + field + "_position",
            "status": status,
            "detail": reason,
            "evidence": {
                "expected": "¥" + price_text(expected),
                "zone_xyxy": list(zone),
                "template_size": list(layout_size),
                "located_amounts": [str(o["amount"]) for o in located],
                "outside_expected_count": len(outside),
                "unlocated_or_low_confidence_currency_tokens": uncertain,
                "located_lines": [
                    {"text": o["text"], "bbox": o["bbox"], "score": o["score"]}
                    for o in located[:12]
                ],
            },
        })
    return checks
