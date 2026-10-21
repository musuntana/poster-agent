"""Version-specific local Acceptance Gate endpoints.

Report is explicitly opt-in; M3a does not automatically publish, repair or
silently treat unavailable OCR as success.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Response

from app import storage
from app.acceptance import AcceptanceOptions, evaluate
from app.ocr_overlay import StaleAcceptanceReport, render_overlay

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


@router.get("/{poster_id}/versions/{version}/acceptance/overlay")
def acceptance_overlay(
    poster_id: str, version: int, ocr_mode: Literal["none", "paddle"] = "paddle"
):
    """Diagnostic PNG only: do not re-run OCR or alter the underlying poster."""
    try:
        saved = report_path(poster_id, version, ocr_mode)
        report = json.loads(saved.read_text(encoding="utf-8"))
        png, details = render_overlay(poster_id, version, report)
        return Response(
            content=png,
            media_type="image/png",
            headers={
                "Cache-Control": "no-store",
                "X-Source-Image-SHA256": details["image_sha256"],
                "X-OCR-Status": details["ocr_status"],
                "X-OCR-Boxes-Drawn": str(details["bbox_drawn"]),
            },
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="该版本尚无对应验收报告，先执行 Acceptance Gate") from exc
    except StaleAcceptanceReport as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="现有验收报告无效，不能生成证据叠加图") from exc
