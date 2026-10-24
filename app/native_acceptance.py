"""M4c: version-bound Acceptance Gate for *actual* 1080x1350 native pixels.

A square poster's OCR report is never reused for the native derivative.
OCR is optional; without a real engine the image remains UNVERIFIED. Neither
rendering nor this report calls an AI image-generation API.
"""
from __future__ import annotations

import hashlib
import io
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from PIL import Image

from app import storage
from app.acceptance import Check, _brand_terms, _field_checks
from app.native_layout import (
    HEIGHT, WIDTH, render_native_feed, render_native_product_focus,
)
from app.ocr_price_layout import assess_price_zones
from app.ocr_provider import OCRExecutionError, OCRUnavailable, recognize
from app.schemas import PosterSpec, price_text

VARIANT = "feed_4x5_native"
PRODUCT_FOCUS_VARIANT = "feed_4x5_native_product_focus"
NativeVariant = Literal["feed_4x5_native", "feed_4x5_native_product_focus"]
NATIVE_VARIANTS = (VARIANT, PRODUCT_FOCUS_VARIANT)
NATIVE_ZONES = {
    "price": (54, 866, 515, 1014),
    "original_price": (54, 1014, 515, 1097),
}
PRODUCT_FOCUS_ZONES = {
    "price": (54, 990, 600, 1120),
    "original_price": (54, 1110, 600, 1200),
}
NATIVE_ZONES_BY_VARIANT = {
    VARIANT: NATIVE_ZONES,
    PRODUCT_FOCUS_VARIANT: PRODUCT_FOCUS_ZONES,
}
MODE = Literal["none", "paddle"]


class StaleNativeEvidence(ValueError):
    pass


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sources(poster_id: str, version: int) -> tuple[PosterSpec, bytes, dict, bytes]:
    source_png = storage.version_path(poster_id, version).read_bytes()
    poster = storage.poster_dir(poster_id)
    spec_bytes = (poster / "versions" / f"v{version}.json").read_bytes()
    spec = PosterSpec.model_validate_json(spec_bytes)
    asset_dir = poster / "assets"
    asset_digest = {
        "product": _sha((asset_dir / "product.png").read_bytes()),
        "background": _sha((asset_dir / "background.png").read_bytes()),
        "logo": _sha((asset_dir / "logo.png").read_bytes()) if
            (asset_dir / "logo.png").is_file() else None,
    }
    return spec, spec_bytes, asset_digest, source_png


def _render(
    poster_id: str, spec: PosterSpec, variant: NativeVariant = VARIANT,
) -> bytes:
    product, logo, background = storage.read_assets(poster_id)
    if variant == VARIANT:
        return render_native_feed(spec, product, logo, background)
    if variant == PRODUCT_FOCUS_VARIANT:
        return render_native_product_focus(spec, product, logo, background)
    raise ValueError("不支持的原生版式")


def report_path(
    poster_id: str, version: int, mode: MODE, variant: NativeVariant = VARIANT,
) -> Path:
    storage.version_path(poster_id, version)
    if variant not in NATIVE_VARIANTS:
        raise ValueError("不支持的原生版式")
    return (
        storage.poster_dir(poster_id) / "versions"
        / f"v{version}.{variant}.acceptance.{mode}.json"
    )


def _rule_checks(spec: PosterSpec, image: Image.Image, asset_hashes: dict) -> list[Check]:
    checks = [
        Check(
            name="native_canvas_dimensions", status="PASS" if image.size == (WIDTH, HEIGHT) else "FAIL",
            detail="实际派生图片尺寸（不证明文字或商品视觉质量）",
            evidence={"expected": [WIDTH, HEIGHT], "actual": list(image.size)},
        ),
        Check(
            name="source_spec_price", status="PASS",
            detail="当前版本 PosterSpec 价格已通过结构化校验，不代表图片文字正确",
            evidence={"price": price_text(spec.price)},
        ),
        Check(
            name="sku_asset_present", status="PASS",
            detail="已读取保存的 SKU 资产；未独立验证画面保真度",
            evidence={"source_sha256": asset_hashes["product"]},
        ),
        Check(
            name="logo_asset_present", status="PASS" if asset_hashes["logo"] else "UNVERIFIED",
            detail="仅表示是否存在原始 Logo 资产，不能判定是否正确显示",
        ),
    ]
    terms = _brand_terms()
    if terms is None:
        checks.append(Check(name="forbidden_terms_in_spec", status="UNVERIFIED",
                            detail="尚未配置品牌词表，不能声称广告文案合规"))
    else:
        content = "\n".join((spec.title, spec.subtitle, spec.cta))
        hits = [term for term in terms if term in content]
        checks.append(Check(name="forbidden_terms_in_spec",
                            status="FAIL" if hits else "PASS",
                            detail="仅检查结构化文案中的禁用词，不是广告合规认证",
                            evidence={"violations": hits}))
    return checks


def evaluate_native(
    poster_id: str, version: int, ocr_mode: MODE,
    variant: NativeVariant = VARIANT,
) -> dict:
    if variant not in NATIVE_VARIANTS:
        raise ValueError("不支持的原生版式")
    spec, spec_bytes, assets, source_png = _sources(poster_id, version)
    png = _render(poster_id, spec, variant)
    with Image.open(io.BytesIO(png)) as image:
        image.load()
        dimension = image.copy()

    ocr_status = "NOT_RUN"
    ocr_runtime = None
    lines: list[dict] = []
    if ocr_mode == "paddle":
        with tempfile.TemporaryDirectory(prefix="poster-native-ocr-") as directory:
            path = Path(directory) / "native-feed.png"
            path.write_bytes(png)
            try:
                lines = recognize(path)
                # A real adapter returns OCRLines with diagnostic package and
                # platform details. Legacy/mock providers return plain lists.
                # Lack of telemetry must stay visible, never imply authenticity.
                ocr_runtime = getattr(lines, "runtime", None)
                ocr_status = "OK"
            except OCRUnavailable:
                ocr_status = "UNAVAILABLE"
            except OCRExecutionError:
                ocr_status = "ERROR"

    checks = (
        _rule_checks(spec, dimension, assets)
        + _field_checks(spec, ocr_status, lines)
        + [Check.model_validate(v) for v in assess_price_zones(
            spec, ocr_status, lines, zones=NATIVE_ZONES_BY_VARIANT[variant], layout_size=(WIDTH, HEIGHT),
        )]
    )
    states = {check.status for check in checks}
    auto_checks = ("FAIL" if "FAIL" in states else
                   "INCOMPLETE" if "UNVERIFIED" in states else "PASS")
    report = {
        "poster_id": poster_id, "version": version, "variant_id": variant,
        "source_image_sha256": _sha(source_png),
        "spec_sha256": _sha(spec_bytes),
        "asset_sha256": assets,
        "native_image_sha256": _sha(png),
        "image_dimensions": [WIDTH, HEIGHT],
        "ocr_mode": ocr_mode, "ocr_status": ocr_status,
        "ocr_runtime": ocr_runtime,
        "ocr_runtime_evidence": "RECORDED" if ocr_runtime else "UNRECORDED",
        "checks": [check.model_dump(mode="json") for check in checks],
        "auto_checks": auto_checks,
        "verdict": "BLOCKED" if auto_checks == "FAIL" else "REVIEW_REQUIRED",
        "requires_human_review": True,
        "inherits_square_acceptance": False,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return report


def verified_report(
    poster_id: str, version: int, mode: MODE, variant: NativeVariant = VARIANT,
) -> dict:
    report = json.loads(
        report_path(poster_id, version, mode, variant).read_text(encoding="utf-8")
    )
    spec, spec_bytes, assets, original = _sources(poster_id, version)
    if report.get("poster_id") != poster_id or report.get("version") != version or \
            report.get("variant_id") != variant or report.get("ocr_mode") != mode:
        raise StaleNativeEvidence("报告不属于当前海报/版本/导出模式")
    if report.get("source_image_sha256") != _sha(original) or \
            report.get("spec_sha256") != _sha(spec_bytes) or \
            report.get("asset_sha256") != assets:
        raise StaleNativeEvidence("保存的海报素材或规格已发生变化，请重新验收")
    if report.get("native_image_sha256") != _sha(_render(poster_id, spec, variant)):
        raise StaleNativeEvidence("原生布局的实际像素与已保存报告不同，请重新验收")
    return report
