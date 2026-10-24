"""M1 REST API + 极简页面。仅适合本地或可信环境使用。"""
from __future__ import annotations
import io
from ipaddress import ip_address
import json
import hashlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
import warnings
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Header, Request, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError
from . import storage, execution_store
from .compositor import compose, local_background, validate_square_layout
from .color_management import InvalidColorProfile, normalize_upload_color
from .image_provider import generate_ai_background, ImageProviderError
from .schemas import PosterSpec, PosterEdit
from .review_routes import router as review_router
from .review_service import ReviewNotFound, get_run
from .brief_routes import router as brief_router
from .acceptance_routes import router as acceptance_router
from .human_review_routes import router as human_review_router
from .channel_export_routes import router as channel_export_router
from .native_acceptance_routes import router as native_acceptance_router
from .native_human_review_routes import router as native_human_review_router
from .trial_queue_routes import router as trial_queue_router
from .batch_import_routes import router as batch_import_router

load_dotenv()
logger = logging.getLogger(__name__)
@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Single-process only: orphaned RUNNING tasks may already have incurred costs.
    execution_store.recover_incomplete()
    yield


app = FastAPI(title="Poster Agent M3c", version="0.3.2", lifespan=lifespan)


def _is_loopback_peer(host: str | None) -> bool:
    """Allow loopback client IPs; Starlette TestClient uses a synthetic hostname."""
    if host == "testclient":
        return True
    if not host:
        return False
    try:
        address = ip_address(host)
    except ValueError:
        return False
    mapped_address = getattr(address, "ipv4_mapped", None)
    if mapped_address is not None:
        address = mapped_address
    return address.is_loopback


@app.middleware("http")
async def enforce_local_only_network_boundary(request: Request, call_next):
    """Reject non-loopback ASGI client IPs; this is defense-in-depth, not authentication."""
    peer_host = request.client.host if request.client is not None else None
    if not _is_loopback_peer(peer_host):
        return JSONResponse(
            status_code=403,
            content={
                "detail": (
                    "Poster Agent 仅允许本机直连；网络共享和反向代理部署未受支持"
                )
            },
        )
    return await call_next(request)
app.include_router(brief_router)
app.include_router(review_router)
app.include_router(acceptance_router)
app.include_router(human_review_router)
app.include_router(channel_export_router)
app.include_router(native_acceptance_router)
app.include_router(native_human_review_router)
app.include_router(trial_queue_router)
app.include_router(batch_import_router)
MAX_UPLOAD = 10 * 1024 * 1024
# These limits apply to the *decoded* image, not the compressed upload size.
# Enforce them BEFORE image.load() to prevent high-memory decompression.
MAX_DECODED_PIXELS = 16_000_000
MAX_IMAGE_EDGE = 8_192
# Pillow also raises for very large dimensions, including formats where its
# safety check occurs during Image.open().
Image.MAX_IMAGE_PIXELS = MAX_DECODED_PIXELS


async def read_upload(upload: UploadFile, *, required: bool = True) -> Image.Image:
    blob = await upload.read(MAX_UPLOAD + 1)
    if not blob:
        raise HTTPException(status_code=422, detail="图片为空")
    if len(blob) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="图片超过 10 MB 限制")
    try:
        # Treat Pillow's decompression warnings as upload rejection, not a
        # warning followed by allocating potentially huge raster buffers.
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(blob)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"}:
                    raise ValueError("格式不支持")
                width, height = image.size
                if width > MAX_IMAGE_EDGE or height > MAX_IMAGE_EDGE or width * height > MAX_DECODED_PIXELS:
                    raise HTTPException(status_code=413, detail="图片像素或边长超限（最多 1600 万像素，单边最多 8192）")
                if width < 64 or height < 64:
                    raise ValueError("图片太小")
                image.load()
                # Capture ICC before EXIF transpose, perform profile-aware
                # conversion to sRGB, then discard all original metadata.
                # Removing ICC without conversion can alter SKU/brand colors.
                source_icc = image.info.get("icc_profile")
                oriented = ImageOps.exif_transpose(image)
                return normalize_upload_color(oriented, source_icc)
    except HTTPException:
        raise
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise HTTPException(status_code=413, detail="图片解码像素超出安全限制") from exc
    except InvalidColorProfile as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="请上传有效的 PNG/JPG/WEBP 图片（尺寸至少 64×64）") from exc


@app.get("/", response_class=HTMLResponse)
async def home():
    return (Path(__file__).resolve().parent / "templates" / "index.html").read_text(encoding="utf-8")


@app.get("/health")
async def health():
    return {"ok": True, "version": "0.3.2"}


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


@app.get("/api/executions")
def list_executions(
    limit: int = Query(default=10, ge=1, le=50),
    offset: int = Query(default=0, ge=0, le=10000),
    status: Literal["RUNNING", "COMPLETED", "UNKNOWN"] | None = Query(default=None),
):
    """Page recent execution summaries; never expose full request or result payloads."""
    return execution_store.list_recent_page(limit=limit, offset=offset, status=status)


@app.get("/api/executions/{key}")
def get_execution(key: str):
    try:
        return execution_store.get(key)
    except execution_store.InvalidExecutionKey as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/executions/{key}/reconcile")
def reconcile_execution(key: str):
    """Inspect a completed on-disk output without dispatching a provider call."""
    try:
        return execution_store.reconcile_unknown(key)
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
    # Fail fast for paid AI requests before reading or decoding uploaded files.
    # A stable key lets the ledger prevent accidental duplicate provider calls.
    if spec.background_mode == "ai" and not idempotency_key:
        raise HTTPException(
            status_code=428,
            detail=(
                "AI 生图可能产生费用，必须提供 Idempotency-Key。若上次请求结果未知，"
                "请先查询执行记录，不要直接发起新的付费请求。"
            ),
        )
    if spec.background_mode == "ai":
        try:
            execution_store.validate_key(idempotency_key)
        except execution_store.InvalidExecutionKey as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
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
    # Layout errors are deterministic input failures. Reject them before the
    # idempotency claim and before any paid image-provider call.
    try:
        validate_square_layout(spec)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Record the same normalized hash in SQLite and the completed disk receipt.
    request_hash = _fingerprint(spec, sku, brand_logo) if idempotency_key is not None else None
    # Claim BEFORE calling a paid model. Local-background requests retain the
    # legacy optional-key behavior; AI requests above cannot proceed without a key.
    if idempotency_key is not None:
        try:
            previous = execution_store.begin(
                idempotency_key,
                request_hash,
                paid=spec.background_mode == "ai",
            )
            if previous is not None:
                return previous
        except execution_store.InvalidExecutionKey as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except execution_store.PaidGenerationLimitExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except execution_store.ExecutionConfigurationError as exc:
            logger.error("Invalid paid generation limit configuration: %s", exc)
            raise HTTPException(
                status_code=500, detail="AI 生图限额配置无效，请检查服务端环境变量"
            ) from exc
        except execution_store.ExecutionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        if spec.background_mode == "ai":
            background = await generate_ai_background(spec.style)
        else:
            background = local_background(spec.style)
        rendered = compose(spec, sku, brand_logo, background)
        result = storage.create(
            spec, sku, brand_logo, background, rendered,
            idempotency_key=idempotency_key, request_hash=request_hash,
        )
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
        changes = edit.model_dump(exclude_unset=True)
        expected_version = changes.pop("expected_version", None)
        if not changes:
            raise HTTPException(status_code=422, detail="至少修改一个字段")
        # Keep the version comparison and write under one process lock. Without
        # it, two browser tabs could both pass the stale-version check before
        # either revision was committed. Multi-worker deployment is unsupported.
        with storage.LOCK:
            current = storage.view(poster_id)
            if expected_version is not None and current["version"] != expected_version:
                raise HTTPException(
                    status_code=409,
                    detail="海报已有更新版本，请重新打开最新版本后再保存修改",
                )
            existing = PosterSpec.model_validate(current["spec"])
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


@app.get("/api/posters/{poster_id}/versions/{version}")
async def poster_version(poster_id: str, version: int):
    try:
        return storage.version_view(poster_id, version)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/posters/{poster_id}/versions/{version}/image")
async def poster_image(poster_id: str, version: int):
    try:
        return FileResponse(storage.version_path(poster_id, version), media_type="image/png")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
