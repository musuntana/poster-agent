"""Replay M3b fixture against the ACTUAL M3a OCR field-check implementation.

Usage:
    python -m scripts.evaluate_acceptance
    python -m scripts.evaluate_acceptance --dataset evals/datasets/m3b_synthetic_ocr_v1.json --output-dir data/eval_runs/m3b
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from evals.benchmark import evaluate_file


def main():
    parser = argparse.ArgumentParser(description="Replay OCR acceptance benchmark (synthetic fixture)")
    parser.add_argument("--dataset", type=Path, default=Path("evals/datasets/m3b_synthetic_ocr_v1.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/eval_runs/m3b"))
    args = parser.parse_args()
    report = evaluate_file(args.dataset, args.output_dir)
    print(json.dumps({
        "dataset": report["dataset_id"],
        "dataset_kind": report["dataset_kind"],
        "cases": report["case_count"],
        "case_metrics": report["case_metrics"],
        "field_metrics": report["field_metrics"],
        "output_dir": str(args.output_dir),
    }, indent=2, ensure_ascii=False))
    print("NOTE: these metrics are from hand-authored synthetic transcripts, NOT actual PaddleOCR model accuracy.")


if __name__ == "__main__":
    main()
