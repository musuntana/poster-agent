"""Read-only status for the default CLI M5g batch journal."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/cli-batches", tags=["cli-batch-status"])

JOURNAL_PATH = (
    Path(__file__).resolve().parents[1]
    / "data/eval_runs/m5g_import/batch_import_results.json"
)
_SKU_ID = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_POSTER_ID = re.compile(r"^[0-9a-f]{32}$")


def _invalid_journal() -> HTTPException:
    return HTTPException(
        status_code=422,
        detail="M5g 命令行批次账本结构不合法或不完整",
    )


def _parse_journal(state: Any) -> dict[str, Any]:
    """Validate persisted JSON at the file boundary before summarizing it."""
    if not isinstance(state, dict):
        raise _invalid_journal()

    expected_ids = state.get("expected_sku_ids")
    expected_count = state.get("expected_sku_count")
    rows = state.get("rows")
    if (
        state.get("schema_version") != "m5g-1"
        or state.get("mode") != "apply"
        or type(state.get("completed")) is not bool
        or not isinstance(expected_ids, list)
        or not 1 <= len(expected_ids) <= 100
        or any(not isinstance(sku, str) or not _SKU_ID.fullmatch(sku)
               for sku in expected_ids)
        or len(set(expected_ids)) != len(expected_ids)
        or type(expected_count) is not int
        or expected_count != len(expected_ids)
        or not isinstance(rows, dict)
        or any(not isinstance(sku, str) for sku in rows)
        or set(rows) - set(expected_ids)
        or not isinstance(state.get("manifest_sha256"), str)
        or not _SHA256.fullmatch(state["manifest_sha256"])
    ):
        raise _invalid_journal()

    for sku, row in rows.items():
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("poster_id"), str)
            or not _POSTER_ID.fullmatch(row["poster_id"])
            or type(row.get("version")) is not int
            or row["version"] != 1
        ):
            raise _invalid_journal()

    summaries = [
        {"sku_id": sku, "status": "COMPLETED" if sku in rows else "PENDING"}
        for sku in expected_ids
    ]
    completed_count = sum(item["status"] == "COMPLETED" for item in summaries)
    complete = state["completed"] is True and completed_count == expected_count
    return {
        "available": True,
        "source": "cli_default_journal",
        "mode": "apply",
        "completed": complete,
        "expected_sku_count": expected_count,
        "completed_sku_count": completed_count,
        "pending_sku_count": expected_count - completed_count,
        "manifest_sha256": state["manifest_sha256"],
        "rows": summaries,
        "note": "只读取默认命令行账本；不运行生成、OCR 或自动验收",
    }


@router.get("/status")
def cli_batch_status() -> dict[str, Any]:
    """Expose summary only; never accept or return caller-controlled paths."""
    if JOURNAL_PATH.is_symlink():
        raise HTTPException(status_code=422, detail="M5g 命令行批次账本不能是符号链接")
    if not JOURNAL_PATH.is_file():
        return {
            "available": False,
            "message": "未找到默认命令行 M5g 批次账本；自定义输出目录不会显示在此处",
            "expected_sku_count": 0,
            "completed_sku_count": 0,
            "pending_sku_count": 0,
            "completed": False,
            "rows": [],
        }
    try:
        state = json.loads(JOURNAL_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="M5g 命令行批次账本无法读取") from exc
    return _parse_journal(state)
