"""Read-only status for the default local M5g batch journal."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/batches", tags=["local-batch-import"])

JOURNAL_PATH = Path(__file__).resolve().parents[1] / "data/eval_runs/m5g_import/batch_import_results.json"


@router.get("/status")
def local_batch_status() -> dict[str, Any]:
    """Expose progress only; never accept a caller-controlled filesystem path."""
    if not JOURNAL_PATH.is_file():
        return {
            "available": False,
            "message": "未找到默认 M5g 批次账本；请先运行批量导入命令",
            "expected_sku_count": 0,
            "completed_sku_count": 0,
            "pending_sku_count": 0,
            "completed": False,
            "rows": [],
        }
    try:
        state = json.loads(JOURNAL_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="M5g 批次账本无法读取") from exc
    rows = state.get("rows")
    expected_ids = state.get("expected_sku_ids")
    expected_count = state.get("expected_sku_count")
    if (
        state.get("schema_version") != "m5g-1"
        or state.get("mode") != "apply"
        or not isinstance(rows, dict)
        or not isinstance(expected_ids, list)
        or type(expected_count) is not int
        or expected_count != len(expected_ids)
        or len(set(expected_ids)) != len(expected_ids)
        or set(rows) - set(expected_ids)
    ):
        raise HTTPException(status_code=422, detail="M5g 批次账本结构或预期 SKU 清单不合法")

    summaries = []
    for sku in expected_ids:
        item = rows.get(sku)
        poster_id = item.get("poster_id") if isinstance(item, dict) else None
        done = (
            isinstance(poster_id, str)
            and len(poster_id) == 32
            and all(ch in "0123456789abcdef" for ch in poster_id)
            and item.get("version") == 1
        )
        summaries.append({"sku_id": sku, "status": "COMPLETED" if done else "PENDING"})
    completed_count = sum(item["status"] == "COMPLETED" for item in summaries)
    complete = state.get("completed") is True and completed_count == expected_count
    return {
        "available": True,
        "mode": "apply",
        "completed": complete,
        "expected_sku_count": expected_count,
        "completed_sku_count": completed_count,
        "pending_sku_count": expected_count - completed_count,
        "manifest_sha256": state.get("manifest_sha256"),
        "rows": summaries,
        "note": "仅读取本地账本；不运行生成、OCR 或自动验收",
    }
