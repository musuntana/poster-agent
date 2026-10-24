"""Local-only web API for staged, resumable real-SKU batch imports."""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import threading
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile

router = APIRouter(prefix="/api/batches", tags=["batch-import"])

MAX_MANIFEST_BYTES = 1 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_BATCH_BYTES = 100 * 1024 * 1024
MAX_IMAGE_FILES = 200
_ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_BATCH_ID = re.compile(r"^[0-9a-f]{32}$")
_ACTIVE_BATCHES: set[str] = set()
_ACTIVE_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _root() -> Path:
    return Path(
        os.environ.get("POSTER_BATCH_DIR", "data/eval_runs/web_batches")
    ).expanduser().resolve()


def _batch_dir(batch_id: str) -> Path:
    if not _BATCH_ID.fullmatch(batch_id):
        raise HTTPException(status_code=422, detail="批次 ID 格式不合法")
    root = _root()
    path = root / batch_id
    if path.is_symlink() or not path.is_dir():
        raise HTTPException(status_code=404, detail="批次不存在")
    return path


def _safe_filename(name: str | None, *, manifest: bool = False) -> str:
    if (
        not name
        or name in {".", ".."}
        or name != name.strip()
        or "/" in name
        or "\\" in name
        or any(ord(char) < 32 for char in name)
        or Path(name).name != name
    ):
        raise HTTPException(status_code=422, detail="文件名不合法；请上传平铺目录中的文件")
    suffix = Path(name).suffix.lower()
    allowed = {".csv"} if manifest else _ALLOWED_IMAGE_SUFFIXES
    if suffix not in allowed:
        raise HTTPException(
            status_code=422,
            detail="清单只支持 CSV；图片只支持 PNG、JPEG、WEBP",
        )
    return name


async def _read_limited(upload: UploadFile, maximum: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await upload.read(64 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > maximum:
            raise HTTPException(status_code=413, detail="上传文件超过大小限制")
        chunks.append(chunk)
    return b"".join(chunks)


def _write_json_atomic(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_state(batch_dir: Path) -> dict:
    path = batch_dir / "batch_status.json"
    if path.is_symlink():
        raise HTTPException(status_code=409, detail="批次状态文件不能是符号链接")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="批次状态文件缺失或损坏") from exc
    if not isinstance(state, dict) or state.get("batch_id") != batch_dir.name:
        raise HTTPException(status_code=409, detail="批次状态文件结构不合法")
    return state


def _public_state(batch_dir: Path, state: dict) -> dict:
    journal_path = batch_dir / "batch_import_results.json"
    journal_rows: dict = {}
    if journal_path.is_file() and not journal_path.is_symlink():
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            if isinstance(journal, dict) and isinstance(journal.get("rows"), dict):
                journal_rows = journal["rows"]
        except (OSError, ValueError):
            journal_rows = {}

    plan = state.get("plan") if isinstance(state.get("plan"), dict) else {}
    sku_ids = plan.get("sku_ids") if isinstance(plan.get("sku_ids"), list) else []
    status = state.get("status", "UNKNOWN")
    current_sku = state.get("current_sku")
    rows = []
    for sku in sku_ids:
        result = journal_rows.get(sku)
        if isinstance(result, dict) and isinstance(result.get("poster_id"), str):
            row_status = "COMPLETED"
        elif status == "FAILED" and current_sku == sku:
            row_status = "FAILED"
        elif status == "INTERRUPTED" and current_sku == sku:
            row_status = "UNKNOWN"
        else:
            row_status = "PENDING"
        rows.append({
            "sku_id": sku,
            "status": row_status,
            "poster_id": result.get("poster_id") if row_status == "COMPLETED" else None,
            "version": result.get("version") if row_status == "COMPLETED" else None,
        })

    return {
        "batch_id": state["batch_id"],
        "status": status,
        "created_at": state.get("created_at"),
        "updated_at": state.get("updated_at"),
        "processed": min(len(journal_rows), len(sku_ids)),
        "total": len(sku_ids),
        "current_sku": current_sku,
        "error": state.get("error"),
        "plan": {
            "total_skus": plan.get("total_skus", len(sku_ids)),
            "unique_source_files": plan.get("unique_source_files", 0),
            "no_paid_model": True,
            "sku_ids": sku_ids,
        },
        "rows": rows,
    }


@router.post("", status_code=201)
async def create_batch(
    manifest: UploadFile = File(...),
    images: list[UploadFile] = File(...),
):
    """Stage and fully preflight a CSV cohort before any poster can be created."""
    manifest_name = _safe_filename(manifest.filename, manifest=True)
    del manifest_name  # The server stores it under a fixed safe name.
    if not 1 <= len(images) <= MAX_IMAGE_FILES:
        raise HTTPException(
            status_code=422,
            detail=f"请上传 1–{MAX_IMAGE_FILES} 张 CSV 引用的商品图或 Logo",
        )

    image_names: set[str] = set()
    for image in images:
        name = _safe_filename(image.filename)
        if name.casefold() in image_names:
            raise HTTPException(status_code=422, detail="上传图片文件名重复（忽略大小写）")
        image_names.add(name.casefold())

    manifest_bytes = await _read_limited(manifest, MAX_MANIFEST_BYTES)
    total_bytes = len(manifest_bytes)
    image_payloads: list[tuple[str, bytes]] = []
    for image in images:
        name = _safe_filename(image.filename)
        payload = await _read_limited(image, MAX_IMAGE_BYTES)
        total_bytes += len(payload)
        if total_bytes > MAX_BATCH_BYTES:
            raise HTTPException(status_code=413, detail="批次总上传大小超过 100 MiB")
        image_payloads.append((name, payload))

    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    batch_id = uuid4().hex
    batch_dir = root / batch_id
    batch_dir.mkdir(mode=0o700, exist_ok=False)
    batch = None
    try:
        (batch_dir / "manifest.csv").write_bytes(manifest_bytes)
        for name, payload in image_payloads:
            (batch_dir / name).write_bytes(payload)

        batch = import_module("scripts.real_sku_batch")
        manifest_sha, prepared = await asyncio.to_thread(
            batch.preflight, batch_dir / "manifest.csv"
        )
        referenced = {
            path.name
            for item in prepared
            for path in (item["product"], item["logo"])
            if path is not None
        }
        uploaded = {name for name, _ in image_payloads}
        unused = sorted(uploaded - referenced)
        if unused:
            raise batch.BatchError(
                "上传了未被 CSV 引用的图片：" + "、".join(unused[:5])
            )

        plan = {
            "manifest_sha256": manifest_sha,
            "total_skus": len(prepared),
            "unique_source_files": len({item["product_sha256"] for item in prepared}),
            "no_paid_model": True,
            "sku_ids": [item["sku_id"] for item in prepared],
        }
        now = _now()
        state = {
            "batch_id": batch_id,
            "status": "READY",
            "created_at": now,
            "updated_at": now,
            "processed": 0,
            "current_sku": None,
            "error": None,
            "plan": plan,
        }
        _write_json_atomic(batch_dir / "batch_status.json", state)
        _write_json_atomic(batch_dir / "batch_preflight.json", {
            "schema_version": "m5g-1",
            "manifest_sha256": manifest_sha,
            "mode": "check",
            **plan,
        })
        return _public_state(batch_dir, state)
    except HTTPException:
        shutil.rmtree(batch_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(batch_dir, ignore_errors=True)
        if batch is not None and isinstance(exc, batch.BatchError):
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if isinstance(exc, (UnicodeDecodeError, OSError, ValueError)):
            raise HTTPException(status_code=422, detail=f"批次预检失败：{exc}") from exc
        raise


def _execute_batch(batch_id: str) -> None:
    try:
        batch_dir = _batch_dir(batch_id)
        _read_state(batch_dir)

        def report(event: dict) -> None:
            latest = _read_state(batch_dir)
            if latest.get("status") not in {"RUNNING", "COMPLETED"}:
                return
            latest["processed"] = event.get("processed", latest.get("processed", 0))
            latest["current_sku"] = event.get("current_sku")
            latest["updated_at"] = _now()
            if event.get("phase") == "COMPLETED":
                latest["status"] = "COMPLETED"
                latest["error"] = None
            _write_json_atomic(batch_dir / "batch_status.json", latest)

        try:
            batch = import_module("scripts.real_sku_batch")
            result = batch.run_batch(
                batch_dir / "manifest.csv",
                batch_dir,
                apply=True,
                progress_callback=report,
                manage_lifespan=False,
            )
            state = _read_state(batch_dir)
            state.update({
                "status": "COMPLETED",
                "processed": result["created_or_reused"],
                "current_sku": None,
                "error": None,
                "updated_at": _now(),
            })
            _write_json_atomic(batch_dir / "batch_status.json", state)
        except Exception as exc:
            try:
                state = _read_state(batch_dir)
                state.update({
                    "status": "FAILED",
                    "error": str(exc).replace(str(batch_dir), "[batch]")[:500],
                    "updated_at": _now(),
                })
                _write_json_atomic(batch_dir / "batch_status.json", state)
            except Exception:
                pass
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE_BATCHES.discard(batch_id)


@router.post("/{batch_id}/apply", status_code=202)
def apply_batch(batch_id: str, background_tasks: BackgroundTasks):
    batch_dir = _batch_dir(batch_id)
    with _ACTIVE_LOCK:
        # Read status under the same lock used when the worker exits. Otherwise
        # a stale RUNNING snapshot could overwrite a just-completed batch.
        state = _read_state(batch_dir)
        if state.get("status") == "COMPLETED":
            return _public_state(batch_dir, state)
        if batch_id in _ACTIVE_BATCHES:
            raise HTTPException(status_code=409, detail="该批次正在执行")
        if _ACTIVE_BATCHES:
            raise HTTPException(status_code=409, detail="一次只允许执行一个本地批次")
        if state.get("status") == "RUNNING":
            state["status"] = "INTERRUPTED"
            state["error"] = "执行进程已不在当前服务中；续跑前会复用已有 SKU 账本"
        if state.get("status") not in {"READY", "FAILED", "INTERRUPTED"}:
            raise HTTPException(status_code=409, detail="当前批次状态不允许启动")
        state.update({
            "status": "RUNNING",
            "processed": state.get("processed", 0),
            "current_sku": None,
            "error": None,
            "updated_at": _now(),
        })
        _write_json_atomic(batch_dir / "batch_status.json", state)
        _ACTIVE_BATCHES.add(batch_id)
    background_tasks.add_task(_execute_batch, batch_id)
    return _public_state(batch_dir, state)


@router.get("/{batch_id}")
def get_batch(batch_id: str):
    batch_dir = _batch_dir(batch_id)
    state = _read_state(batch_dir)
    if state.get("status") == "RUNNING":
        with _ACTIVE_LOCK:
            if batch_id not in _ACTIVE_BATCHES:
                # Re-read after the worker-exit lock: a stale RUNNING snapshot
                # must not replace COMPLETED/FAILED written just before exit.
                state = _read_state(batch_dir)
                if state.get("status") == "RUNNING":
                    state.update({
                        "status": "INTERRUPTED",
                        "error": "服务可能在批次执行期间重启；可安全续跑相同批次",
                        "updated_at": _now(),
                    })
                    _write_json_atomic(batch_dir / "batch_status.json", state)
    return _public_state(batch_dir, state)
