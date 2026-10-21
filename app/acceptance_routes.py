"""Version-specific local Acceptance Gate endpoints.

Report is explicitly opt-in; M3a does not automatically publish, repair or
silently treat unavailable OCR as success.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app import storage
from app.acceptance import AcceptanceOptions, evaluate

router = APIRouter(prefix="/api/posters", tags=["acceptance"])


def report_path(poster_id: str, version: int, mode: str) -> Path:
    storage.version_path(poster_id, version)
    return storage.poster_dir(poster_id) / "versions" / f"v{version}.acceptance.{mode}.json"


@router.post("/{poster_id}/versions/{version}/acceptance")
def run_acceptance(poster_id: str, version: int, options: AcceptanceOptions):
    try:
        report = evaluate(poster_id, version, options.ocr_mode)
        destination = report_path(poster_id, version, options.ocr_mode)
        # Uses existing single-process atomic file replace helper.
        with storage.LOCK:
            storage._atomic_json(destination, report)
        return report
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="海报或版本不存在") from exc
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=422, detail="验收规则或文件格式无效，请检查后台配置") from exc


@router.get("/{poster_id}/versions/{version}/acceptance")
def read_acceptance(poster_id: str, version: int, ocr_mode: str = "none"):
    if ocr_mode not in {"none", "paddle"}:
        raise HTTPException(status_code=422, detail="ocr_mode 只能是 none 或 paddle")
    try:
        destination = report_path(poster_id, version, ocr_mode)
        return json.loads(destination.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="此版本尚无对应验收报告") from exc
