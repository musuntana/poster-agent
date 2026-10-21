"""M5d: score native 4:5 saved real-OCR reports vs human-reviewed PNGs.

No model is invoked. Positive class = a human-reported defective field.
An OCR position check is required for price fields, and UNVERIFIED remains
abstention. Visual-only human reviews are descriptive, NOT OCR predictions.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from app import storage
from app.native_acceptance import VARIANT, StaleNativeEvidence, verified_report
from app.native_human_reviews import VISUAL_FIELDS, CORE_FIELDS, OPTIONAL_FIELDS
from app.human_annotations import REVIEWER_PATTERN, VALID_ID
from evals.benchmark import _classification, metrics

OCR_FIELDS = frozenset((*CORE_FIELDS, *OPTIONAL_FIELDS))
VISUAL = frozenset(VISUAL_FIELDS)
REQUIRED = frozenset({
    "poster_id", "version", "variant_id", "field", "defective",
    "image_sha256", "source_image_sha256", "reviewer",
})
DIGEST = re.compile(r"^[a-f0-9]{64}$")


def read_native_labels(path: Path) -> list[dict]:
    """Parse only M5c-native labels: never reinterpret square CSV as native."""
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not REQUIRED.issubset(reader.fieldnames):
            raise ValueError("原生 4:5 CSV 缺少必要列（必须包含 variant_id/source_image_sha256）")
        records, seen, reviewers = [], set(), set()
        for line_number, row in enumerate(reader, start=2):
            try:
                poster_id = row["poster_id"].strip()
                version = int(row["version"])
                variant = row["variant_id"].strip()
                field = row["field"].strip()
                defective = row["defective"].strip().lower()
                image_sha = row["image_sha256"].strip().lower()
                source_sha = row["source_image_sha256"].strip().lower()
                reviewer = row["reviewer"].strip()
                if not VALID_ID.fullmatch(poster_id) or version < 1:
                    raise ValueError("invalid poster/version")
                if variant != VARIANT or field not in OCR_FIELDS | VISUAL:
                    raise ValueError("wrong variant or field")
                if defective not in {"true", "false"}:
                    raise ValueError("invalid human label")
                if not DIGEST.fullmatch(image_sha) or not DIGEST.fullmatch(source_sha):
                    raise ValueError("invalid digest")
                if not re.fullmatch(REVIEWER_PATTERN, reviewer):
                    raise ValueError("invalid reviewer")
                identity = poster_id, version, field
                if identity in seen:
                    raise ValueError("duplicate labeled field")
                seen.add(identity)
                reviewers.add(reviewer)
                records.append({
                    "poster_id": poster_id, "version": version, "variant_id": variant,
                    "field": field, "defective": defective == "true",
                    "image_sha256": image_sha, "source_image_sha256": source_sha,
                    "reviewer": reviewer,
                })
            except (ValueError, TypeError, AttributeError, KeyError) as exc:
                raise ValueError(f"原生 4:5 CSV 第 {line_number} 行无效") from exc
    if not records:
        raise ValueError("没有可评测的人工标签")
    if len(reviewers) != 1:
        raise ValueError("一次只允许评测一位人工审核员；不能把意见合并成共识")
    return records


def _text_prediction(field: str, checks: dict[str, str]) -> str:
    names = ["ocr_" + field]
    if field in {"price", "original_price"}:
        names.append("ocr_" + field + "_position")
    status = [checks.get(name) for name in names]
    if any(item not in {"PASS", "FAIL", "UNVERIFIED"} for item in status):
        raise ValueError(f"原生验收报告缺少字段或价格位置证据：{field}")
    if "FAIL" in status:
        return "FAIL"
    if all(item == "PASS" for item in status):
        return "PASS"
    return "UNVERIFIED"


def evaluate_native_reports(annotations: Path, output_dir: Path) -> dict:
    records = read_native_labels(annotations)
    # Validate and collect EVERYTHING before creating any result artifacts.
    # A single missing/failed OCR run or stale hash aborts the whole evaluation.
    report_cache = {}
    rows: list[dict] = []
    visual_counts = defaultdict(Counter)
    for record in records:
        pid, version, field = record["poster_id"], record["version"], record["field"]
        key = pid, version
        if key not in report_cache:
            try:
                report = verified_report(pid, version, "paddle")
            except (FileNotFoundError, StaleNativeEvidence, ValueError, OSError) as exc:
                raise ValueError(f"{pid} v{version}: 缺少有效且未过期的原生 PaddleOCR 验收报告") from exc
            if report.get("ocr_status") != "OK" or report.get("ocr_mode") != "paddle":
                raise ValueError(f"{pid} v{version}: OCR 未成功执行，拒绝生成准确率指标")
            if report.get("variant_id") != VARIANT or report.get("image_dimensions") != [1080, 1350]:
                raise ValueError(f"{pid} v{version}: 不是原生 1080×1350 的验收报告")
            checks_list = report.get("checks")
            if not isinstance(checks_list, list):
                raise ValueError("验收报告没有合法的 checks")
            check_status = {}
            for item in checks_list:
                name, status = item.get("name"), item.get("status")
                if name in check_status:
                    raise ValueError(f"{pid} v{version}: 重复 OCR 检查 {name}")
                check_status[name] = status
            report_cache[key] = (report, check_status)
        report, checks = report_cache[key]
        if (record["image_sha256"] != report.get("native_image_sha256")
                or record["source_image_sha256"] != report.get("source_image_sha256")):
            raise ValueError(f"{pid} v{version}: 人工标注与原生导出/原方图摘要不一致")
        if field in VISUAL:
            visual_counts[field]["defective" if record["defective"] else "correct"] += 1
            # Do not invent an automated SKU/contrast/composition classifier.
            continue

        predicted = _text_prediction(field, checks)
        rows.append({
            "poster_id": pid, "version": version, "variant_id": VARIANT,
            "field": field, "reviewer": record["reviewer"],
            "ground_truth_defective": record["defective"],
            "prediction": predicted,
            "outcome": _classification(predicted, record["defective"]),
            "image_sha256": record["image_sha256"],
            "source_image_sha256": record["source_image_sha256"],
        })

    if not rows:
        raise ValueError("缺少可评测的 OCR 文本字段标签；视觉字段不能冒充 OCR 指标")
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["field"]].append(row["outcome"])

    all_metrics = metrics([row["outcome"] for row in rows])
    output = {
        "schema_version": "1.0",
        "dataset_kind": "native_4x5_human_labeled_poster_versions",
        "variant_id": VARIANT,
        "ocr_mode": "paddle",
        "ocr_engine_execution": "saved_report_only",
        "warning": (
            "User-provided human judgments are not independently audited. "
            "These are per-field OCR-detection metrics on assessed labels; "
            "visual-only fields are NOT model predictions. "
            "Do not treat simulated OCR reports as real inference evidence."
        ),
        "label_csv_sha256": hashlib.sha256(annotations.read_bytes()).hexdigest(),
        "reviewer": records[0]["reviewer"],
        "poster_versions": len(report_cache),
        "human_labels_total": len(records),
        "ocr_text_labels": len(rows),
        "visual_labels": sum(sum(c.values()) for c in visual_counts.values()),
        "metrics": all_metrics,
        "by_field": {name: metrics(values) for name, values in sorted(grouped.items())},
        "visual_human_only": {
            name: {"correct": count["correct"], "defective": count["defective"]}
            for name, count in sorted(visual_counts.items())
        },
        "source_report_sha256": {
            f"{pid}:v{version}": hashlib.sha256(
                (storage.poster_dir(pid) / "versions"
                 / f"v{version}.{VARIANT}.acceptance.paddle.json").read_bytes()
            ).hexdigest()
            for pid, version in sorted(report_cache)
        },
        "small_sample_warning": len(report_cache) < 20,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "native_real_summary.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (output_dir / "native_real_cases.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return output
