"""Read-only channel PNG exports: M4a safe-scaled, M4b native 4:5."""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app import storage
from app.channel_exports import PRESETS, render_variant
from app.native_layout import WIDTH, HEIGHT, render_native_feed
from app.schemas import PosterSpec

router = APIRouter(prefix="/api/posters", tags=["channel-exports"])
NATIVE_ID = "feed_4x5_native"


@router.get("/{poster_id}/versions/{version}/exports")
def list_channel_exports(poster_id: str, version: int):
    try:
        storage.version_path(poster_id, version)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="海报版本不存在") from exc
    return {
        "poster_id": poster_id,
        "version": version,
        # Legacy fields refer to the existing safe contain formats.
        "mode": "contain_with_blurred_background",
        "reflows_text": False,
        "automatically_accepted": False,
        "formats": [
            {
                "id": key, "width": size[0], "height": size[1],
                "mode": "contain", "reflows_text": False,
                "url": f"/api/posters/{poster_id}/versions/{version}/exports/{key}",
            }
            for key, size in PRESETS.items()
        ],
        # Native formats are separate so old clients retain their M4a contract.
        "native_formats": [{
            "id": NATIVE_ID, "width": WIDTH, "height": HEIGHT,
            "mode": "native_layout", "reflows_text": True,
            "automatically_accepted": False,
            "url": f"/api/posters/{poster_id}/versions/{version}/exports/{NATIVE_ID}",
        }],
    }


@router.get("/{poster_id}/versions/{version}/exports/{preset}")
def download_channel_export(poster_id: str, version: int, preset: str):
    if preset not in PRESETS and preset != NATIVE_ID:
        raise HTTPException(status_code=422, detail="不支持的渠道尺寸")
    try:
        poster = storage.poster_dir(poster_id)
        source = storage.version_path(poster_id, version).read_bytes()
        if preset == NATIVE_ID:
            spec_bytes = (poster / "versions" / f"v{version}.json").read_bytes()
            spec = PosterSpec.model_validate_json(spec_bytes)
            product, logo, background = storage.read_assets(poster_id)
            result_png = render_native_feed(spec, product, logo, background)
            width, height = WIDTH, HEIGHT
            source_hash = hashlib.sha256(source).hexdigest()
            export_mode = "native_layout"
            box = None
        else:
            image = render_variant(source, preset)
            result_png = image.png
            width, height = image.width, image.height
            source_hash = image.source_sha256
            export_mode = "contain"
            box = image.content_box
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="海报版本不存在") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    headers = {
        "Content-Disposition": f'attachment; filename="poster-v{version}-{preset}.png"',
        "Cache-Control": "no-store",
        "X-Source-Image-SHA256": source_hash,
        "X-Export-Mode": export_mode,
        "X-Export-Size": f"{width}x{height}",
        "X-Automatically-Accepted": "false",
    }
    if box is not None:
        headers["X-Content-Box"] = ",".join(str(v) for v in box)
    return Response(content=result_png, media_type="image/png", headers=headers)
