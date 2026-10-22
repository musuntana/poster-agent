"""确定性海报渲染：背景 + 原始 SKU + Logo + 可编辑文字。"""
from __future__ import annotations
import io
import math
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps
from .schemas import PosterSpec, price_text

SIZE = 1024
FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]


def get_font(size: int) -> ImageFont.FreeTypeFont:
    choices = [os.getenv("POSTER_FONT_PATH", ""), *FONT_CANDIDATES]
    for file in choices:
        if file and Path(file).is_file():
            try:
                return ImageFont.truetype(file, size)
            except OSError:
                pass
    raise RuntimeError("找不到中文字体，请设置 POSTER_FONT_PATH 指向支持中文的 .ttf/.ttc 字体")


def _cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(image.convert("RGB"), size, method=Image.Resampling.LANCZOS)


def _contain(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    return ImageOps.contain(image.convert("RGBA"), size, method=Image.Resampling.LANCZOS)


def local_background(style: str) -> Image.Image:
    """本地确定性渐变背景（不是 AI 生图）。"""
    palette = ((30, 55, 111), (8, 22, 55))
    if "粉" in style or "浪漫" in style:
        palette = ((151, 65, 120), (38, 27, 71))
    if "绿" in style or "自然" in style:
        palette = ((36, 112, 94), (11, 47, 68))
    img = Image.new("RGB", (SIZE, SIZE))
    draw = ImageDraw.Draw(img)
    for y in range(SIZE):
        t = y / (SIZE - 1)
        color = tuple(int(a * (1-t) + b * t) for a, b in zip(*palette))
        draw.line((0, y, SIZE, y), fill=color)
    for i in range(6):
        r = 120 + i * 82
        draw.arc((655-r, 510-r, 655+r, 510+r), 0, 360, fill=(60, 130, 173), width=2)
    return img


def _fit_text(draw: ImageDraw.ImageDraw, text: str, max_width: int,
              max_size: int = 64, min_size: int = 23) -> ImageFont.FreeTypeFont:
    for size in range(max_size, min_size - 1, -2):
        font = get_font(size)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
    return get_font(min_size)


def _break_lines(draw: ImageDraw.ImageDraw, value: str, font: ImageFont.FreeTypeFont, width: int, max_lines: int = 2) -> list[str]:
    lines: list[str] = []
    line = ""
    for ch in value:
        candidate = line + ch
        if line and draw.textbbox((0, 0), candidate, font=font)[2] > width:
            lines.append(line)
            line = ch
        else:
            line = candidate
    if line:
        lines.append(line)
    if len(lines) > max_lines:
        raise ValueError("标题太长，超出两行海报布局，请缩短标题")
    return lines


def compose(spec: PosterSpec, product: Image.Image, logo: Image.Image | None, background: Image.Image) -> bytes:
    canvas = _cover(background, (SIZE, SIZE)).convert("RGBA")
    overlay = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rounded_rectangle((32, 44, 510, 980), radius=30, fill=(2, 18, 42, 207))
    canvas = Image.alpha_composite(canvas, overlay)

    # 商品主体使用原图，透明 PNG 能获得最佳合成效果。
    product_rgba = _contain(product, (460, 680))
    if product_rgba.getextrema()[3][0] == 255:  # 白底或不透明图，保留完整素材而非虚假抠图
        card = Image.new("RGBA", (474, 700), (255, 255, 255, 241))
        mask = Image.new("L", card.size, 0)
        md = ImageDraw.Draw(mask)
        md.rounded_rectangle((0, 0, 473, 699), radius=30, fill=255)
        card.putalpha(mask)
        canvas.alpha_composite(card, (535, 190))
    product_x = 542 + (460 - product_rgba.width) // 2
    product_y = 205 + (680 - product_rgba.height) // 2
    canvas.alpha_composite(product_rgba, (product_x, product_y))

    if logo is not None:
        logo_image = _contain(logo, (260, 105))
        canvas.alpha_composite(logo_image, (75, 82))

    draw = ImageDraw.Draw(canvas)
    title_font = _fit_text(draw, spec.title, 405, max_size=61, min_size=35)
    lines = _break_lines(draw, spec.title, title_font, 410)
    top = 274
    for line in lines:
        draw.text((75, top), line, font=title_font, fill="#FFFFFF", stroke_width=0)
        top += 86
    subtitle_font = _fit_text(draw, spec.subtitle, 400, max_size=29, min_size=20)
    if spec.subtitle:
        subtitle_width = draw.textbbox((0, 0), spec.subtitle, font=subtitle_font)[2]
        if subtitle_width > 400 or top + 10 + 35 > 600:
            raise ValueError("副标题超出方形海报安全排版区域，请缩短副标题")
        draw.text((75, top+10), spec.subtitle, font=subtitle_font, fill="#CAE4FF")

    draw.text((75, 610), "限时到手价", font=get_font(31), fill="#CDE6FF")
    price_value = "¥" + price_text(spec.price)
    price_font = _fit_text(draw, price_value, 418, max_size=105, min_size=45)
    price_box = draw.textbbox((0, 0), price_value, font=price_font)
    if price_box[2] - price_box[0] > 418:
        raise ValueError("价格过长，无法安全排版")
    draw.text((65, 668), price_value, font=price_font, fill="#FFE596", stroke_width=1, stroke_fill="#D59F52")
    if spec.original_price is not None:
        original = "原价 ¥" + price_text(spec.original_price)
        original_font = _fit_text(draw, original, 405, max_size=28, min_size=20)
        original_box = draw.textbbox((0, 0), original, font=original_font)
        if original_box[2] - original_box[0] > 405:
            raise ValueError("原价过长，无法安全排版")
        draw.text((78, 800), original, font=original_font, fill="#BBD2ED")
    draw.rounded_rectangle((76, 867, 410, 934), radius=28, fill="#F8CA5A")
    cta_font = _fit_text(draw, spec.cta, 300, max_size=36, min_size=21)
    box = draw.textbbox((0, 0), spec.cta, font=cta_font)
    if box[2] - box[0] > 300:
        raise ValueError("CTA 文案过长，无法放入按钮")
    draw.text((243 - (box[2]-box[0])/2, 877), spec.cta, font=cta_font, fill="#203653")
    output = io.BytesIO()
    canvas.convert("RGB").save(output, "PNG", optimize=True)
    return output.getvalue()
