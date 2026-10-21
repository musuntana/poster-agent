"""M4a read-only, no-paid-inference export endpoint."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app import storage
from app.channel_exports import PRESETS, render_variant

router = APIRouter(prefix="/api/posters", tags=["channel-exports"])


@router.get("/{poster_id}/versions/{version}/exports")
def list_channel_exports(poster_id: str, version: int):
    try:
        storage.version_path(poster_id, version)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="海报版本不存在") from exc
    return {
        "poster_id": poster_id,
        "version": version,
        "mode": "contain_with_blurred_background",
        "reflows_text": False,
        "automatically_accepted": False,
        "formats": [
            {
                "id": key,
                "width": size[0],
                "height": size[1],
                "url": f"/api/posters/{poster_id}/versions/{version}/exports/{key}",
            }
            for key, size in PRESETS.items()
        ],
    }


@router.get("/{poster_id}/versions/{version}/exports/{preset}")
def download_channel_export(poster_id: str, version: int, preset: str):
    if preset not in PRESETS:
        raise HTTPException(status_code=422, detail="不支持的渠道尺寸")
    try:
        source = storage.version_path(poster_id, version).read_bytes()
        image = render_variant(source, preset)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="海报版本不存在") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(
        content=image.png,
        media_type="image/png",
        headers={
            "Content-Disposition": (
                f'attachment; filename="poster-v{version}-{preset}.png"'
            ),
            "Cache-Control": "no-store",
            "X-Source-Image-SHA256": image.source_sha256,
            "X-Export-Mode": "contain",
            "X-Export-Size": f"{image.width}x{image.height}",
            "X-Content-Box": ",".join(str(v) for v in image.content_box),
        },
    )
