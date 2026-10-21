"""Local OCR environment diagnosis and evidence of a real model invocation.

Unlike mock-based tests, inference is opt-in and loads the actual OCR provider.
A NOT_RUN or failed result NEVER contributes to claimed OCR quality metrics.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from PIL import Image

from app.ocr_provider import OCRUnavailable, OCRExecutionError, recognize


def _package_version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def _module_available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def environment() -> dict:
    version = sys.version_info
    return {
        "platform": platform.system(),
        "machine": platform.machine(),
        "python": f"{version.major}.{version.minor}.{version.micro}",
        "python_ocr_wheel_candidate": (version.major, version.minor) >= (3, 9)
        and (version.major, version.minor) <= (3, 13),
        "paddlepaddle_distribution": _package_version("paddlepaddle"),
        "paddleocr_distribution": _package_version("paddleocr"),
        "paddle_module_detected": _module_available("paddle"),
        "paddleocr_module_detected": _module_available("paddleocr"),
        "note": "Package presence is not proof that OCR initialized or ran successfully.",
    }


def _image_evidence(image_path: Path) -> dict:
    raw = image_path.read_bytes()
    with Image.open(image_path) as image:
        image.verify()
        image_format = image.format
    if image_format not in ("PNG", "JPEG", "WEBP"):
        raise ValueError("OCR probe only supports PNG/JPEG/WEBP input")
    with Image.open(image_path) as image:
        size = [image.width, image.height]
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "format": image_format,
        "pixel_size": size,
        "bytes": len(raw),
    }


def probe(
    image_path: Path | None = None,
    *,
    infer: bool = False,
    include_text: bool = False,
    runner: Callable[[Path], list[dict]] = recognize,
) -> dict:
    """Return a report suitable for local persistence.

    Without infer=True no model is loaded or called. The runner argument exists
    only to unit-test orchestration without downloading model weights.
    """
    report = {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "engine": "PaddleOCR",
        "environment": environment(),
        "mode": "inference" if infer else "diagnosis",
        "status": "NOT_RUN",
        "model_invocation_completed": False,
        "elapsed_ms": None,
        "line_count": None,
        "mean_recognition_score": None,
        "image": None,
        "message": "仅完成环境诊断，未加载或调用模型",
    }
    if image_path is not None:
        report["image"] = _image_evidence(image_path)

    if not infer:
        return report
    if image_path is None:
        raise ValueError("真实 OCR 推理必须传入 --image 指定一个图片文件")

    started = time.perf_counter()
    try:
        lines = runner(image_path)
        if not isinstance(lines, list):
            raise OCRExecutionError("OCR 返回的数据格式不是行列表")
        scores = [
            float(line["score"]) for line in lines
            if isinstance(line, dict) and line.get("score") is not None
        ]
        report["status"] = "OK"
        report["model_invocation_completed"] = True
        report["line_count"] = len(lines)
        report["mean_recognition_score"] = (
            round(sum(scores) / len(scores), 6) if scores else None
        )
        report["message"] = "OCR 调用完成；识别置信度不能替代人工标注准确率"
        if include_text:
            report["ocr_lines"] = lines
            report["privacy_warning"] = "OCR 文本可能包含用户或业务信息，请勿直接上传本报告"
    except OCRUnavailable:
        report["status"] = "UNAVAILABLE"
        report["message"] = "PaddleOCR 未安装或模型无法初始化；没有执行完成的推理结果"
    except (OCRExecutionError, ValueError, TypeError):
        report["status"] = "ERROR"
        report["message"] = "PaddleOCR 推理失败；没有执行完成的推理结果"
    finally:
        report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return report


def save_report(result: dict, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Prevent partial JSON when interrupted. Local path belongs to the user.
    temp = destination.with_suffix(destination.suffix + ".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(destination)
