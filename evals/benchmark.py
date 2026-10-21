"""Reproducible OCR acceptance benchmark for M3b.

The initial fixture is *synthetic*: hand-authored OCR transcripts and
hypothetical visible text. This measures evaluator behavior, NOT OCR model
accuracy, visual quality, or production pass rates.

Positive class = defective visible field.  Detector positive = FAIL.
UNVERIFIED is an abstention, never silently treated as PASS.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.acceptance import _field_checks
from app.schemas import PosterSpec


class OCRLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    score: float | None = Field(default=None, ge=0, le=1)


class BenchmarkCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=2)
    scenario: str
    category: str
    visible_text: list[str]
    ocr_status: Literal["OK", "NOT_RUN", "UNAVAILABLE", "ERROR"] = "OK"
    ocr_lines: list[str | OCRLine]
    truth_defects: list[Literal["title", "price", "cta", "subtitle", "original_price"]]

    @model_validator(mode="after")
    def no_duplicate_truth(self):
        if len(set(self.truth_defects)) != len(self.truth_defects):
            raise ValueError("duplicate ground truth fields")
        return self


class BenchmarkDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: str
    kind: Literal["synthetic_contract_tests", "human_annotated_real_images"]
    description: str
    base_spec: PosterSpec
    target_fields: list[Literal["title", "price", "cta", "subtitle", "original_price"]]
    label_definition: str
    cases: list[BenchmarkCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_contract(self):
        if len(set(self.target_fields)) != len(self.target_fields):
            raise ValueError("target_fields duplicated")
        if len(set(case.id for case in self.cases)) != len(self.cases):
            raise ValueError("case ids duplicated")
        for case in self.cases:
            if set(case.truth_defects) - set(self.target_fields):
                raise ValueError(f"{case.id}: ground truth outside target_fields")
        if self.kind == "human_annotated_real_images":
            raise ValueError("Real-image benchmark requires a separate image/annotation loader; not supported here")
        return self


def _make_lines(case: BenchmarkCase) -> list[dict]:
    # The visible_text ground-truth annotation is deliberately NEVER passed to
    # the evaluator; only OCR transcripts are passed.
    return [
        {"text": line, "score": 1.0} if isinstance(line, str)
        else line.model_dump()
        for line in case.ocr_lines
    ]


def _classification(prediction: str, defective: bool) -> str:
    if prediction == "UNVERIFIED":
        return "ABSTAIN_DEFECT" if defective else "ABSTAIN_GOOD"
    if prediction == "FAIL":
        return "TP" if defective else "FP"
    if prediction == "PASS":
        return "FN" if defective else "TN"
    raise ValueError("Unknown status " + prediction)


def _safe_ratio(a: int, b: int) -> float | None:
    return round(a / b, 6) if b else None


def metrics(outcomes: list[str]) -> dict:
    c = Counter(outcomes)
    tp, fp, fn, tn = (c[key] for key in ("TP", "FP", "FN", "TN"))
    abstain_good, abstain_defect = c["ABSTAIN_GOOD"], c["ABSTAIN_DEFECT"]
    assessed = tp + fp + fn + tn
    positives = tp + fn + abstain_defect
    precision = _safe_ratio(tp, tp + fp)
    recall = _safe_ratio(tp, tp + fn)
    f1 = _safe_ratio(2 * tp, 2 * tp + fp + fn)
    return {
        "total": assessed + abstain_good + abstain_defect,
        "assessed": assessed,
        "abstained": abstain_good + abstain_defect,
        "confusion": {
            "TP": tp, "FP": fp, "FN": fn, "TN": tn,
            "ABSTAIN_GOOD": abstain_good, "ABSTAIN_DEFECT": abstain_defect,
        },
        "coverage": _safe_ratio(assessed, assessed + abstain_good + abstain_defect),
        "precision_on_assessed": precision,
        "recall_on_assessed": recall,
        "f1_on_assessed": f1,
        "recall_including_abstentions": _safe_ratio(tp, positives),
        "false_alarm_rate_on_good_assessed": _safe_ratio(fp, fp + tn),
    }


def run_dataset(dataset: BenchmarkDataset) -> tuple[dict, list[dict]]:
    rows: list[dict] = []
    for case in dataset.cases:
        checks = {
            c.name[4:]: c
            for c in _field_checks(dataset.base_spec, case.ocr_status, _make_lines(case))
            if c.name.startswith("ocr_")
        }
        case_predictions: list[str] = []
        for field in dataset.target_fields:
            if field not in checks:
                raise ValueError(f"{case.id}: missing evaluator output: {field}")
            result = checks[field]
            predicted = result.status
            defective = field in case.truth_defects
            case_predictions.append(predicted)
            rows.append({
                "case_id": case.id, "category": case.category, "scenario": case.scenario,
                "field": field, "truth_defective": defective, "prediction": predicted,
                "outcome": _classification(predicted, defective),
                "ocr_status": case.ocr_status,
                "notes": result.detail,
            })
        verdict = (
            "FAIL" if "FAIL" in case_predictions else
            "UNVERIFIED" if "UNVERIFIED" in case_predictions else "PASS"
        )
        rows.append({
            "case_id": case.id, "category": case.category, "scenario": case.scenario,
            "field": "__case__", "truth_defective": bool(case.truth_defects),
            "prediction": verdict,
            "outcome": _classification(verdict, bool(case.truth_defects)),
            "ocr_status": case.ocr_status,
            "notes": "case-level aggregate across target text fields",
        })
    fields = [r for r in rows if r["field"] != "__case__"]
    cases = [r for r in rows if r["field"] == "__case__"]
    report = {
        "schema_version": "1.0",
        "dataset_id": dataset.dataset_id,
        "dataset_kind": dataset.kind,
        "warning": "SYNTHETIC CONTRACT TEST ONLY; NOT an OCR accuracy measurement or real customer evaluation",
        "label_definition": dataset.label_definition,
        "case_count": len(dataset.cases),
        "target_fields": dataset.target_fields,
        "case_metrics": metrics([r["outcome"] for r in cases]),
        "field_metrics": metrics([r["outcome"] for r in fields]),
        "by_category": {
            name: metrics([r["outcome"] for r in group])
            for name, group in sorted(_group_cases(cases).items())
        },
        "errors": [
            {
                "case_id": r["case_id"],
                "category": r["category"],
                "field": r["field"],
                "outcome": r["outcome"],
                "scenario": r["scenario"],
            }
            for r in rows if r["outcome"] in ("FP", "FN", "ABSTAIN_DEFECT")
        ],
    }
    return report, rows


def _group_cases(rows: list[dict]) -> dict[str, list[dict]]:
    groups = defaultdict(list)
    for row in rows:
        groups[row["category"]].append(row)
    return groups


def evaluate_file(source: Path, output_dir: Path) -> dict:
    raw = source.read_bytes()
    dataset = BenchmarkDataset.model_validate_json(raw)
    report, rows = run_dataset(dataset)
    report["input_sha256"] = hashlib.sha256(raw).hexdigest()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "benchmark_summary.json"
    details_path = output_dir / "benchmark_cases.csv"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with details_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return report
