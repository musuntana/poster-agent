"""M5e: local, read-only native 4:5 evaluation cohort readiness.

Reuses M5c's actual reviewer context and M4c's verified report. This module
does NOT run OCR, generate images with AI, or fabricate an accuracy metric.
"""
from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter
from pathlib import Path

from app import native_human_reviews as reviews
from app import storage
from app.human_annotations import REVIEWER_PATTERN, VALID_ID
from app.native_acceptance import StaleNativeEvidence, verified_report
from evals.native_real_reports import OCR_FIELDS, _text_prediction

BLOCKED = {
    "REVIEW_STALE", "SOURCE_INVALID", "OCR_MISSING", "OCR_STALE",
    "OCR_NOT_OK", "OCR_CHECKS_INVALID",
}


def audit_trial(reviewer: str) -> tuple[dict, list[dict]]:
    if not re.fullmatch(REVIEWER_PATTERN, reviewer):
        raise ValueError("审核员代号只能使用 2–40 位字母、数字、下划线和短横线")
    rows: list[dict] = []
    with storage.LOCK:
        for poster in sorted(storage.root().glob("*")):
            if not poster.is_dir() or not VALID_ID.fullmatch(poster.name):
                continue
            version_dir = poster / "versions"
            if not version_dir.is_dir():
                continue
            for source in sorted(version_dir.glob("v[0-9]*.png")):
                try:
                    version = int(source.stem[1:])
                except ValueError:
                    continue
                row = {
                    "poster_id": poster.name, "version": version,
                    "status": "", "definite_labels": 0,
                    "ocr_text_labels": 0, "unsure_labels": 0,
                    "product_sha256": "", "native_image_sha256": "",
                    "action": "",
                }
                try:
                    ctx = reviews.context(poster.name, version, reviewer)
                    row["product_sha256"] = ctx["asset_sha256"]["product"]
                    row["native_image_sha256"] = ctx["image_sha256"]
                    selected = ctx["existing_review"]
                    if selected is None:
                        row["status"] = "NEEDS_REVIEW"
                        row["action"] = "在网页人工审核当前原生 4:5 PNG"
                    else:
                        reviewed = [selected["fields"][key]["judgment"]
                                    for key in ctx["fields_to_review"]]
                        row["definite_labels"] = sum(x != "unsure" for x in reviewed)
                        row["unsure_labels"] = reviewed.count("unsure")
                        row["ocr_text_labels"] = sum(
                            selected["fields"][key]["judgment"] != "unsure"
                            for key in ctx["fields_to_review"] if key in OCR_FIELDS
                        )
                        if row["definite_labels"] == 0:
                            row["status"] = "REVIEW_UNSURE"
                            row["action"] = "人工复核不确定的字段；这些字段不参与评分"
                        else:
                            try:
                                report = verified_report(poster.name, version, "paddle")
                            except FileNotFoundError:
                                row["status"] = "OCR_MISSING"
                                row["action"] = "对原生 4:5 图片手动执行 PaddleOCR 验收"
                            except (StaleNativeEvidence, ValueError, OSError, KeyError):
                                row["status"] = "OCR_STALE"
                                row["action"] = "素材/规格或报告变化，重新运行原生 PaddleOCR"
                            else:
                                if report.get("ocr_status") != "OK" or report.get("ocr_mode") != "paddle":
                                    row["status"] = "OCR_NOT_OK"
                                    row["action"] = "排查 OCR 引擎，成功执行后重测"
                                elif report.get("image_dimensions") != [1080, 1350]:
                                    row["status"] = "OCR_CHECKS_INVALID"
                                    row["action"] = "报告画布尺寸错误，必须重新验收"
                                else:
                                    try:
                                        checks = report["checks"]
                                        if not isinstance(checks, list):
                                            raise ValueError("missing checks")
                                        indexed = {}
                                        for check in checks:
                                            name = check["name"]
                                            if name in indexed:
                                                raise ValueError("duplicate check")
                                            indexed[name] = check["status"]
                                        for field in ctx["fields_to_review"]:
                                            if field in OCR_FIELDS and selected["fields"][field]["judgment"] != "unsure":
                                                _text_prediction(field, indexed)
                                    except (KeyError, TypeError, ValueError):
                                        row["status"] = "OCR_CHECKS_INVALID"
                                        row["action"] = "OCR 字段或价格区域检查不完整，重新验收"
                                    else:
                                        row["status"] = "READY"
                                        row["action"] = "可以参与原生 4:5 离线评测"
                except (FileNotFoundError, OSError, ValueError, KeyError, TypeError) as exc:
                    # This path is a diagnostic, not evidence of valid inputs.
                    row["status"] = (
                        "REVIEW_STALE" if isinstance(exc, reviews.NativeReviewConflict)
                        else "SOURCE_INVALID"
                    )
                    row["action"] = "检查图片、规格与最新人工审核的哈希或 JSON 完整性"
                rows.append(row)

    counts = Counter(row["status"] for row in rows)
    labeled = [row for row in rows if row["definite_labels"] > 0]
    ready = [row for row in labeled if row["status"] == "READY"]
    all_products = {r["product_sha256"] for r in ready if r["product_sha256"]}
    with_text = sum(row["ocr_text_labels"] for row in ready)
    can_score = bool(ready and with_text and len(ready) == len(labeled)) and not any(
        row["status"] in {"SOURCE_INVALID", "REVIEW_STALE"} for row in rows
    )
    summary = {
        "schema_version": "1.0", "variant_id": "feed_4x5_native",
        "reviewer": reviewer, "source": "local_poster_versions",
        "model_invoked": False, "ocr_inference_performed": False,
        "total_versions": len(rows),
        "reviewed_versions_with_definite_labels": len(labeled),
        "ready_versions": len(ready),
        "ready_distinct_product_sha256_count": len(all_products),
        "minimum_distinct_products": 20,
        "meets_suggested_minimum": len(all_products) >= 20,
        "scorable_text_labels": with_text,
        "status_counts": dict(sorted(counts.items())),
        "can_score": bool(can_score),
        "warning": (
            "Readiness only; product digests approximate distinct source images, "
            "not independent product categories. Mocked OCR can produce OK reports "
            "in tests; no proof of live model or professional review accuracy."
        ),
    }
    return summary, rows


def save_trial_audit(output_dir: Path, summary: dict, rows: list[dict]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "native_trial_readiness.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    fields = [
        "poster_id", "version", "status", "definite_labels",
        "ocr_text_labels", "unsure_labels", "product_sha256",
        "native_image_sha256", "action",
    ]
    with (output_dir / "native_trial_manifest.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run_trial(reviewer: str, output_dir: Path, *, score: bool = False) -> dict:
    """Audit by default. --score exclusively reuses the existing M5d scorer."""
    summary, rows = audit_trial(reviewer)
    save_trial_audit(output_dir, summary, rows)
    if not score:
        return summary
    if not summary["can_score"]:
        raise ValueError(
            "存在未完成/过期的人工或 OCR 证据，或没有可评测文字标签；"
            "已输出 native_trial_manifest.csv，请先处理缺口"
        )

    # Export labels through the existing M5c contract, never construct new
    # ground truth from a PosterSpec or from OCR prediction.
    import tempfile
    from evals.native_real_reports import evaluate_native_reports

    csv_content, _meta = reviews.export(reviewer)
    with tempfile.TemporaryDirectory(prefix="native-trial-labels-") as tmp:
        labels_path = Path(tmp) / "labels.csv"
        labels_path.write_text(csv_content, encoding="utf-8")
        result = evaluate_native_reports(labels_path, output_dir)

    # Do not mix this content digest of transient UTF-8 CSV with the BOM-prefixed
    # download digest shown by a browser; the scored bytes are specified above.
    result["trial_readiness_file"] = "native_trial_readiness.json"
    result["ready_distinct_product_sha256_count"] = summary["ready_distinct_product_sha256_count"]
    return result
