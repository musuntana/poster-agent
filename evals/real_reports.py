"""Evaluate actual saved Acceptance Gate reports against user-supplied manual labels.

This loader NEVER generates labels or assumes a PNG's text is correct because
the design spec says so. The user must inspect actual exported posters and
provide annotations separately. Stale reports are rejected by SHA-256.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from app import storage
from evals.benchmark import _classification, metrics

_FIELDS = {"title", "price", "cta", "subtitle", "original_price"}
_REQUIRED = {"poster_id", "version", "field", "defective", "image_sha256", "reviewer"}
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def load_labels(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not _REQUIRED.issubset(set(reader.fieldnames)):
            raise ValueError(f"标注 CSV 缺少必填列: {sorted(_REQUIRED)}")
        records = []
        seen = set()
        for i, item in enumerate(reader, start=2):
            try:
                poster_id = item["poster_id"].strip()
                version = int(item["version"])
                field = item["field"].strip()
                answer = item["defective"].strip().lower()
                sha = item["image_sha256"].strip().lower()
                reviewer = item["reviewer"].strip()
                if version < 1 or field not in _FIELDS or answer not in {"true", "false"}:
                    raise ValueError("invalid value")
                if not _DIGEST.fullmatch(sha) or not reviewer:
                    raise ValueError("missing hash or reviewer")
                identity = (poster_id, version, field)
                if identity in seen:
                    raise ValueError("重复的同海报版本字段")
                seen.add(identity)
                records.append({**dict(zip(_REQUIRED, [None]*len(_REQUIRED))), **{
                    "poster_id": poster_id, "version": version, "field": field,
                    "defective": answer == "true", "image_sha256": sha, "reviewer": reviewer,
                }})
            except (ValueError, AttributeError, TypeError) as exc:
                raise ValueError(f"标注 CSV 第 {i} 行无效，请修正数据") from exc
        if not records:
            raise ValueError("标注 CSV 为空，不能生成评测结论")
        return records


def evaluate_real_reports(annotations: Path, output_dir: Path, ocr_mode: str = "paddle") -> dict:
    if ocr_mode not in {"paddle", "none"}:
        raise ValueError("ocr_mode 必须是 paddle 或 none")
    records = load_labels(annotations)
    rows = []
    for record in records:
        poster_id, version, field = record["poster_id"], record["version"], record["field"]
        image = storage.version_path(poster_id, version)
        spec = storage.poster_dir(poster_id) / "versions" / f"v{version}.json"
        image_digest = hashlib.sha256(image.read_bytes()).hexdigest()
        spec_digest = hashlib.sha256(spec.read_bytes()).hexdigest()
        if record["image_sha256"] != image_digest:
            raise ValueError(f"{poster_id} v{version}: 人工标注与当前图片 SHA-256 不一致")
        report_file = storage.poster_dir(poster_id) / "versions" / f"v{version}.acceptance.{ocr_mode}.json"
        if not report_file.is_file():
            raise ValueError(f"{poster_id} v{version}: 还没有 {ocr_mode} 模式的验收报告")
        report = json.loads(report_file.read_text(encoding="utf-8"))
        if report.get("image_sha256") != image_digest or report.get("spec_sha256") != spec_digest:
            raise ValueError(f"{poster_id} v{version}: 验收报告已过期，请重新验收")
        if report.get("ocr_mode") != ocr_mode:
            raise ValueError("报告模式不一致")
        checks = {c["name"]: c["status"] for c in report.get("checks", [])}
        predicted = checks.get("ocr_" + field)
        if predicted not in {"PASS", "FAIL", "UNVERIFIED"}:
            raise ValueError(f"{poster_id} v{version}: 缺少必要的字段 OCR 检查 {field}")
        rows.append({
            "poster_id": poster_id, "version": version, "field": field,
            "reviewer": record["reviewer"], "ground_truth_defective": record["defective"],
            "prediction": predicted,
            "outcome": _classification(predicted, record["defective"]),
            "image_sha256": image_digest,
        })
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["field"]].append(row["outcome"])
    summary = {
        "schema_version": "1.0",
        "source": "manual_annotation_csv_against_saved_acceptance_reports",
        "warning": "Labels are user-supplied and NOT independently verified. This is field-level scoring, not a whole-poster business pass rate.",
        "annotation_sha256": hashlib.sha256(annotations.read_bytes()).hexdigest(),
        "ocr_mode": ocr_mode,
        "field_labels": len(rows),
        "unique_poster_versions": len({(r["poster_id"], r["version"]) for r in rows}),
        "metrics": metrics([r["outcome"] for r in rows]),
        "by_field": {key: metrics(values) for key, values in sorted(grouped.items())},
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "real_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (output_dir / "real_cases.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return summary
