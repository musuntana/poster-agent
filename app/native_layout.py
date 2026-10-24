"""M4b: genuinely reflowed 1080x1350 feed poster, not square contain-scaling.

Reuse the original SKU, optional logo, original local/paid background, and a
selected immutable version's spec. This module must NEVER contact a model.
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageOps, ImageFont

from .compositor import _cover, _contain, get_font, _fit_text
from .schemas import PosterSpec, price_text

WIDTH, HEIGHT = 1080, 1350
TEXT_LEFT, TEXT_WIDTH = 68, 440
PRODUCT_BOX = (560, 266, 1030, 1044)
TITLE_TOP = 266
TITLE_BOTTOM = 655


def _title_lines(draw: ImageDraw.ImageDraw, text: str):
    """Fit even long 35-character campaigns into a bounded upper-left region."""
    for size in range(64, 25, -2):
        font = get_font(size)
        lines: list[str] = []
        part = ""
        for character in text:
            candidate = part + character
            if part and draw.textbbox((0, 0), candidate, font=font)[2] > TEXT_WIDTH:
                lines.append(part)
                part = character
            else:
                part = candidate
        if part:
            lines.append(part)
        line_step = size + 16
        if len(lines) <= 4 and len(lines) * line_step <= 298:
            return font, lines, line_step
    raise ValueError("标题超出 4:5 原生版式可用空间，请缩短标题")


def render_native_feed(
    spec: PosterSpec, product: Image.Image, logo: Image.Image | None,
    background: Image.Image,
) -> bytes:
    canvas = _cover(background, (WIDTH, HEIGHT)).convert("RGBA")
    layer = Image.new("RGBA", (WIDTH, HEIGHT))
    deco = ImageDraw.Draw(layer, "RGBA")
    deco.rounded_rectangle((32, 38, 532, 1288), radius=38,
                           fill=(6, 20, 42, 227))
    deco.rounded_rectangle((549, 245, 1045, 1066), radius=38,
                           fill=(5, 18, 37, 110))
    canvas = Image.alpha_composite(canvas, layer)

    # Keep SKU true colors and any original white background. No segmentation.
    left, top, right, bottom = PRODUCT_BOX
    subject = _contain(product, (right - left - 26, bottom - top - 26))
    if subject.getextrema()[3][0] == 255:
        card = Image.new("RGBA", (right-left, bottom-top), (255, 255, 255, 245))
        mask = Image.new("L", card.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, card.width - 1, card.height - 1), radius=30, fill=255
        )
        card.putalpha(mask)
        canvas.alpha_composite(card, (left, top))
    sx = left + ((right-left) - subject.width)//2
    sy = top + ((bottom-top) - subject.height)//2
    canvas.alpha_composite(subject, (sx, sy))

    if logo is not None:
        mark = _contain(logo, (286, 120))
        canvas.alpha_composite(mark, (TEXT_LEFT, 73))

    draw = ImageDraw.Draw(canvas)
    font, lines, step = _title_lines(draw, spec.title)
    for index, line in enumerate(lines):
        draw.text((TEXT_LEFT, TITLE_TOP + index*step), line,
                  font=font, fill="#FFFFFF")
    subtitle_y = TITLE_TOP + len(lines)*step + 19
    if subtitle_y + 50 > TITLE_BOTTOM:
        raise ValueError("副标题没有足够的 4:5 排版空间")
    if spec.subtitle:
        subtitle_font = _fit_text(draw, spec.subtitle, TEXT_WIDTH,
                                  max_size=28, min_size=18)
        # Fail rather than writing off-screen when a very long subtitle exists.
        if draw.textbbox((0, 0), spec.subtitle, font=subtitle_font)[2] > TEXT_WIDTH:
            raise ValueError("副标题太长，超出 4:5 排版宽度")
        draw.text((TEXT_LEFT, subtitle_y), spec.subtitle,
                  font=subtitle_font, fill="#C7E2FC")

    draw.text((TEXT_LEFT, 814), "限时到手价", font=get_font(32), fill="#CBE6FF")
    sale = "¥" + price_text(spec.price)
    sale_font = _fit_text(draw, sale, TEXT_WIDTH, max_size=106, min_size=34)
    if draw.textbbox((0, 0), sale, font=sale_font)[2] > TEXT_WIDTH:
        raise ValueError("价格过长，无法安全排版")
    draw.text((TEXT_LEFT - 3, 880), sale, font=sale_font,
              fill="#FFE79B", stroke_width=1, stroke_fill="#B9873B")

    if spec.original_price is not None:
        original = "原价 ¥" + price_text(spec.original_price)
        original_font = _fit_text(draw, original, TEXT_WIDTH,
                                  max_size=29, min_size=19)
        if draw.textbbox((0, 0), original, font=original_font)[2] > TEXT_WIDTH:
            raise ValueError("原价超出 4:5 排版宽度")
        draw.text((TEXT_LEFT, 1025), original, font=original_font, fill="#BFD7EE")

    button = (TEXT_LEFT, 1102, 480, 1194)
    draw.rounded_rectangle(button, radius=34, fill="#F5C85C")
    cta_font = _fit_text(draw, spec.cta, button[2]-button[0]-35,
                         max_size=39, min_size=20)
    bbox = draw.textbbox((0, 0), spec.cta, font=cta_font)
    if bbox[2] - bbox[0] > button[2]-button[0]-35:
        raise ValueError("CTA 文案过长，无法放入按钮")
    center = (button[0] + button[2]) / 2
    draw.text((center - (bbox[2]-bbox[0])/2, 1121), spec.cta,
              font=cta_font, fill="#203653")

    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()


def _wide_title_lines(
    draw: ImageDraw.ImageDraw, text: str, width: int = 920,
    max_lines: int = 2, max_size: int = 58, min_size: int = 34,
):
    """Fit a title into the wide lower-panel region without clipping."""
    for size in range(max_size, min_size - 1, -2):
        font = get_font(size)
        lines: list[str] = []
        part = ""
        for character in text:
            candidate = part + character
            if part and draw.textbbox((0, 0), candidate, font=font)[2] > width:
                lines.append(part)
                part = character
            else:
                part = candidate
        if part:
            lines.append(part)
        line_step = size + 10
        if len(lines) <= max_lines and len(lines) * line_step <= 132:
            return font, lines, line_step
    raise ValueError("标题超出商品主体优先版式的可用空间，请缩短标题")


def render_native_product_focus(
    spec: PosterSpec, product: Image.Image, logo: Image.Image | None,
    background: Image.Image,
) -> bytes:
    """Alternative native 4:5 composition: product hero above a separate offer panel.

    This renderer is deterministic and offline. It intentionally has a distinct
    layout ID so quality labels for the split layout cannot be reused.
    """
    canvas = _cover(background, (WIDTH, HEIGHT)).convert("RGBA")
    panel = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    deco = ImageDraw.Draw(panel, "RGBA")
    deco.rounded_rectangle(
        (32, 748, 1048, 1318), radius=38, fill=(6, 20, 42, 232)
    )
    canvas = Image.alpha_composite(canvas, panel)

    # Preserve the complete SKU and its colors; never segment or redraw it.
    left, top, right, bottom = 110, 142, 970, 730
    subject = _contain(product, (right - left - 28, bottom - top - 28))
    if subject.getextrema()[3][0] == 255:
        card = Image.new(
            "RGBA", (right - left, bottom - top), (255, 255, 255, 246)
        )
        mask = Image.new("L", card.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, card.width - 1, card.height - 1), radius=30, fill=255
        )
        card.putalpha(mask)
        canvas.alpha_composite(card, (left, top))
    sx = left + ((right - left) - subject.width) // 2
    sy = top + ((bottom - top) - subject.height) // 2
    canvas.alpha_composite(subject, (sx, sy))

    if logo is not None:
        mark = _contain(logo, (250, 84))
        canvas.alpha_composite(mark, (68, 38))

    draw = ImageDraw.Draw(canvas)
    title_font, lines, line_step = _wide_title_lines(draw, spec.title)
    title_top = 786
    for index, line in enumerate(lines):
        draw.text(
            (68, title_top + index * line_step), line,
            font=title_font, fill="#FFFFFF",
        )

    subtitle_y = title_top + len(lines) * line_step + 8
    if spec.subtitle:
        subtitle_font = _fit_text(
            draw, spec.subtitle, 920, max_size=29, min_size=18
        )
        subtitle_box = draw.textbbox((0, 0), spec.subtitle, font=subtitle_font)
        if subtitle_box[2] > 920 or subtitle_y + (subtitle_box[3] - subtitle_box[1]) > 974:
            raise ValueError("副标题超出商品主体优先版式的可用空间，请缩短副标题")
        draw.text(
            (68, subtitle_y), spec.subtitle, font=subtitle_font, fill="#C7E2FC"
        )

    draw.text((68, 974), "限时到手价", font=get_font(28), fill="#CBE6FF")
    sale = "¥" + price_text(spec.price)
    sale_font = _fit_text(draw, sale, 520, max_size=82, min_size=34)
    sale_box = draw.textbbox((0, 0), sale, font=sale_font)
    if sale_box[2] > 520:
        raise ValueError("价格过长，无法安全排版")
    draw.text(
        (65, 1010), sale, font=sale_font,
        fill="#FFE79B", stroke_width=1, stroke_fill="#B9873B",
    )

    if spec.original_price is not None:
        original = "原价 ¥" + price_text(spec.original_price)
        original_font = _fit_text(draw, original, 480, max_size=27, min_size=18)
        original_box = draw.textbbox((0, 0), original, font=original_font)
        if original_box[2] > 480:
            raise ValueError("原价过长，无法安全排版")
        draw.text(
            (68, 1134), original, font=original_font, fill="#BFD7EE"
        )

    button = (640, 1114, 1012, 1210)
    draw.rounded_rectangle(button, radius=34, fill="#F5C85C")
    cta_font = _fit_text(
        draw, spec.cta, button[2] - button[0] - 34,
        max_size=38, min_size=20,
    )
    cta_box = draw.textbbox((0, 0), spec.cta, font=cta_font)
    cta_width = cta_box[2] - cta_box[0]
    if cta_width > button[2] - button[0] - 34:
        raise ValueError("CTA 文案过长，无法放入按钮")
    center = (button[0] + button[2]) / 2
    draw.text(
        (center - cta_width / 2, 1138), spec.cta,
        font=cta_font, fill="#203653",
    )

    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()
