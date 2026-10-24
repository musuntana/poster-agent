"""One local command to identify missing 4:5 human/OCR evidence and score it.

By default this is an audit: it does NOT invoke PaddleOCR, models or scoring.
Use --score explicitly only when the status manifest is fully READY.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.native_acceptance import NATIVE_VARIANTS, VARIANT
from evals.native_trial import run_trial
from evals.native_batch_scope import load_batch_scope


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit real 4:5 poster evidence; optionally score saved OCR and human labels"
    )
    parser.add_argument("--reviewer", required=True,
                        help="M5c reviewer alias, e.g. local-operator")
    parser.add_argument("--output-dir", type=Path,
                        default=Path("data/eval_runs/m5e_native_trial"))
    parser.add_argument("--variant", choices=NATIVE_VARIANTS, default=VARIANT,
                        help="Independent native 4:5 template to audit/score (default classic)")
    parser.add_argument("--score", action="store_true",
                        help="Score with existing M5d evaluator (requires complete valid reports)")
    parser.add_argument("--batch-journal", type=Path,
                        help="Optional M5g batch_import_results.json; score latest version of each SKU only")
    args = parser.parse_args()
    try:
        scope = load_batch_scope(args.batch_journal) if args.batch_journal else None
        summary = run_trial(
            args.reviewer, args.output_dir, score=args.score, batch_scope=scope,
            variant=args.variant
        )
    except ValueError as exc:
        parser.exit(2, f"无法评分：{exc}\n缺口清单：{args.output_dir}/native_trial_manifest.csv\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("此命令未执行 OCR 推理；样本真实性仍需运营人工核对。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
