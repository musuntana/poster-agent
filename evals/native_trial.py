"""M5e: local, read-only native 4:5 evaluation cohort readiness.

Reuses M5c's actual reviewer context and M4c's verified report. This module
does NOT run OCR, generate images with AI, or fabricate an accuracy metric.
"""
from __future__ import annotations

import csv
import json
import os
import re
from datetime import datetime, timezone
from tempfile import TemporaryDirectory
from uuid import uuid4
from collections import Counter
from pathlib import Path

from app import native_human_reviews as reviews
from app import storage
from app.human_annotations import REVIEWER_PATTERN, VALID_ID
from app.native_acceptance import StaleNativeEvidence, verified_report
from evals.native_real_reports import OCR_FIELDS, _text_prediction


def audit_trial(reviewer: str, *, batch_scope: dict | None = None) -> tuple[dict, list[dict]]:
    if not re.fullmatch(REVIEWER_PATTERN, reviewer):
        raise ValueError("审核员代号只能使用 2–40 位字母、数字、下划线和短横线")
    rows: list[dict] = []
    chosen = batch_scope["sku_by_id"] if batch_scope is not None else None
    recorded = set()
    unrecorded = set()
    with storage.LOCK:
        for poster in sorted(storage.root().glob("*")):
            if not poster.is_dir() or not VALID_ID.fullmatch(poster.name):
                continue
            if chosen is not None and poster.name not in chosen:
                continue
            # One latest version per imported SKU; don't double-count revisions.
            latest = None
            if chosen is not None:
                try:
                    latest = storage.view(poster.name)["version"]
                except (OSError, ValueError, KeyError, TypeError, FileNotFoundError):
                    continue
            version_dir = poster / "versions"
            if not version_dir.is_dir():
                continue
            for source in sorted(version_dir.glob("v[0-9]*.png")):
                try:
                    version = int(source.stem[1:])
                except ValueError:
                    continue
                if chosen is not None and version != latest:
                    continue
                row = {
                    "poster_id": poster.name, "version": version,
                    "sku_id": chosen[poster.name] if chosen is not None else "",
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
                                        identifier = (poster.name, version)
                                        if report.get("ocr_runtime_evidence") == "RECORDED" and isinstance(
                                            report.get("ocr_runtime"), dict
                                        ):
                                            recorded.add(identifier)
                                        else:
                                            unrecorded.add(identifier)
                except (reviews.NativeReviewConflict, FileNotFoundError, OSError, ValueError, KeyError, TypeError) as exc:
                    # This path is a diagnostic, not evidence of valid inputs.
                    row["status"] = (
                        "REVIEW_STALE" if isinstance(exc, reviews.NativeReviewConflict)
                        else "SOURCE_INVALID"
                    )
                    row["action"] = "检查图片、规格与最新人工审核的哈希或 JSON 完整性"
                rows.append(row)

    if chosen is not None:
        present = {row["poster_id"] for row in rows}
        for pid in sorted(chosen.keys() - present):
            rows.append({
                "poster_id": pid, "version": 0, "sku_id": chosen[pid],
                "status": "SOURCE_INVALID", "definite_labels": 0,
                "ocr_text_labels": 0, "unsure_labels": 0,
                "product_sha256": "", "native_image_sha256": "",
                "action": "批次海报不存在、版本文件缺失或版本元数据不可用",
            })
        rows.sort(key=lambda item: (item["sku_id"], item["version"]))

    counts = Counter(row["status"] for row in rows)
    labeled = [row for row in rows if row["definite_labels"] > 0]
    ready = [row for row in labeled if row["status"] == "READY"]
    all_products = {r["product_sha256"] for r in ready if r["product_sha256"]}
    with_text = sum(row["ocr_text_labels"] for row in ready)
    can_score = bool(ready and with_text and len(ready) == len(labeled)) and not any(
        row["status"] in {"SOURCE_INVALID", "REVIEW_STALE"} for row in rows
    )
    if chosen is not None:
        can_score = can_score and len(rows) == len(chosen) and all(
            row["status"] == "READY" for row in rows
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
        "ocr_adapter_runtime_recorded_versions": len(recorded),
        "ocr_adapter_runtime_unrecorded_versions": len(unrecorded),
        "ocr_runtime_attested": False,
        "status_counts": dict(sorted(counts.items())),
        "can_score": bool(can_score),
        "warning": (
            "Readiness only; product digests approximate distinct source images, "
            "not independent product categories. Runtime metadata is diagnostic only, "
            "not cryptographically attested; mocked OCR may produce OK reports. "
            "No independent proof of model inference or review accuracy."
        ),
    }
    if batch_scope is not None:
        summary.update({
            "scope_mode": batch_scope["scope_mode"],
            "source_batch_journal_sha256": batch_scope["source_batch_journal_sha256"],
            "source_manifest_sha256": batch_scope["source_manifest_sha256"],
            "batch_sku_count": batch_scope["batch_sku_count"],
            "batch_unique_raw_product_sha256_count": batch_scope[
                "batch_unique_raw_product_sha256_count"
            ],
            "cohort_requires_all_skus_ready": True,
            "warning": summary["warning"] +
                " The local M5g journal is not a signed image-origin certificate.",
        })
    return summary, rows


def save_trial_audit(output_dir: Path, summary: dict, rows: list[dict]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "native_trial_readiness.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    fields = [
        "poster_id", "version", "sku_id", "status", "definite_labels",
        "ocr_text_labels", "unsure_labels", "product_sha256",
        "native_image_sha256", "action",
    ]
    with (output_dir / "native_trial_manifest.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


SCORE_FILES = ("native_real_summary.json", "native_real_cases.csv", "native_trial_labels.csv")


def _archive_previous_scores(output_dir: Path) -> str | None:
    """Preserve scored artifacts from an earlier run, never present as latest."""
    existing = [output_dir / name for name in SCORE_FILES
                if (output_dir / name).is_file()]
    if not existing:
        return None
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:12]
    directory = output_dir / "history" / name
    directory.mkdir(parents=True, exist_ok=False)
    for path in existing:
        os.replace(path, directory / path.name)
    return f"history/{name}"


def run_trial(
    reviewer: str, output_dir: Path, *, score: bool = False,
    batch_scope: dict | None = None,
) -> dict:
    """Readiness is default; --score stages results and exposes them only on success.

    Earlier results move to a versioned history folder, never silently remain
    at the root after a newer audit discovers missing or stale evidence.
    """
    summary, rows = audit_trial(reviewer, batch_scope=batch_scope)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary["trial_run_id"] = uuid4().hex
    summary["evaluated_at_utc"] = datetime.now(timezone.utc).isoformat()
    summary["previous_score_archive"] = _archive_previous_scores(output_dir)
    summary["latest_score_status"] = "AUDIT_ONLY" if not score else "BLOCKED"
    save_trial_audit(output_dir, summary, rows)
    if not score:
        return summary
    if not summary["can_score"]:
        raise ValueError(
            "存在未完成/过期的人工或 OCR 证据，或没有可评测文字标签；"
            "已输出 native_trial_manifest.csv，请先处理缺口"
        )

    # Strictly reuse the existing M5c exporter and M5d evaluator. The temporary
    # score directory prevents partly written artifacts from appearing current.
    from evals.native_real_reports import evaluate_native_reports

    try:
        target_versions = ({row["poster_id"]: row["version"] for row in rows}
                           if batch_scope is not None else None)
        csv_content, _meta = reviews.export(
            reviewer, target_versions=target_versions
        )
        with TemporaryDirectory(prefix=".score-staging-", dir=output_dir) as tmp:
            staged = Path(tmp)
            labels_path = staged / "native_trial_labels.csv"
            labels_path.write_text(csv_content, encoding="utf-8")
            result = evaluate_native_reports(labels_path, staged)
            result["trial_readiness_file"] = "native_trial_readiness.json"
            result["ready_distinct_product_sha256_count"] = summary[
                "ready_distinct_product_sha256_count"
            ]
            if batch_scope is not None:
                result.update({
                    "scope_mode": batch_scope["scope_mode"],
                    "source_batch_journal_sha256": batch_scope["source_batch_journal_sha256"],
                    "source_manifest_sha256": batch_scope["source_manifest_sha256"],
                    "batch_sku_count": batch_scope["batch_sku_count"],
                    "batch_unique_raw_product_sha256_count": batch_scope[
                        "batch_unique_raw_product_sha256_count"
                    ],
                    "scored_sku_to_poster_id": {
                        row["sku_id"]: row["poster_id"] for row in rows
                    },
                    "cohort_requires_all_skus_ready": True,
                })
                (staged / "native_real_summary.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            for name in SCORE_FILES:
                os.replace(staged / name, output_dir / name)
    except Exception:
        for name in SCORE_FILES:
            (output_dir / name).unlink(missing_ok=True)
        summary["latest_score_status"] = "FAILED"
        save_trial_audit(output_dir, summary, rows)
        raise

    summary["latest_score_status"] = "SCORED"
    save_trial_audit(output_dir, summary, rows)
    return result
