"""M5g: offline-first real SKU batch preparation, using the existing HTTP API.

No paid model: the only permitted background mode is "local". By default
only validate and write a plan. --apply creates real posters using the
existing upload/ICC/PosterSpec/idempotency pipeline via FastAPI TestClient.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import io
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import storage
from app.main import MAX_UPLOAD, app, read_upload
from app.schemas import PosterSpec

REQUIRED = frozenset({"sku_id", "product_image", "title", "price"})
OPTIONAL = frozenset({"original_price", "subtitle", "cta", "style", "logo_image"})
ALLOWED = REQUIRED | OPTIONAL
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg",
               ".jpeg": "image/jpeg", ".webp": "image/webp"}
SKU_ID = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
MAX_ROWS = 100
SCHEMA = "m5g-1"


class BatchError(ValueError):
    """Preflight, integrity, or safe-retry error for a local batch."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _source_file(value: str, directory: Path) -> Path:
    if not value or value.strip() != value or Path(value).is_absolute():
        raise BatchError("图片路径必须为 CSV 所在文件夹内的相对路径")
    path = (directory / value).resolve()
    if not path.is_relative_to(directory) or not path.is_file():
        raise BatchError("商品图片不存在或位于清单目录之外")
    if path.suffix.lower() not in IMAGE_TYPES:
        raise BatchError("图片只支持 PNG、JPEG、WEBP")
    return path


def _validated_image(path: Path) -> str:
    size = path.stat().st_size
    if not 0 < size <= MAX_UPLOAD:
        raise BatchError("图片为空或超过 10 MiB 上传上限")
    raw = path.read_bytes()
    try:
        # Same upload decoding, ICC conversion, EXIF treatment, pixel cap.
        asyncio.run(read_upload(UploadFile(file=io.BytesIO(raw), filename=path.name)))
    except HTTPException as exc:
        raise BatchError(f"上传图片未通过现有安全校验（HTTP {exc.status_code}）") from exc
    return _sha(raw)


def preflight(csv_path: Path) -> tuple[str, list[dict]]:
    csv_path = Path(csv_path)
    if csv_path.is_symlink():
        raise BatchError("CSV 清单不能是符号链接")
    csv_path = csv_path.resolve(strict=True)
    if not csv_path.is_file():
        raise BatchError("CSV 清单必须是普通文件")
    base = csv_path.parent
    raw_csv = csv_path.read_bytes()
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames
        if not columns or not REQUIRED.issubset(columns):
            raise BatchError("CSV 必须包含 sku_id, product_image, title, price")
        if len(set(columns)) != len(columns) or set(columns) - ALLOWED:
            raise BatchError("CSV 含重复或不支持的列；禁止设置 background_mode")
        records = list(reader)
    if not records or len(records) > MAX_ROWS:
        raise BatchError("每个批次应包含 1–100 行 SKU")

    seen = set()
    prepared = []
    for line, row in enumerate(records, start=2):
        try:
            if None in row:
                raise BatchError("列数与表头不一致")
            sku = (row.get("sku_id") or "").strip()
            if not SKU_ID.fullmatch(sku) or sku in seen:
                raise BatchError("sku_id 无效或批次内重复")
            seen.add(sku)
            spec_fields = {
                key: (row.get(key) or "").strip()
                for key in ("title", "price", "original_price", "subtitle", "cta", "style")
            }
            data = {
                "title": spec_fields["title"],
                "price": spec_fields["price"],
                "original_price": spec_fields["original_price"],
                "style": spec_fields["style"] or "蓝色科技风",
                "subtitle": spec_fields["subtitle"] or "品质生活 · 限时优惠",
                "cta": spec_fields["cta"] or "立即抢购",
                "background_mode": "local",
            }
            PosterSpec.model_validate({
                **data, "original_price": data["original_price"] or None,
            })
            product = _source_file(row["product_image"], base)
            product_sha = _validated_image(product)
            logo = None
            logo_sha = None
            if (row.get("logo_image") or "").strip():
                logo = _source_file(row["logo_image"], base)
                logo_sha = _validated_image(logo)
            # Scope the idempotency key to the manifest *path* + stable SKU ID;
            # edited inputs under the same ID must conflict, not create twice.
            key = "m5g_" + _sha((str(csv_path) + "\n" + sku).encode())[:56]
            request_digest = _sha(json.dumps({
                "sku_id": sku, "data": data,
                "product_sha256": product_sha, "logo_sha256": logo_sha,
            }, ensure_ascii=False, sort_keys=True).encode())
            prepared.append({
                "sku_id": sku, "data": data, "product": product,
                "logo": logo, "product_sha256": product_sha,
                "logo_sha256": logo_sha, "idempotency_key": key,
                "request_sha256": request_digest,
            })
        except (BatchError, ValidationError, OSError, ValueError) as exc:
            raise BatchError(f"CSV 第 {line} 行校验失败：{exc}") from exc
    return _sha(raw_csv), prepared


def _write_json_atomic(destination: Path, value: dict) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    staging.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    os.replace(staging, destination)


def _file_parts(item: dict) -> dict:
    parts = {}
    for key, path in (("product", item["product"]), ("logo", item["logo"])):
        if path is not None:
            data = path.read_bytes()
            expected = item["product_sha256"] if key == "product" else item["logo_sha256"]
            if _sha(data) != expected:
                raise BatchError(f"{item['sku_id']}：预检后 {key} 文件发生变化，拒绝生成")
            parts[key] = (path.name, data, IMAGE_TYPES[path.suffix.lower()])
    return parts


def run_batch(
    manifest: Path,
    output_dir: Path,
    *,
    apply: bool = False,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    manage_lifespan: bool = True,
) -> dict:
    manifest_sha, prepared = preflight(manifest)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "batch_import_results.json"
    if output.is_symlink():
        raise BatchError("批次账本不能是符号链接")
    expected_sku_ids = [item["sku_id"] for item in prepared]
    if apply and output.exists():
        state = json.loads(output.read_text(encoding="utf-8"))
        if state.get("manifest_sha256") != manifest_sha:
            raise BatchError("导入清单已经变化；请使用新批次目录，不允许覆盖已有记录")
        if state.get("schema_version") != SCHEMA or state.get("mode") != "apply" or \
                not isinstance(state.get("rows"), dict):
            raise BatchError("现有批次账本结构不合法，拒绝续跑")
        if set(state["rows"]) - set(expected_sku_ids):
            raise BatchError("现有批次账本包含当前 CSV 之外的 SKU")
        # Older M5g journals have no completion metadata. A safe --apply
        # rerun can backfill it after checking all expected rows.
        if "expected_sku_ids" in state and state["expected_sku_ids"] != expected_sku_ids:
            raise BatchError("现有批次账本的预期 SKU 与当前 CSV 不一致")
        if "expected_sku_count" in state and (
            type(state["expected_sku_count"]) is not int
            or state["expected_sku_count"] != len(expected_sku_ids)
        ):
            raise BatchError("现有批次账本的预期 SKU 数量不一致")
    else:
        state = {
            "schema_version": SCHEMA, "manifest_sha256": manifest_sha,
            "mode": "apply", "rows": {},
        }
    if apply:
        state["expected_sku_ids"] = expected_sku_ids
        state["expected_sku_count"] = len(expected_sku_ids)
        # Persist incomplete state after each SKU; only the final successful
        # pass may publish a completed batch journal.
        state["completed"] = False

    plan = {
        "schema_version": SCHEMA, "manifest_sha256": manifest_sha,
        "mode": "apply" if apply else "check",
        "total_skus": len(prepared),
        "no_paid_model": True,
        "unique_source_files": len({p["product_sha256"] for p in prepared}),
        "sku_ids": [p["sku_id"] for p in prepared],
    }
    if not apply:
        _write_json_atomic(output_dir / "batch_preflight.json", plan)
        return plan

    def report(phase: str, item: dict, poster_id: str | None = None) -> None:
        if progress_callback is not None:
            progress_callback({
                "phase": phase,
                "processed": len(state["rows"]),
                "total": len(prepared),
                "current_sku": item["sku_id"] if phase != "COMPLETED" else None,
                "poster_id": poster_id,
            })

    # The existing local-only FastAPI route owns upload validation, storage,
    # and idempotency. Web batches avoid re-running app startup recovery inside
    # an already-running server; the CLI keeps its historical lifespan behavior.
    def apply_rows(client: TestClient) -> None:
        for item in prepared:
            sku = item["sku_id"]
            report("STARTED", item)
            old = state["rows"].get(sku)
            if old is not None:
                if old.get("request_sha256") != item["request_sha256"]:
                    raise BatchError(f"{sku}：同一 sku_id 的输入已更改，拒绝覆盖历史")
                try:
                    storage.version_path(old["poster_id"], 1)
                except (KeyError, FileNotFoundError) as exc:
                    raise BatchError(f"{sku}：导入账本指向不存在的海报，需人工对账") from exc
                report("ROW_COMPLETED", item, old["poster_id"])
                continue

            files = _file_parts(item)
            result = client.post(
                "/api/posters", files=files, data=item["data"],
                headers={"Idempotency-Key": item["idempotency_key"]},
            )
            if result.status_code != 201:
                raise BatchError(
                    f"{sku}：创建失败（HTTP {result.status_code}）。"
                    "不自动重试状态未知的任务，请检查本地执行账本"
                )
            reply = result.json()
            poster_id = reply.get("poster_id")
            if not isinstance(poster_id, str):
                raise BatchError(f"{sku}：API 没有返回有效海报 ID")
            try:
                storage.version_path(poster_id, 1)
            except FileNotFoundError as exc:
                raise BatchError(f"{sku}：API 结果与本地海报存储不一致") from exc
            state["rows"][sku] = {
                "poster_id": poster_id, "version": 1,
                "request_sha256": item["request_sha256"],
                "product_sha256": item["product_sha256"],
                "logo_sha256": item["logo_sha256"],
                "idempotency_key": item["idempotency_key"],
            }
            # Commit success after every SKU. If the process dies between API
            # response and this write, the execution ledger recovers the same
            # response on resume without another poster render.
            _write_json_atomic(output, state)
            report("ROW_COMPLETED", item, poster_id)

    if manage_lifespan:
        with TestClient(app) as client:
            apply_rows(client)
    else:
        client = TestClient(app)
        try:
            apply_rows(client)
        finally:
            client.close()

    if set(state["rows"]) != set(expected_sku_ids):
        raise BatchError("导入未覆盖全部预期 SKU，拒绝标记为完成")
    state["completed"] = True
    _write_json_atomic(output, state)
    result = {
        **plan,
        "created_or_reused": len(state["rows"]),
        "poster_ids": {sku: row["poster_id"] for sku, row in state["rows"].items()},
    }
    if progress_callback is not None:
        progress_callback({
            "phase": "COMPLETED",
            "processed": len(state["rows"]),
            "total": len(prepared),
            "current_sku": None,
            "poster_id": None,
        })
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare real product SKUs offline; import only with --apply"
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path,
                        default=Path("data/eval_runs/m5g_import"))
    parser.add_argument("--apply", action="store_true",
                        help="Actually create local-background posters (no AI calls)")
    args = parser.parse_args()
    try:
        result = run_batch(args.manifest, args.output_dir, apply=args.apply)
    except (BatchError, OSError) as exc:
        parser.exit(2, f"批量导入未完成：{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
