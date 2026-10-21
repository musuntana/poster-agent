"""Small, explicit upload color-management boundary.

When a source includes a valid ICC profile, convert its pixel values to the
standard sRGB working space BEFORE discarding all embedded metadata. Do not
silently ignore malformed or unsupported profiles.
"""
from __future__ import annotations

import io

from PIL import Image, ImageCms

MAX_ICC_BYTES = 256 * 1024


class InvalidColorProfile(ValueError):
    """An ICC-bearing input cannot be safely interpreted as sRGB."""


def normalize_upload_color(oriented: Image.Image, icc: bytes | None) -> Image.Image:
    """Return metadata-free RGBA pixels. Preserve alpha exactly.

    No ICC means untagged image is assumed sRGB (common web convention),
    *not* independently color-calibrated. ICC profile interpretation is
    best-effort LittleCMS; malformed/unsupported ICCs fail closed.
    """
    if not icc:
        # Distinguish empty/invalid ICC payload from genuinely absent ICC.
        if icc is not None:
            raise InvalidColorProfile("图片 ICC 色彩配置为空，无法确认真实颜色")
        output = oriented.convert("RGBA")
        output.info.clear()
        return output

    if not isinstance(icc, bytes) or len(icc) > MAX_ICC_BYTES:
        raise InvalidColorProfile("ICC 色彩配置过大或不合法")
    try:
        source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        srgb = ImageCms.createProfile("sRGB")
        if oriented.mode == "CMYK":
            # CMYK JPEG requires a proper CMYK ICC → RGB transform. Calling
            # plain .convert('RGB') first would reinterpret CMYK incorrectly.
            converted = ImageCms.profileToProfile(
                oriented, source, srgb, outputMode="RGB"
            )
            output = converted.convert("RGBA")
        else:
            rgba = oriented.convert("RGBA")
            alpha = rgba.getchannel("A")
            # Transform only RGB channels. This prevents LittleCMS/Pillow from
            # modifying alpha and supports transparent PNG/WebP assets.
            rgb = ImageCms.profileToProfile(
                rgba.convert("RGB"), source, srgb, outputMode="RGB"
            )
            output = rgb.convert("RGBA")
            output.putalpha(alpha)
        output.info.clear()
        return output
    except (OSError, ValueError, TypeError, ImageCms.PyCMSError) as exc:
        raise InvalidColorProfile("图片 ICC 色彩配置损坏或与像素格式不匹配") from exc
