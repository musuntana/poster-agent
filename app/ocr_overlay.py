"""M3f: non-destructive visual diagnostic overlay for saved OCR evidence.

Never calls OCR, never edits a saved poster and never pretends missing bounding
boxes were measured. The sole source of OCR rows is the saved Acceptance Gate
report, which is hash-matched to the exact poster version before rendering.
"""
from __future__ import annotations

import hashlib
import io
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.acceptance import AcceptanceReport
from app.ocr_price_layout import PRICE_ZONES
from app import storage

STATUS_COLORS = {
    "PASS": (55, 207, 122, 255),
    "FAIL": (244, 80, 88, 255),
    "UNVERIFIED": (255, 197, 70, 255),
}
OCR_BOX_COLOR = (75, 230, 246, 255)
MAX_DRAWN_BOXES = 35


class StaleAcceptanceReport(RuntimeError):
    pass


def _verified_source(poster_id: str, version: int, report: dict) -> tuple[bytes, AcceptanceReport]:
    """Image AND spec must match the saved acceptance report, never just image."""
    source = storage.version_path(poster_id, version)
    spec = storage.poster_dir(poster_id) / "versions" / f"v{version}.json"
    png = source.read_bytes()
    spec_data = spec.read_bytes()
    parsed = AcceptanceReport.model_validate(report)
    if parsed.poster_id != poster_id or parsed.version != version:
        raise StaleAcceptanceReport("验收报告与海报 ID/版本不匹配")
    if parsed.image_sha256 != hashlib.sha256(png).hexdigest():
        raise StaleAcceptanceReport("验收报告对应的图片已变化，必须重新运行验收")
    if parsed.spec_sha256 != hashlib.sha256(spec_data).hexdigest():
        raise StaleAcceptanceReport("验收报告对应的规格已变化，必须重新运行验收")
    return png, parsed


def _valid_box(box, image_size: tuple[int, int]):
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    if any(isinstance(value, bool) or not isinstance(value, (float, int))
           or not math.isfinite(value) for value in box):
        return None
    x1, y1, x2, y2 = map(float, box)
    w, h = image_size
    if not (0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h):
        return None
    return tuple(int(round(v)) for v in (x1, y1, x2, y2))


def _report_ocr_lines(report: AcceptanceReport) -> list[dict]:
    """The legacy report stores the first 35 OCR observations in field evidence.

    Do not stitch together repeated field copies or claim full model output.
    """
    for check in report.checks:
        if check.name == "ocr_price" and isinstance(check.evidence.get("ocr_lines"), list):
            return check.evidence["ocr_lines"][:MAX_DRAWN_BOXES]
    return []


def render_overlay(
    poster_id: str, version: int, saved_report: dict
) -> tuple[bytes, dict]:
    png, report = _verified_source(poster_id, version, saved_report)
    with Image.open(io.BytesIO(png)) as img:
        img.load()
        base = img.convert("RGBA")
    w, h = base.size
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")
    font = ImageFont.load_default(size=19)
    small_font = ImageFont.load_default(size=15)
    status = {check.name: check.status for check in report.checks}

    # Draw price zones ONLY for this actual fixed layout; other dimensions
    # must not reuse the coordinates as if they were valid.
    zones_drawn = 0
    if (w, h) == (1024, 1024):
        for name, zone in PRICE_ZONES.items():
            check_name = "ocr_" + name + "_position"
            if check_name not in status:
                continue
            color = STATUS_COLORS[status[check_name]]
            draw.rectangle(zone, fill=(color[0], color[1], color[2], 42),
                           outline=color, width=5)
            draw.rectangle((zone[0], zone[1]-30, zone[0]+265, zone[1]),
                           fill=(12, 21, 34, 235))
            draw.text((zone[0]+6, zone[1]-26), f"{name}: {status[check_name]}",
                      font=small_font, fill=color)
            zones_drawn += 1

    rows = _report_ocr_lines(report) if report.ocr_status == "OK" else []
    boxes_drawn = 0
    unavailable_boxes = 0
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            unavailable_boxes += 1
            continue
        bbox = _valid_box(row.get("bbox"), (w, h))
        if bbox is None:
            unavailable_boxes += 1
            continue
        draw.rectangle(bbox, outline=OCR_BOX_COLOR, width=3)
        # Numeric labels can be matched against OCR row order in JSON.
        draw.text((min(bbox[0]+2, w-40), min(bbox[1]+1, h-20)),
                  str(i+1), font=small_font, fill=OCR_BOX_COLOR,
                  stroke_fill=(7, 12, 21, 255), stroke_width=2)
        boxes_drawn += 1

    draw.rectangle((0, 0, w, 65), fill=(9, 21, 34, 236))
    draw.text((12, 8), f"DIAGNOSTIC ONLY   v{version}   OCR: {report.ocr_status}",
              font=font, fill=(255, 255, 255, 255))
    draw.text((12, 38),
              f"zone: {zones_drawn}  bbox: {boxes_drawn}/{len(rows)}  "
              f"missing-box: {unavailable_boxes}  truncated-at: {MAX_DRAWN_BOXES}",
              font=small_font, fill=(255, 231, 163, 255))
    result = Image.alpha_composite(base, overlay).convert("RGB")
    output = io.BytesIO()
    result.save(output, format="PNG", optimize=True)
    details = {
        "ocr_status": report.ocr_status,
        "image_sha256": report.image_sha256,
        "bbox_drawn": boxes_drawn,
        "observed_lines_in_saved_report": len(rows),
        "missing_or_invalid_boxes": unavailable_boxes,
        "zones_drawn": zones_drawn,
        "limited_to_first": MAX_DRAWN_BOXES,
    }
    return output.getvalue(), details
