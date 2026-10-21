"""M5c: auditable HUMAN inspection of the rendered native 4:5 PNG.

This is intentionally separate from square-vN human labels. Neither OCR
accuracy nor product fidelity is inferred automatically from a human click.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from uuid import uuid4
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app import storage
from app.human_annotations import HumanFieldReview, REVIEWER_PATTERN, VALID_ID
from app.native_acceptance import VARIANT, _render, _sources

VISUAL_FIELDS = ("sku_integrity", "text_legibility", "composition")
CORE_FIELDS = ("title", "price", "cta")
OPTIONAL_FIELDS = ("subtitle", "original_price")
ALL_FIELDS = frozenset((*VISUAL_FIELDS, *CORE_FIELDS, *OPTIONAL_FIELDS))
LABELS = {
    "sku_integrity": "商品图完整/无遮挡",
    "text_legibility": "整体文字可读性",
    "composition": "构图与层次",
    "title": "主标题",
    "price": "活动价",
    "cta": "CTA",
    "subtitle": "副标题",
    "original_price": "原价",
}


class NativeReviewConflict(RuntimeError):
    pass


class NativeReviewSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewer: str = Field(min_length=2, max_length=40, pattern=REVIEWER_PATTERN)
    image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    asset_sha256: dict[Literal["product", "background", "logo"], str | None]
    fields: dict[
        Literal["sku_integrity", "text_legibility", "composition",
                "title", "price", "cta", "subtitle", "original_price"],
        HumanFieldReview,
    ]

    @model_validator(mode="after")
    def must_include_core_fields(self):
        if not set((*CORE_FIELDS, *VISUAL_FIELDS)).issubset(self.fields):
            raise ValueError("必须人工审核商品完整度、可读性、构图、标题、活动价及 CTA")
        return self


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _snapshot(poster_id: str, version: int) -> dict:
    spec, spec_bytes, assets, square_png = _sources(poster_id, version)
    native_png = _render(poster_id, spec)
    fields = list((*VISUAL_FIELDS, *CORE_FIELDS))
    if spec.subtitle:
        fields.append("subtitle")
    if spec.original_price is not None:
        fields.append("original_price")
    return {
        "spec": spec,
        "image_sha256": _digest(native_png),
        "source_image_sha256": _digest(square_png),
        "spec_sha256": _digest(spec_bytes),
        "asset_sha256": assets,
        "fields_to_review": fields,
    }


def _folder(poster_id: str, version: int):
    storage.version_path(poster_id, version)
    return (storage.poster_dir(poster_id) / "versions"
            / f"v{version}.{VARIANT}.human_reviews")


def _all_reviews(poster_id: str, version: int) -> list[dict]:
    folder = _folder(poster_id, version)
    records = []
    if folder.is_dir():
        for path in folder.glob("*.json"):
            if not VALID_ID.fullmatch(path.stem):
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
            records.append(record)
    return sorted(records, key=lambda x: (x["created_at"], x["review_id"]))


def _latest_reviews(records: list[dict]) -> dict[str, dict]:
    latest = {}
    for record in records:
        latest[record["reviewer"]] = record
    return latest


def _matches(review: dict, snapshot: dict) -> bool:
    return all((
        review.get("image_sha256") == snapshot["image_sha256"],
        review.get("source_image_sha256") == snapshot["source_image_sha256"],
        review.get("spec_sha256") == snapshot["spec_sha256"],
        review.get("asset_sha256") == snapshot["asset_sha256"],
        review.get("variant_id") == VARIANT,
    ))


def context(poster_id: str, version: int, reviewer: str | None = None) -> dict:
    with storage.LOCK:
        snapshot = _snapshot(poster_id, version)
        history = _all_reviews(poster_id, version)
        latest = _latest_reviews(history)
        existing = latest.get(reviewer)
        if existing is not None and not _matches(existing, snapshot):
            raise NativeReviewConflict("旧人工标签与实际原生图或源素材不一致，请重新审核")
        return {
            "poster_id": poster_id, "version": version, "variant_id": VARIANT,
            "image_url": f"/api/posters/{poster_id}/versions/{version}/exports/{VARIANT}",
            "image_sha256": snapshot["image_sha256"],
            "source_image_sha256": snapshot["source_image_sha256"],
            "spec_sha256": snapshot["spec_sha256"],
            "asset_sha256": snapshot["asset_sha256"],
            "expected_spec": snapshot["spec"].model_dump(mode="json"),
            "fields_to_review": snapshot["fields_to_review"],
            "field_labels": LABELS,
            "review_count": len(history),
            "distinct_reviewers": len(latest),
            "existing_review": existing,
            "auto_approved": False,
        }


def submit(poster_id: str, version: int, payload: NativeReviewSubmission) -> dict:
    with storage.LOCK:
        snapshot = _snapshot(poster_id, version)
        if not all((
            payload.image_sha256 == snapshot["image_sha256"],
            payload.source_image_sha256 == snapshot["source_image_sha256"],
            payload.spec_sha256 == snapshot["spec_sha256"],
            payload.asset_sha256 == snapshot["asset_sha256"],
        )):
            raise NativeReviewConflict("审核中原生海报或 SKU/背景/规格变化，请刷新页面")
        if set(payload.fields) != set(snapshot["fields_to_review"]):
            raise ValueError("请准确标注当前原生版本的全部字段")

        record = {
            "review_id": uuid4().hex,
            "poster_id": poster_id,
            "version": version,
            "variant_id": VARIANT,
            "reviewer": payload.reviewer,
            "image_sha256": snapshot["image_sha256"],
            "source_image_sha256": snapshot["source_image_sha256"],
            "spec_sha256": snapshot["spec_sha256"],
            "asset_sha256": snapshot["asset_sha256"],
            "fields": {key: value.model_dump(mode="json")
                       for key, value in payload.fields.items()},
            "created_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "origin": "manual_review",
            "auto_approved": False,
        }
        folder = _folder(poster_id, version)
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / f"{record['review_id']}.json"
        # Local-only, append-only; a later correction creates a new record.
        with destination.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        return record


def export(reviewer: str) -> tuple[str, dict]:
    if re.fullmatch(REVIEWER_PATTERN, reviewer) is None:
        raise ValueError("审核员代号不合法")
    rows, considered, uncertain = [], 0, 0
    with storage.LOCK:
        for poster in sorted(storage.root().glob("*")):
            if not poster.is_dir() or not VALID_ID.fullmatch(poster.name):
                continue
            version_dir = poster / "versions"
            if not version_dir.is_dir():
                continue
            for source in sorted(version_dir.glob("v[0-9]*.png")):
                try:
                    version = int(source.stem[1:])
                    latest = _latest_reviews(_all_reviews(poster.name, version)).get(reviewer)
                    if latest is None:
                        continue
                    snapshot = _snapshot(poster.name, version)
                    if not _matches(latest, snapshot):
                        raise NativeReviewConflict(
                            f"{poster.name} v{version}: 人工标注和导出图/源素材摘要不一致"
                        )
                    considered += 1
                    for key in snapshot["fields_to_review"]:
                        result = latest["fields"][key]
                        if result["judgment"] == "unsure":
                            uncertain += 1
                            continue
                        rows.append({
                            "poster_id": poster.name, "version": version,
                            "variant_id": VARIANT, "field": key,
                            "defective": "true" if result["judgment"] == "defective" else "false",
                            "image_sha256": snapshot["image_sha256"],
                            "source_image_sha256": snapshot["source_image_sha256"],
                            "reviewer": reviewer,
                        })
                except (OSError, ValueError) as exc:
                    raise NativeReviewConflict(
                        f"{poster.name}: 人工标注对应的原生图或规格无效"
                    ) from exc

    if not rows:
        raise ValueError("该审核员没有可导出的确定性原生 4:5 标签")
    output = io.StringIO()
    columns = [
        "poster_id", "version", "variant_id", "field", "defective",
        "image_sha256", "source_image_sha256", "reviewer",
    ]
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue(), {
        "poster_versions": considered, "field_labels": len(rows),
        "uncertain_excluded": uncertain,
    }
