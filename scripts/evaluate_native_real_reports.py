"""Offline score of actual saved native 4:5 OCR results against operator CSV.

No image generation, OCR inference, or external model call occurs in this
command. A missing real native PaddleOCR report is a hard error.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from evals.native_real_reports import evaluate_native_reports


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate native 4:5 OCR with human labels")
    parser.add_argument("--annotations", type=Path, required=True,
                        help="CSV exported from /api/annotations/native-4x5/export.csv")
    parser.add_argument("--output-dir", type=Path,
                        default=Path("data/eval_runs/m5d_native_real"))
    args = parser.parse_args()
    result = evaluate_native_reports(args.annotations, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
