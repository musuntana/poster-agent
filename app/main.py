"""M1 REST API + 极简页面。仅适合本地或可信环境使用。"""
from __future__ import annotations
import io
import json
import hashlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Header
from fastapi.responses import FileResponse, HTMLResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError
from . import storage, execution_store
from .compositor import compose, local_background
from .image_provider import generate_ai_background, ImageProviderError
from .schemas import PosterSpec, PosterEdit
from .review_routes import router as review_router
from .review_service import ReviewNotFound, get_run
from .brief_routes import router as brief_router

load_dotenv()
logger = logging.getLogger(__name__)
@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Single-process only: orphaned RUNNING tasks may already have incurred costs.
    execution_store.recover_incomplete()
    yield


app = FastAPI(title="Poster Agent M2c", version="0.2.2", lifespan=lifespan)
app.include_router(brief_router)
app.include_router(review_router)
MAX_UPLOAD = 10 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 25_000_000


async def read_upload(upload: UploadFile, *, required: bool = True) -> Image.Image:
    blob = await upload.read(MAX_UPLOAD + 1)
    if not blob:
        raise HTTPException(status_code=422, detail="图片为空")
    if len(blob) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="图片超过 10 MB 限制")
    try:
        with Image.open(io.BytesIO(blob)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("格式不支持")
            image.load()
            image = ImageOps.exif_transpose(image)
            if image.width < 64 or image.height < 64:
                raise ValueError("图片太小")
            return image.convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(status_code=422, detail="请上传有效的 PNG/JPG/WEBP 图片（尺寸至少 64×64）") from exc


@app.get("/", response_class=HTMLResponse)
async def home():
    return (Path(__file__).resolve().parent / "templates" / "index.html").read_text(encoding="utf-8")


@app.get("/health")
async def health():
    return {"ok": True, "version": "0.2.2"}


def _fingerprint(spec: PosterSpec, product: Image.Image, logo: Image.Image | None) -> str:
    """Hash normalized input pixels and fields, not arbitrary file paths."""
    digest = hashlib.sha256()
    digest.update(json.dumps(spec.model_dump(mode="json"), sort_keys=True).encode("utf-8"))
    for asset in (product, logo):
        if asset is None:
            digest.update(b"none")
        else:
            digest.update(f"{asset.mode}:{asset.width}x{asset.height}".encode())
            digest.update(asset.tobytes())
    return digest.hexdigest()


@app.get("/api/executions/{key}")
def get_execution(key: str):
    try:
        return execution_store.get(key)
    except execution_store.InvalidExecutionKey as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/posters", status_code=201)
async def create_poster(
    product: Annotated[UploadFile, File(description="SKU 商品图片")],
    title: Annotated[str, Form()],
    price: Annotated[str, Form()],
    style: Annotated[str, Form()] = "蓝色科技风",
    subtitle: Annotated[str, Form()] = "品质生活 · 限时优惠",
    original_price: Annotated[str, Form()] = "",
    cta: Annotated[str, Form()] = "立即抢购",
    background_mode: Annotated[str, Form()] = "local",
    logo: UploadFile | None = File(default=None),
    review_run_id: Annotated[str, Form()] = "",
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        spec = PosterSpec(title=title, price=price, subtitle=subtitle, style=style,
                          original_price=original_price.strip() or None, cta=cta,
                          background_mode=background_mode)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_context=False, include_input=False)) from exc
    # M2b: creating an image from an approved Brief must use exactly the
    # confirmed structured specification. Existing manually-filled M1 requests
    # without review_run_id remain supported.
    if review_run_id:
        try:
            review = get_run(review_run_id)
        except ReviewNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if review["status"] != "APPROVED":
            raise HTTPException(status_code=409, detail="请先完成并确认海报规格，再生成海报")
        if review["confirmed_spec"] != spec.model_dump(mode="json"):
            raise HTTPException(status_code=409, detail="当前字段与已确认的规格不一致，请重新编译并确认")
    sku = await read_upload(product)
    brand_logo = await read_upload(logo) if logo is not None and logo.filename else None
    # Claim BEFORE calling a paid model. Requests without this header still use
    # the M1 legacy behavior (for compatibility), and are not idempotent.
    if idempotency_key is not None:
        try:
            previous = execution_store.begin(idempotency_key, _fingerprint(spec, sku, brand_logo))
            if previous is not None:
                return previous
        except execution_store.InvalidExecutionKey as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except execution_store.ExecutionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        if spec.background_mode == "ai":
            background = await generate_ai_background(spec.style)
        else:
            background = local_background(spec.style)
        rendered = compose(spec, sku, brand_logo, background)
        result = storage.create(spec, sku, brand_logo, background, rendered)
        if idempotency_key is not None:
            execution_store.finish(idempotency_key, result)
        return result
    except ImageProviderError as exc:
        if idempotency_key is not None:
            execution_store.mark_unknown(idempotency_key)
        logger.warning("Qwen background generation failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception:
        if idempotency_key is not None:
            execution_store.mark_unknown(idempotency_key)
        logger.exception("poster creation failure")
        raise HTTPException(status_code=500, detail="海报生成异常，可能存在未知结果，请查询执行记录") from None


@app.get("/api/posters/{poster_id}")
async def get_poster(poster_id: str):
    try:
        return storage.view(poster_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/posters/{poster_id}/revisions", status_code=201)
async def revise_poster(poster_id: str, edit: PosterEdit):
    try:
        current = storage.view(poster_id)
        existing = PosterSpec.model_validate(current["spec"])
        changes = edit.model_dump(exclude_unset=True)
        if not changes:
            raise HTTPException(status_code=422, detail="至少修改一个字段")
        spec = PosterSpec.model_validate({**existing.model_dump(), **changes})
        product, logo, background = storage.read_assets(poster_id)
        rendered = compose(spec, product, logo, background)
        return storage.new_version(poster_id, spec, rendered)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_context=False, include_input=False)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/posters/{poster_id}/versions/{version}/image")
async def poster_image(poster_id: str, version: int):
    try:
        return FileResponse(storage.version_path(poster_id, version), media_type="image/png")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
