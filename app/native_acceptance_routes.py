"""Separate quality reports for native 4:5 derivatives, without paid image API calls."""
from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import storage
from app.native_acceptance import (
    StaleNativeEvidence, evaluate_native, report_path, verified_report
)

router = APIRouter(prefix="/api/posters", tags=["native-quality"])


class NativeQualityOptions(BaseModel):
    ocr_mode: Literal["none", "paddle"] = "none"


@router.post("/{poster_id}/versions/{version}/exports/feed_4x5_native/acceptance")
def run_native_acceptance(poster_id: str, version: int, options: NativeQualityOptions):
    try:
        report = evaluate_native(poster_id, version, options.ocr_mode)
        with storage.LOCK:
            storage._atomic_json(
                report_path(poster_id, version, options.ocr_mode), report
            )
        return report
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="海报或版本不存在") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="无法完成原生 4:5 验收，请检查源素材和布局配置") from exc


@router.get("/{poster_id}/versions/{version}/exports/feed_4x5_native/acceptance")
def read_native_acceptance(
    poster_id: str, version: int, ocr_mode: Literal["none", "paddle"] = "none"
):
    try:
        return verified_report(poster_id, version, ocr_mode)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="此导出版本尚无验收记录") from exc
    except StaleNativeEvidence as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="验收报告无效，需重新运行") from exc
