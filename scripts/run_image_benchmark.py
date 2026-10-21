"""Generate/replay 20 rendered PNG image fixtures with optional real PaddleOCR.

  python -m scripts.run_image_benchmark
  python -m scripts.run_image_benchmark --ocr paddle
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from evals.image_fixtures import generate_fixture_set
from evals.image_runner import evaluate_image_manifest


def main():
    parser = argparse.ArgumentParser(description="Render PNG fixtures and optionally run real PaddleOCR")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="distinct output dirs used by default for real OCR vs offline mode")
    parser.add_argument("--ocr", choices=["none", "paddle"], default="none")
    args = parser.parse_args()
    output_dir = args.output_dir or (
        Path("data/eval_runs/m3b_images_paddle")
        if args.ocr == "paddle" else Path("data/eval_runs/m3b_images")
    )
    manifest = generate_fixture_set(output_dir / "images")
    report = evaluate_image_manifest(manifest, output_dir, args.ocr)
    print(json.dumps({
        "dataset_id": report["dataset_id"],
        "dataset_kind": report["dataset_kind"],
        "rendered_png_files": report["fixture_count"],
        "ocr_requested": report["ocr_mode"],
        "ocr_actually_executed": report["model_executed"],
        "case_metrics": report["case_metrics"],
        "field_metrics": report["field_metrics"],
        "output_dir": str(output_dir),
    }, ensure_ascii=False, indent=2))
    if not report["model_executed"]:
        print("NOTICE: all OCR statuses are UNVERIFIED. This run did NOT test OCR accuracy.")
    else:
        print("NOTICE: OCR ran on PROGRAMMATIC PNGs, not on real user images.")


if __name__ == "__main__":
    main()
