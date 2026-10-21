"""Optional PaddleOCR 3.x adapter. Never claim OCR was performed when unavailable.

Install separately: see docs/uv-workflow.md. CI injects a fake adapter, so it
does not download large OCR weights.
"""
from __future__ import annotations

from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from platform import machine, python_version



class OCRLines(list):
    """Compatibility-preserving list of observations with adapter telemetry.

    This is diagnostic metadata, NOT cryptographic model-execution attestation.
    """

    def __init__(self, lines: list[dict], runtime: dict):
        super().__init__(lines)
        self.runtime = runtime


def _package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


class OCRUnavailable(RuntimeError):
    pass


class OCRExecutionError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _engine():
    try:
        from paddleocr import PaddleOCR
    except (ImportError, OSError) as exc:
        raise OCRUnavailable("PaddleOCR 未安装或推理依赖不可用，参见 docs/uv-workflow.md") from exc
    try:
        return PaddleOCR(
            lang="ch",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
    except Exception as exc:
        raise OCRUnavailable("PaddleOCR 模型无法初始化，请检查本机 CPU/模型依赖") from exc


def _json_dict(result):
    data = getattr(result, "json", None)
    if callable(data):
        data = data()
    if not isinstance(data, dict):
        try:
            data = dict(result)
        except (TypeError, ValueError) as exc:
            raise OCRExecutionError("无法解析 PaddleOCR 响应格式") from exc
    if "res" in data and isinstance(data["res"], dict):
        data = data["res"]
    return data


def recognize(image_path: Path) -> list[dict]:
    """Return per-line text, score and optional pixel bbox (PaddleOCR 3.x)."""
    try:
        results = _engine().predict(str(image_path))
        lines = []
        for result in results:
            data = _json_dict(result)
            texts = data.get("rec_texts", [])
            scores = data.get("rec_scores", [])
            boxes = data.get("rec_boxes", [])
            for i, value in enumerate(texts):
                box = boxes[i] if i < len(boxes) else None
                if box is not None and hasattr(box, "tolist"):
                    box = box.tolist()
                bbox = [int(v) for v in box] if box is not None and len(box) == 4 else None
                lines.append({
                    "text": str(value),
                    "score": float(scores[i]) if i < len(scores) else None,
                    "bbox": bbox,
                })
        return OCRLines(lines, {
            "source": "paddleocr_adapter",
            "paddleocr_package_version": _package_version("paddleocr"),
            "paddlepaddle_package_version": _package_version("paddlepaddle"),
            "python_version": python_version(),
            "machine": machine(),
            "line_count": len(lines),
            "attested": False,
        })
    except (OCRUnavailable, OCRExecutionError):
        raise
    except Exception as exc:
        # Avoid exposing model stack traces / file paths to API callers.
        raise OCRExecutionError("PaddleOCR 识别失败；请在服务器日志排查模型运行环境") from exc
