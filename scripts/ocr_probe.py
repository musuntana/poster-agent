"""Opt-in real PaddleOCR smoke test; never equate diagnose with inference.

# Diagnostic only (does NOT load model):
python -m scripts.ocr_probe

# Actual local OCR on one previously generated PNG:
python -m scripts.generate_demo
python -m scripts.ocr_probe --infer --image data/examples/sample_preview.png

# Save raw text only when explicitly requested:
python -m scripts.ocr_probe --infer --image data/examples/sample_preview.png --include-text
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from evals.ocr_runtime import probe, save_report


def main() -> int:
    parser = argparse.ArgumentParser(description="PaddleOCR environment doctor and real one-image inference")
    parser.add_argument("--image", type=Path, help="existing PNG/JPEG/WEBP to inspect")
    parser.add_argument("--infer", action="store_true", help="ACTUALLY load and run PaddleOCR on --image")
    parser.add_argument("--include-text", action="store_true",
                        help="persist sensitive recognized strings and coordinates")
    parser.add_argument("--output", type=Path, default=Path("data/eval_runs/ocr_probe.json"))
    args = parser.parse_args()
    if args.infer and args.image is None:
        parser.error("--infer requires --image")
    result = probe(args.image, infer=args.infer, include_text=args.include_text)
    save_report(result, args.output)
    print(json.dumps({
        "status": result["status"],
        "model_invocation_completed": result["model_invocation_completed"],
        "line_count": result["line_count"],
        "elapsed_ms": result["elapsed_ms"],
        "environment": result["environment"],
        "message": result["message"],
        "saved_to": str(args.output),
    }, ensure_ascii=False, indent=2))
    if args.infer and result["status"] != "OK":
        print("RESULT: real OCR did not finish; no model accuracy claim is valid.")
        return 2
    if not args.infer:
        print("RESULT: diagnosis only; no OCR inference or accuracy measurement.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
