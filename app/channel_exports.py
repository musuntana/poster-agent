"""M4a: no-crop channel export derivatives from a SAVED poster version.

This is intentionally NOT responsive relayout. Exported PNG contains the
original square poster entirely (no SKU/price clipping), with a softly blurred
background occupying unused canvas area. It requires no external AI calls and
never changes the source version or its Acceptance Gate report.
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFilter, ImageOps

PRESETS = {
    "feed_4x5": (1080, 1350),
    "story_9x16": (1080, 1920),
    "landscape_16x9": (1200, 675),
}
MARGIN = 24


@dataclass(frozen=True)
class Variant:
    png: bytes
    width: int
    height: int
    content_box: tuple[int, int, int, int]
    source_sha256: str


def render_variant(source_png: bytes, preset: str) -> Variant:
    if preset not in PRESETS:
        raise ValueError("不支持的导出规格")
    width, height = PRESETS[preset]
    source_hash = hashlib.sha256(source_png).hexdigest()

    with Image.open(io.BytesIO(source_png)) as image:
        if image.format != "PNG" or image.size != (1024, 1024):
            raise ValueError("该功能仅支持已保存的 1024×1024 原始 PNG 海报")
        image.load()
        original = image.convert("RGB")

    # The *background* may be cropped and blurred; all foreground poster
    # contents are preserved in the central contain-scaled layer.
    base = ImageOps.fit(original, (width, height), method=Image.Resampling.LANCZOS)
    base = base.filter(ImageFilter.GaussianBlur(radius=28)).convert("RGBA")
    tint = Image.new("RGBA", (width, height), (9, 20, 37, 96))
    base = Image.alpha_composite(base, tint)

    foreground = ImageOps.contain(
        original, (width - 2 * MARGIN, height - 2 * MARGIN),
        method=Image.Resampling.LANCZOS,
    )
    x = (width - foreground.width) // 2
    y = (height - foreground.height) // 2
    # Solid border keeps the original readable against the blurred edges.
    draw = ImageDraw.Draw(base)
    draw.rounded_rectangle(
        (x - 3, y - 3, x + foreground.width + 2, y + foreground.height + 2),
        radius=4, fill=(246, 249, 255, 255),
    )
    base.paste(foreground.convert("RGBA"), (x, y))
    result = io.BytesIO()
    base.convert("RGB").save(result, format="PNG", optimize=True)
    return Variant(
        png=result.getvalue(), width=width, height=height,
        content_box=(x, y, x + foreground.width, y + foreground.height),
        source_sha256=source_hash,
    )
