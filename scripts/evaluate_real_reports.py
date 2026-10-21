"""Evaluate real saved poster reports against independently collected CSV labels.

Run this only after manually viewing each exported PNG and recording its hash.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from evals.real_reports import evaluate_real_reports


def main():
    parser = argparse.ArgumentParser(description="Evaluate saved Acceptance Gate reports using manual labels")
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("data/eval_runs/m3b_real"))
    parser.add_argument("--ocr-mode", choices=["none", "paddle"], default="paddle")
    args = parser.parse_args()
    summary = evaluate_real_reports(args.annotations, args.output_dir, args.ocr_mode)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("Labels are supplied by the annotator. Metrics are NOT independently audited.")


if __name__ == "__main__":
    main()
