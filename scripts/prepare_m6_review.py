"""M6: prepare a private offline review package from a completed M5g batch.

Default is a read-only plan. --apply copies the existing square PNGs, uses
the official read-only native 4:5 export route, and creates unreviewed CSV
drafts. No OCR, paid model, upload, authorization or human judgment occurs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app import storage
from app.main import app
from evals.native_batch_scope import load_batch_scope
from scripts.validate_m6_cohort import REGISTRY_FIELDS, REVIEW_FIELDS

SCHEMA = "m6-review-preparation-1"
NATIVE_ID = "feed_4x5_native"
FIELDS = (
    "sku_integrity", "text_legibility", "title", "subtitle",
    "price", "original_price", "price_position", "original_price_position",
    "cta", "composition",
)


class PreparationError(ValueError):
    """Local batch review preparation could not be completed safely."""


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _image_ok(content: bytes, size: tuple[int, int]) -> bool:
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.load()
            return image.format == "PNG" and image.size == size
    except (OSError, ValueError):
        return False


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def prepare_package(journal: Path, output_dir: Path, *, apply: bool = False) -> dict:
    """Reuse existing artifacts without changing poster storage or review state."""
    journal = Path(journal).resolve(strict=True)
    scope = load_batch_scope(journal)
    source = json.loads(journal.read_text(encoding="utf-8"))
    batch = source["rows"]
    sku_by_id = scope["sku_by_id"]
    items = sorted(((sku, pid) for pid, sku in sku_by_id.items()))
    output_dir = Path(output_dir).expanduser().resolve()
    if apply and output_dir.exists():
        raise PreparationError("输出目录已存在；为防止覆盖图片或人工标注，请指定全新的批次目录")

    plan = {
        "schema_version": SCHEMA,
        "mode": "apply" if apply else "check",
        "source_batch_journal_sha256": scope["source_batch_journal_sha256"],
        "source_manifest_sha256": scope["source_manifest_sha256"],
        "batch_sku_count": len(items),
        "expected_review_rows": len(items) * 2,
        "automatically_accepted": False,
        "human_review_performed": False,
        "ocr_inference_performed": False,
        "paid_model_calls": 0,
    }
    if not apply:
        return plan

    # Directories are intentionally local-only and must be new. A failure
    # produces an incomplete directory, never a fake completed review package.
    output_dir.mkdir(parents=True, exist_ok=False)
    images_dir = output_dir / "images"
    images_dir.mkdir()

    registry_rows: list[dict] = []
    review_rows: list[dict] = []
    missing_images = 0
    export_failures: list[dict] = []

    with TestClient(app) as client:
        for sku, pid in items:
            record = batch[sku]
            registry_rows.append({
                "cohort_id": "",
                "sku_id": sku,
                "product_category": "",
                "product_family_id": "",
                "variant_or_packaging_condition": "",
                "source_asset_authorization_status": "PENDING",
                "authorization_evidence_ref": "",
                "logo_authorization_status": (
                    "PENDING" if record.get("logo_sha256") else "NOT_APPLICABLE"
                ),
                "source_image_sha256": record["product_sha256"],
                "distinct_sku_verified": "NO",
                "duplicate_source_image_reviewed": "NO",
                "reviewer_alias": "",
                "notes": "",
            })

            version = str(record["version"])
            spec_sha = ""
            square_sha = ""
            spec: dict = {}
            square_data = b""
            problem = ""
            try:
                current = storage.view(pid)
                version = str(current["version"])
                if current["background_mode"] != "local":
                    raise PreparationError("背景不是 local，拒绝混入免费基线")
                spec_path = storage.poster_dir(pid) / "versions" / f"v{version}.json"
                spec_raw = spec_path.read_bytes()
                spec = json.loads(spec_raw)
                if spec.get("background_mode") != "local":
                    raise PreparationError("规格中的背景模式不是 local")
                spec_sha = _sha(spec_raw)
                square_data = storage.version_path(pid, int(version)).read_bytes()
                if not _image_ok(square_data, (1024, 1024)):
                    raise PreparationError("当前方图不是有效的 1024x1024 PNG")
                square_sha = _sha(square_data)
            except (FileNotFoundError, OSError, ValueError, KeyError,
                    json.JSONDecodeError) as exc:
                problem = f"SOURCE_INVALID:{type(exc).__name__}"

            common = {
                "cohort_id": "",
                "sku_id": sku,
                "poster_id": pid,
                "source_version": version,
                "source_product_sha256": record["product_sha256"],
                "request_sha256": record["request_sha256"],
                "spec_sha256": spec_sha,
                "source_square_image_sha256": square_sha,
                "reviewer_alias": "",
                **{field: "UNVERIFIED" for field in FIELDS},
                "overall_accept": "UNVERIFIED",
                "overall_reason": "NOT_REVIEWED",
                "reviewed_at_utc": "",
            }
            if not str(spec.get("subtitle") or "").strip():
                common["subtitle"] = "N/A"
            if spec.get("original_price") in (None, ""):
                common["original_price"] = "N/A"
                common["original_price_position"] = "N/A"

            square_row = {
                **common, "variant_id": "square",
                "output_image_path": "", "output_image_sha256": "",
            }
            native_row = {
                **common, "variant_id": NATIVE_ID,
                "output_image_path": "", "output_image_sha256": "",
            }
            if problem:
                for row in (square_row, native_row):
                    row["overall_reason"] = "OUTPUT_MISSING:" + problem
                missing_images += 2
                export_failures.append({"sku_id": sku, "reason": problem})
                review_rows.extend((square_row, native_row))
                continue

            square_name = f"images/{sku}-square.png"
            (output_dir / square_name).write_bytes(square_data)
            square_row.update({
                "output_image_path": square_name,
                "output_image_sha256": square_sha,
            })
            response = client.get(
                f"/api/posters/{pid}/versions/{version}/exports/{NATIVE_ID}"
            )
            native_bytes = response.content
            if (
                response.status_code == 200
                and response.headers.get("x-source-image-sha256") == square_sha
                and response.headers.get("x-export-mode") == "native_layout"
                and response.headers.get("x-automatically-accepted") == "false"
                and _image_ok(native_bytes, (1080, 1350))
            ):
                native_name = f"images/{sku}-native-4x5.png"
                (output_dir / native_name).write_bytes(native_bytes)
                native_row.update({
                    "output_image_path": native_name,
                    "output_image_sha256": _sha(native_bytes),
                })
            else:
                # No content or failure text from the route is treated as
                # approved content. The row must remain visible and unverified.
                reason = f"EXPORT_FAILED:HTTP_{response.status_code}"
                native_row["overall_reason"] = reason
                export_failures.append({"sku_id": sku, "reason": reason})
                missing_images += 1

            review_rows.extend((square_row, native_row))

    _write_csv(
        output_dir / "m6_cohort_registry.csv", REGISTRY_FIELDS, registry_rows
    )
    _write_csv(
        output_dir / "m6_output_reviews.csv", REVIEW_FIELDS, review_rows
    )
    summary = {
        **plan,
        "review_rows_created": len(review_rows),
        "missing_output_count": missing_images,
        "export_failures": export_failures,
        "registry_file": "m6_cohort_registry.csv",
        "reviews_file": "m6_output_reviews.csv",
        "workflow": (
            "MANUAL_REQUIRED: confirm licenses and independent SKUs, "
            "review both PNGs, run real OCR separately, then validate evidence"
        ),
    }
    (output_dir / "m6_preparation.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare local unverified M6 drafts without models or OCR"
    )
    parser.add_argument("--journal", required=True, type=Path)
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("data/eval_runs/m6_review_package_001"),
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        result = prepare_package(args.journal, args.output_dir, apply=args.apply)
    except (PreparationError, OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"M6 离线准备失败：{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
