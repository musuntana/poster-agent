"""Offline M6 evidence gate for the real-SKU cohort.

This validates local manifests, authorization registry, batch provenance, current
poster versions, review worksheet hashes, and M5j readiness. It never runs OCR,
calls a model, uploads files, or computes a product-quality score.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from app import storage
from evals.native_batch_scope import load_batch_scope
from scripts.real_sku_batch import BatchError, preflight

SCHEMA = "m6-validation-1"
MIN_SKUS = 20
SHA = re.compile(r"^[0-9a-f]{64}$")
SKU = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
VERSION = re.compile(r"^[1-9][0-9]*$")
LABELS = frozenset({"PASS", "FAIL", "UNVERIFIED"})
VARIANTS = ("square", "feed_4x5_native")

REGISTRY_FIELDS = (
    "cohort_id", "sku_id", "product_category", "product_family_id",
    "variant_or_packaging_condition", "source_asset_authorization_status",
    "authorization_evidence_ref", "logo_authorization_status",
    "source_image_sha256", "distinct_sku_verified",
    "duplicate_source_image_reviewed", "reviewer_alias", "notes",
)
REVIEW_FIELDS = (
    "cohort_id", "sku_id", "poster_id", "source_version", "variant_id",
    "output_image_path", "source_product_sha256", "request_sha256",
    "spec_sha256", "output_image_sha256", "source_square_image_sha256",
    "reviewer_alias", "sku_integrity", "text_legibility", "title",
    "subtitle", "price", "original_price", "price_position",
    "original_price_position", "cta", "composition", "overall_accept",
    "overall_reason", "reviewed_at_utc",
)
NATIVE_MANIFEST_FIELDS = (
    "poster_id", "version", "sku_id", "status", "definite_labels",
    "ocr_text_labels", "unsure_labels", "product_sha256",
    "native_image_sha256", "action",
)
FIELD_LABELS = (
    "sku_integrity", "text_legibility", "title", "subtitle", "price",
    "original_price", "price_position", "original_price_position", "cta",
    "composition",
)


class M6ValidationError(ValueError):
    """A local evidence package cannot be validated safely."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_sha(value: str) -> bool:
    return bool(SHA.fullmatch(value or ""))


def _read_csv(path: Path, fields: tuple[str, ...], label: str):
    path = path.resolve(strict=True)
    raw = path.read_bytes()
    if not raw or len(raw) > 2_000_000:
        raise M6ValidationError(f"{label} CSV 为空或超过 2 MB")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            header = reader.fieldnames
            if not header or len(header) != len(set(header)) or set(header) != set(fields):
                raise M6ValidationError(
                    f"{label} CSV 表头不匹配；缺失列={sorted(set(fields) - set(header or []))}，"
                    f"额外列={sorted(set(header or []) - set(fields))}"
                )
            rows = []
            for line, row in enumerate(reader, start=2):
                if None in row or any(value is None for value in row.values()):
                    raise M6ValidationError(f"{label} CSV 第 {line} 行列数与表头不一致")
                rows.append({key: (value or "").strip() for key, value in row.items()})
    except (UnicodeDecodeError, csv.Error) as exc:
        raise M6ValidationError(f"{label} CSV 不是合法 UTF-8 CSV") from exc
    if not rows:
        raise M6ValidationError(f"{label} CSV 没有数据行")
    return path, rows, _sha(raw)


def _read_json(path: Path, label: str) -> tuple[Path, dict, str]:
    path = path.resolve(strict=True)
    raw = path.read_bytes()
    if not raw or len(raw) > 2_000_000:
        raise M6ValidationError(f"{label} JSON 为空或超过 2 MB")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise M6ValidationError(f"{label} 不是合法 UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise M6ValidationError(f"{label} 顶层必须是 JSON object")
    return path, value, _sha(raw)


def _utc_timestamp(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)


def _output_path(value: str, reviews_path: Path) -> Path:
    if not value or value.startswith(("http://", "https://")):
        raise M6ValidationError("输出图片必须是本地路径，不能使用 URL")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = reviews_path.parent / path
    return path.resolve(strict=True)


def validate_bundle(
    manifest: Path,
    registry: Path,
    journal: Path,
    reviews: Path,
    native_manifest: Path,
    readiness: Path,
    *,
    min_skus: int = MIN_SKUS,
) -> dict:
    """Validate the full M6 local evidence bundle without mutating its inputs."""
    manifest_path = Path(manifest)
    _registry_path, registry_rows, registry_sha = _read_csv(
        Path(registry), REGISTRY_FIELDS, "M6 cohort registry"
    )
    reviews_path, review_rows, reviews_sha = _read_csv(
        Path(reviews), REVIEW_FIELDS, "M6 output review"
    )
    _native_manifest_path, native_rows, native_manifest_sha = _read_csv(
        Path(native_manifest), NATIVE_MANIFEST_FIELDS, "M5j native trial manifest"
    )
    journal_path = Path(journal).resolve(strict=True)
    journal_path, journal_data, journal_sha = _read_json(journal_path, "M5g batch journal")
    _readiness_path, readiness_data, readiness_sha = _read_json(
        Path(readiness), "M5j readiness"
    )

    try:
        manifest_sha, prepared = preflight(manifest_path)
    except (BatchError, OSError, ValueError) as exc:
        raise M6ValidationError(f"M5g manifest 预检失败：{exc}") from exc
    prepared_by_sku = {item["sku_id"]: item for item in prepared}
    expected_skus = set(prepared_by_sku)
    errors: list[str] = []
    warnings: list[str] = []
    present_output_count = 0
    missing_output_count = 0

    try:
        scope = load_batch_scope(journal_path)
    except (OSError, ValueError) as exc:
        raise M6ValidationError(f"M5g batch journal 无效：{exc}") from exc

    if journal_data.get("schema_version") != "m5g-1" or journal_data.get("mode") != "apply":
        errors.append("M5g journal 不是 schema m5g-1 的 apply 批次")
    if journal_data.get("completed") is not True:
        errors.append("M5g journal 未标记 completed=true")
    if journal_data.get("manifest_sha256") != manifest_sha:
        errors.append("冻结 M5g CSV SHA-256 与 batch journal 不一致")
    if scope.get("source_manifest_sha256") != manifest_sha:
        errors.append("M5j batch scope 的 source manifest SHA-256 不一致")

    journal_rows = journal_data.get("rows")
    if not isinstance(journal_rows, dict):
        raise M6ValidationError("M5g journal 缺少 rows object")
    expected_from_journal = journal_data.get("expected_sku_ids")
    if not isinstance(expected_from_journal, list) or set(expected_from_journal) != expected_skus:
        errors.append("M5g expected_sku_ids 与冻结 CSV 的 SKU 集合不一致")
    if journal_data.get("expected_sku_count") != len(expected_skus):
        errors.append("M5g expected_sku_count 与冻结 CSV 的 SKU 数不一致")
    if set(journal_rows) != expected_skus:
        errors.append("M5g rows 与冻结 CSV 的 SKU 集合不一致")
    if scope.get("batch_sku_count") != len(expected_skus):
        errors.append("M5g batch scope SKU 数与冻结 CSV 不一致")
    native_by_sku: dict[str, dict] = {}
    if len(native_rows) != len(expected_skus):
        errors.append("M5j native_trial_manifest.csv 行数与冻结 cohort SKU 数不一致")
    for index, row in enumerate(native_rows, start=2):
        sku = row["sku_id"]
        if not SKU.fullmatch(sku):
            errors.append(f"M5j manifest 第 {index} 行 sku_id 不合法")
            continue
        if sku in native_by_sku:
            errors.append(f"M5j manifest 中 sku_id 重复：{sku}")
            continue
        native_by_sku[sku] = row
        if sku not in expected_skus:
            errors.append(f"M5j manifest 含 cohort 之外的 SKU：{sku}")
            continue
        journal_item = journal_rows.get(sku, {})
        if row["poster_id"] != journal_item.get("poster_id"):
            errors.append(f"{sku}: M5j manifest poster_id 与 M5g journal 不一致")
        if row["product_sha256"] != journal_item.get("product_sha256"):
            errors.append(f"{sku}: M5j manifest product_sha256 与 M5g journal 不一致")
        if not _is_sha(row["native_image_sha256"]):
            errors.append(f"{sku}: M5j manifest native_image_sha256 格式无效")
        if row["status"] != "READY":
            errors.append(f"{sku}: M5j manifest 状态不是 READY")
        if not VERSION.fullmatch(row["version"]):
            errors.append(f"{sku}: M5j manifest version 必须是正整数")
    if set(native_by_sku) != expected_skus:
        errors.append("M5j manifest 的 SKU 集合必须与冻结 M5g CSV 完全一致")
    if len(expected_skus) < min_skus:
        errors.append(f"独立 SKU 数不足：{len(expected_skus)}，要求至少 {min_skus}")

    # The M5j file is a readiness report only. It does not cryptographically
    # attest that a real OCR engine ran; that limitation remains explicit.
    readiness_checks = {
        "can_score": readiness_data.get("can_score") is True,
        "scope_mode": readiness_data.get("scope_mode") == "batch_latest_per_sku",
        "journal_sha256": readiness_data.get("source_batch_journal_sha256") == journal_sha,
        "manifest_sha256": readiness_data.get("source_manifest_sha256") == manifest_sha,
        "batch_sku_count": readiness_data.get("batch_sku_count") == len(expected_skus),
        "total_versions": readiness_data.get("total_versions") == len(expected_skus),
        "ready_versions": readiness_data.get("ready_versions") == len(expected_skus),
    }
    for name, passed in readiness_checks.items():
        if not passed:
            errors.append(f"M5j readiness 门禁失败：{name}")
    if readiness_data.get("ocr_runtime_attested") is not False:
        warnings.append("M5j readiness 未明确声明 OCR runtime attestation 为 false；人工核对报告来源")

    cohort_ids = {row["cohort_id"] for row in registry_rows if row["cohort_id"]}
    if len(cohort_ids) != 1 or any(not row["cohort_id"] for row in registry_rows):
        errors.append("M6 registry 必须使用且仅使用一个非空 cohort_id")
    cohort_id = next(iter(cohort_ids), "")
    registry_by_sku: dict[str, dict] = {}
    for index, row in enumerate(registry_rows, start=2):
        sku = row["sku_id"]
        if not SKU.fullmatch(sku):
            errors.append(f"registry 第 {index} 行 sku_id 不合法")
            continue
        if sku in registry_by_sku:
            errors.append(f"registry 中 sku_id 重复：{sku}")
            continue
        registry_by_sku[sku] = row
        if sku not in expected_skus:
            errors.append(f"registry 含冻结 M5g CSV 之外的 SKU：{sku}")
            continue
        prepared_item = prepared_by_sku[sku]
        journal_item = journal_rows.get(sku, {})
        if row["source_asset_authorization_status"] != "AUTHORIZED":
            errors.append(f"{sku}: 商品素材授权状态不是 AUTHORIZED")
        if not row["authorization_evidence_ref"]:
            errors.append(f"{sku}: 缺少本地授权证据引用")
        if row["logo_authorization_status"] not in {"AUTHORIZED", "NOT_APPLICABLE"}:
            errors.append(f"{sku}: Logo 授权状态无效")
        if prepared_item.get("logo_sha256") is None and row["logo_authorization_status"] != "NOT_APPLICABLE":
            errors.append(f"{sku}: M5g 未使用 Logo，registry 应标 NOT_APPLICABLE")
        if prepared_item.get("logo_sha256") is not None and row["logo_authorization_status"] != "AUTHORIZED":
            errors.append(f"{sku}: M5g 使用了 Logo，但 registry 未标 AUTHORIZED")
        if row["source_image_sha256"] != journal_item.get("product_sha256"):
            errors.append(f"{sku}: registry 源图 SHA-256 与 M5g journal 不一致")
        if not _is_sha(row["source_image_sha256"]):
            errors.append(f"{sku}: registry 源图 SHA-256 格式无效")
        if row["distinct_sku_verified"] != "YES":
            errors.append(f"{sku}: distinct_sku_verified 必须是 YES")
        if row["duplicate_source_image_reviewed"] != "YES":
            errors.append(f"{sku}: duplicate_source_image_reviewed 必须是 YES")
        for field in ("product_category", "product_family_id", "variant_or_packaging_condition", "reviewer_alias"):
            if not row[field]:
                errors.append(f"{sku}: registry 字段 {field} 不能为空")

    if set(registry_by_sku) != expected_skus:
        errors.append("M6 registry 的 SKU 集合必须与冻结 M5g CSV 完全一致")
    if len(registry_rows) != len(expected_skus):
        errors.append("M6 registry 必须恰好每个 SKU 一行")
    if len({row["product_category"] for row in registry_rows if row["product_category"]}) < 2:
        warnings.append("cohort 只记录到一种商品类别；请确认样本覆盖是否足够多样")
    if len({row["product_family_id"] for row in registry_rows if row["product_family_id"]}) < 2:
        warnings.append("cohort 只记录到一个商品家族；请确认是否把近似变体误当成独立样本")

    source_hashes = [
        record.get("product_sha256", "")
        for record in journal_rows.values() if isinstance(record, dict)
    ]
    if len(set(source_hashes)) < len(source_hashes):
        warnings.append("不同 SKU 存在重复源图 SHA-256；已保留样本，但需在最终汇总中报告")

    if len(review_rows) != 2 * len(expected_skus):
        errors.append(
            f"M6 review 行数应为 {2 * len(expected_skus)}，实际为 {len(review_rows)}；"
            "缺失输出也必须保留对应行"
        )
    review_by_key: dict[tuple[str, str, str], dict] = {}
    for index, row in enumerate(review_rows, start=2):
        sku, variant = row["sku_id"], row["variant_id"]
        if variant not in VARIANTS:
            errors.append(f"review 第 {index} 行 variant_id 无效：{variant}")
            continue
        key = (row["cohort_id"], sku, variant)
        if key in review_by_key:
            errors.append(f"review 组合键重复：{sku}/{variant}")
            continue
        review_by_key[key] = row
        if sku not in expected_skus:
            errors.append(f"review 含冻结 cohort 之外的 SKU：{sku}")
            continue
        if row["cohort_id"] != cohort_id:
            errors.append(f"{sku}/{variant}: review cohort_id 与 registry 不一致")
        if row["poster_id"] != journal_rows.get(sku, {}).get("poster_id"):
            errors.append(f"{sku}/{variant}: poster_id 与 M5g journal 不一致")
        if row["source_product_sha256"] != journal_rows.get(sku, {}).get("product_sha256"):
            errors.append(f"{sku}/{variant}: source_product_sha256 与 M5g journal 不一致")
        if row["request_sha256"] != journal_rows.get(sku, {}).get("request_sha256"):
            errors.append(f"{sku}/{variant}: request_sha256 与 M5g journal 不一致")
        if not row["reviewer_alias"]:
            errors.append(f"{sku}/{variant}: reviewer_alias 不能为空")
        if not VERSION.fullmatch(row["source_version"]):
            errors.append(f"{sku}/{variant}: source_version 必须是正整数")
            continue

        if variant == "feed_4x5_native":
            native_record = native_by_sku.get(sku)
            if native_record is None:
                errors.append(f"{sku}/{variant}: 缺少 M5j native manifest 记录")
            else:
                if native_record["version"] != row["source_version"]:
                    errors.append(f"{sku}/{variant}: source_version 与 M5j native manifest 不一致")
                if native_record["native_image_sha256"] != row["output_image_sha256"]:
                    errors.append(f"{sku}/{variant}: 评审图片 SHA-256 与 M5j native_image_sha256 不一致")

        poster_id = row["poster_id"]
        try:
            latest = storage.view(poster_id)
            version = int(row["source_version"])
            if latest["version"] != version:
                errors.append(f"{sku}/{variant}: source_version 不是当前最新版本")
            square_path = storage.version_path(poster_id, version)
            square_sha = _sha(square_path.read_bytes())
            spec_path = storage.poster_dir(poster_id) / "versions" / f"v{version}.json"
            spec_raw = spec_path.read_bytes()
            spec = json.loads(spec_raw)
            spec_sha = _sha(spec_raw)
        except (FileNotFoundError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            errors.append(f"{sku}/{variant}: 无法读取当前海报版本证据：{exc}")
            continue

        if row["spec_sha256"] != spec_sha:
            errors.append(f"{sku}/{variant}: spec_sha256 与当前 v{version}.json 不一致")
        if not _is_sha(row["spec_sha256"]):
            errors.append(f"{sku}/{variant}: spec_sha256 格式无效")
        if row["source_square_image_sha256"] != square_sha:
            errors.append(f"{sku}/{variant}: source_square_image_sha256 与当前方图不一致")
        if not _is_sha(row["source_square_image_sha256"]):
            errors.append(f"{sku}/{variant}: source_square_image_sha256 格式无效")

        for field in FIELD_LABELS:
            value = row[field]
            na_allowed = (
                (field == "subtitle" and not str(spec.get("subtitle") or "").strip())
                or (field in {"original_price", "original_price_position"}
                    and spec.get("original_price") in (None, ""))
            )
            if value == "N/A" and na_allowed:
                continue
            if value not in LABELS:
                errors.append(f"{sku}/{variant}: 字段 {field} 的标签值无效：{value or '(empty)'}")
        if row["overall_accept"] not in LABELS:
            errors.append(f"{sku}/{variant}: overall_accept 必须为 PASS/FAIL/UNVERIFIED")
        if row["overall_accept"] in {"FAIL", "UNVERIFIED"} and not row["overall_reason"]:
            errors.append(f"{sku}/{variant}: overall FAIL/UNVERIFIED 必须填写 overall_reason")
        if not _utc_timestamp(row["reviewed_at_utc"]):
            errors.append(f"{sku}/{variant}: reviewed_at_utc 必须是带 UTC 时区的 ISO 8601 时间")

        path_value, hash_value = row["output_image_path"], row["output_image_sha256"]
        if not path_value and not hash_value:
            missing_output_count += 1
            if row["overall_accept"] != "UNVERIFIED":
                errors.append(f"{sku}/{variant}: 缺失输出必须标记 overall_accept=UNVERIFIED")
            if not any(code in row["overall_reason"] for code in ("OUTPUT_MISSING", "EXPORT_FAILED")):
                errors.append(f"{sku}/{variant}: 缺失输出必须记录 OUTPUT_MISSING 或 EXPORT_FAILED")
            errors.append(f"{sku}/{variant}: 预期输出缺失，保留在分母并阻塞 M6 验收")
            continue
        if not path_value or not hash_value:
            missing_output_count += 1
            errors.append(f"{sku}/{variant}: output_image_path 与 output_image_sha256 必须同时填写")
            continue
        if not _is_sha(hash_value):
            errors.append(f"{sku}/{variant}: output_image_sha256 格式无效")
        try:
            actual_path = _output_path(path_value, reviews_path)
            output_raw = actual_path.read_bytes()
            actual_sha = _sha(output_raw)
            with Image.open(actual_path) as image:
                image.load()
                actual_size = image.size
                image_format = image.format
            present_output_count += 1
        except (OSError, ValueError, M6ValidationError) as exc:
            missing_output_count += 1
            errors.append(f"{sku}/{variant}: 输出 PNG 不可读取：{exc}")
            continue
        if actual_sha != hash_value:
            errors.append(f"{sku}/{variant}: output_image_sha256 与实际文件不一致")
        expected_size = (1024, 1024) if variant == "square" else (1080, 1350)
        if image_format != "PNG" or actual_size != expected_size:
            errors.append(
                f"{sku}/{variant}: 输出必须是 {expected_size[0]}x{expected_size[1]} PNG，"
                f"实际格式/尺寸={image_format}/{actual_size}"
            )
        if variant == "square" and actual_sha != square_sha:
            errors.append(f"{sku}/{variant}: 方图评审文件与当前存储版本 PNG 不一致")

    for sku in sorted(expected_skus):
        for variant in VARIANTS:
            if (cohort_id, sku, variant) not in review_by_key:
                errors.append(f"{sku}/{variant}: 缺少必需的评审行")

    overall_counts = {
        variant: dict(Counter(
            row["overall_accept"] for row in review_rows
            if row["variant_id"] == variant and row["overall_accept"] in LABELS
        ))
        for variant in VARIANTS
    }
    for variant in VARIANTS:
        for label in LABELS:
            overall_counts[variant].setdefault(label, 0)

    report = {
        "schema_version": SCHEMA,
        "status": "PASS" if not errors else "BLOCKED",
        "scope": "local_metadata_and_evidence_integrity_only",
        "product_quality_score_computed": False,
        "ocr_inference_performed": False,
        "paid_model_calls": 0,
        "minimum_skus_required": min_skus,
        "expected_sku_count": len(expected_skus),
        "registry_row_count": len(registry_rows),
        "expected_output_count": 2 * len(expected_skus),
        "review_row_count": len(review_rows),
        "present_output_count": present_output_count,
        "missing_output_count": missing_output_count,
        "distinct_source_image_sha256_count": len(set(source_hashes)),
        "product_category_count": len({row["product_category"] for row in registry_rows if row["product_category"]}),
        "product_family_count": len({row["product_family_id"] for row in registry_rows if row["product_family_id"]}),
        "manual_overall_counts": overall_counts,
        "input_sha256": {
            "manifest": manifest_sha,
            "batch_journal": journal_sha,
            "cohort_registry": registry_sha,
            "output_reviews": reviews_sha,
            "m5j_native_manifest": native_manifest_sha,
            "m5j_readiness": readiness_sha,
        },
        "warnings": warnings,
        "errors": errors,
        "limitations": [
            "Registry authorization and distinct-SKU fields are operator-supplied assertions, not signed proof.",
            "M5j readiness metadata is not cryptographic attestation that a real OCR engine ran.",
            "PASS means evidence metadata and files passed these checks; it is not a product-quality or production-readiness claim.",
        ],
    }
    return report


def _safe_report_destination(output: Path, inputs: tuple[Path, ...]) -> Path:
    destination = output.expanduser().resolve()
    for source in inputs:
        if destination == Path(source).expanduser().resolve():
            raise M6ValidationError("报告输出路径不能覆盖 manifest、registry、journal、reviews 或 readiness 输入")
    if destination.exists():
        raise M6ValidationError("报告输出文件已存在；请使用新路径以保留此前的证据记录")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the local M6 real-SKU evidence bundle; no OCR/model calls"
    )
    parser.add_argument("--manifest", type=Path, required=True, help="Frozen M5g CSV")
    parser.add_argument("--registry", type=Path, required=True, help="Local M6 cohort registry CSV")
    parser.add_argument("--journal", type=Path, required=True, help="Completed M5g batch_import_results.json")
    parser.add_argument("--reviews", type=Path, required=True, help="Local M6 output review CSV")
    parser.add_argument("--native-manifest", type=Path, required=True,
                        help="M5j native_trial_manifest.csv; binds reviewed PNG to OCR/review evidence")
    parser.add_argument("--readiness", type=Path, required=True, help="M5j native_trial_readiness.json")
    parser.add_argument("--output", type=Path, required=True, help="Local JSON report destination")
    args = parser.parse_args()
    try:
        destination = _safe_report_destination(
            args.output,
            (args.manifest, args.registry, args.journal, args.reviews,
             args.native_manifest, args.readiness),
        )
    except M6ValidationError as exc:
        parser.error(str(exc))
    try:
        report = validate_bundle(
            args.manifest, args.registry, args.journal, args.reviews,
            args.native_manifest, args.readiness
        )
    except (M6ValidationError, BatchError, OSError, ValueError, json.JSONDecodeError) as exc:
        report = {
            "schema_version": SCHEMA,
            "status": "BLOCKED",
            "scope": "local_metadata_and_evidence_integrity_only",
            "product_quality_score_computed": False,
            "ocr_inference_performed": False,
            "paid_model_calls": 0,
            "errors": [str(exc)],
            "warnings": [],
        }
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    destination.write_bytes(raw)
    print(json.dumps({**report, "report_sha256": _sha(raw)}, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
