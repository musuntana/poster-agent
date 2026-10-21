"""Replay M3b on ACTUAL code-rendered PNG pixels, optionally calling PaddleOCR.

When --ocr none is selected, the report is fully abstained. When paddle is
selected, a missing model is an explicit error instead of fabricated PASS.

Synthetic image ground truth is generated from authored rendered-spec changes;
it is not equivalent to manually labeled product/user images.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Literal

from app.acceptance import _field_checks
from app.ocr_provider import OCRExecutionError, OCRUnavailable, recognize
from evals.benchmark import _classification, metrics
from evals.image_fixtures import ImageManifest


def evaluate_image_manifest(
    manifest_file: Path, output_dir: Path,
    ocr_mode: Literal["none", "paddle"] = "none",
) -> dict:
    if ocr_mode not in {"none", "paddle"}:
        raise ValueError("ocr_mode must be none or paddle")

    raw_manifest = manifest_file.read_bytes()
    manifest = ImageManifest.model_validate_json(raw_manifest)
    rows: list[dict] = []
    for fixture in manifest.fixtures:
        image_file = manifest_file.parent / fixture.image_file
        png = image_file.read_bytes()  # missing file is a hard failure
        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"{fixture.case_id}: expected an actual PNG file")
        checksum = hashlib.sha256(png).hexdigest()
        if checksum != fixture.image_sha256:
            raise ValueError(f"{fixture.case_id}: PNG SHA-256 mismatch; fixture may have changed")
        if ocr_mode == "paddle":
            # Must be REAL engine when this mode is requested. No fallback to
            # synthetic OCR lines, no silent substitution of expected labels.
            try:
                ocr_lines = recognize(image_file)
            except (OCRUnavailable, OCRExecutionError) as exc:
                raise RuntimeError(
                    "PaddleOCR 推理不可用或执行失败；未生成任何真实 OCR 指标。请检查运行环境。"
                ) from exc
            ocr_status = "OK"
        else:
            ocr_lines = []
            ocr_status = "NOT_RUN"

        checks = {c.name.removeprefix("ocr_"): c
                  for c in _field_checks(manifest.expected_spec, ocr_status, ocr_lines)}
        case_predictions: list[str] = []
        for field in manifest.target_fields:
            if field not in checks:
                raise ValueError(f"{fixture.case_id}: no OCR check for expected field {field}")
            status = checks[field].status
            case_predictions.append(status)
            truth = field in fixture.truth_defects
            rows.append({
                "case_id": fixture.case_id,
                "category": fixture.category,
                "field": field,
                "ground_truth_defective": truth,
                "prediction": status,
                "outcome": _classification(status, truth),
                "image_sha256": checksum,
            })
        case_status = ("FAIL" if "FAIL" in case_predictions else
                       "UNVERIFIED" if "UNVERIFIED" in case_predictions else "PASS")
        rows.append({
            "case_id": fixture.case_id,
            "category": fixture.category,
            "field": "__case__",
            "ground_truth_defective": bool(fixture.truth_defects),
            "prediction": case_status,
            "outcome": _classification(case_status, bool(fixture.truth_defects)),
            "image_sha256": checksum,
        })

    case_rows = [row for row in rows if row["field"] == "__case__"]
    field_rows = [row for row in rows if row["field"] != "__case__"]
    grouped: dict[str, list[str]] = defaultdict(list)
    for row in case_rows:
        grouped[row["category"]].append(row["outcome"])

    report = {
        "dataset_id": manifest.dataset_id,
        "dataset_kind": manifest.dataset_kind,
        "warning": ("PNG images and defects are PROGRAMMATIC test fixtures, not "
                    "real customer images/human labels; PaddleOCR only ran if "
                    "ocr_mode=paddle and this evaluation returned successfully."),
        "ocr_mode": ocr_mode,
        "model_executed": ocr_mode == "paddle",
        "fixture_count": len(manifest.fixtures),
        "manifest_sha256": hashlib.sha256(raw_manifest).hexdigest(),
        "case_metrics": metrics([row["outcome"] for row in case_rows]),
        "field_metrics": metrics([row["outcome"] for row in field_rows]),
        "by_category": {k: metrics(v) for k, v in sorted(grouped.items())},
        "errors": [row for row in rows if row["outcome"] in ("FP", "FN", "ABSTAIN_DEFECT")],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "image_benchmark_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (output_dir / "image_benchmark_cases.csv").open(
        "w", newline="", encoding="utf-8"
    ) as out:
        writer = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return report
