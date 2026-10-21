"""Local human labeling loop for real exported poster versions.

Append-only reviews on disk; exports only decisive (correct/defective) labels
for an explicitly selected reviewer. No auth: MUST NOT expose this service
to untrusted networks. Human judgements are self-reported, not verified.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from typing import Literal

from pydantic import BaseModel, Field, ConfigDict, model_validator

from app import storage
from app.schemas import PosterSpec

FIELDS = ("title", "price", "cta", "subtitle", "original_price")
REQUIRED_FIELDS = {"title", "price", "cta"}
REVIEWER_PATTERN = r"^[a-zA-Z0-9_-]{2,40}$"
VALID_ID = re.compile(r"^[0-9a-f]{32}$")


class AnnotationConflict(RuntimeError):
    pass


class HumanFieldReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    judgment: Literal["correct", "defective", "unsure"]
    reason: str = Field(default="", max_length=300)


class SubmitAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewer: str = Field(min_length=2, max_length=40, pattern=REVIEWER_PATTERN)
    image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fields: dict[Literal["title", "price", "cta", "subtitle", "original_price"], HumanFieldReview]

    @model_validator(mode="after")
    def validate_fields(self):
        if not REQUIRED_FIELDS.issubset(set(self.fields)):
            raise ValueError("至少必须人工检查标题、活动价和 CTA")
        return self


def _source(poster_id: str, version: int):
    image_path = storage.version_path(poster_id, version)
    json_path = storage.poster_dir(poster_id) / "versions" / f"v{version}.json"
    image_bytes = image_path.read_bytes()
    spec_bytes = json_path.read_bytes()
    spec = PosterSpec.model_validate_json(spec_bytes)
    return {
        "spec": spec,
        "image_sha256": hashlib.sha256(image_bytes).hexdigest(),
        "spec_sha256": hashlib.sha256(spec_bytes).hexdigest(),
    }


def _review_dir(poster_id: str, version: int):
    storage.version_path(poster_id, version)  # validated poster and version
    return storage.poster_dir(poster_id) / "versions" / f"v{version}.human_reviews"


def _load_reviews(poster_id: str, version: int) -> list[dict]:
    directory = _review_dir(poster_id, version)
    rows = []
    if directory.is_dir():
        for file in directory.glob("*.json"):
            if not VALID_ID.fullmatch(file.stem):
                continue
            data = json.loads(file.read_text(encoding="utf-8"))
            rows.append(data)
    return sorted(rows, key=lambda r: (r["created_at"], r["review_id"]))


def _latest_by_reviewer(poster_id: str, version: int) -> dict[str, dict]:
    latest = {}
    for review in _load_reviews(poster_id, version):
        latest[review["reviewer"]] = review
    return latest


def get_context(poster_id: str, version: int, reviewer: str | None = None) -> dict:
    with storage.LOCK:
        source = _source(poster_id, version)
        present = ["title", "price", "cta"]
        if source["spec"].subtitle:
            present.append("subtitle")
        if source["spec"].original_price is not None:
            present.append("original_price")
        latest = _latest_by_reviewer(poster_id, version)
        return {
            "poster_id": poster_id,
            "version": version,
            "image_url": f"/api/posters/{poster_id}/versions/{version}/image",
            "image_sha256": source["image_sha256"],
            "spec_sha256": source["spec_sha256"],
            "expected_spec": source["spec"].model_dump(mode="json"),
            "fields_to_review": present,
            "existing_review": latest.get(reviewer) if reviewer else None,
            "review_count": len(_load_reviews(poster_id, version)),
            "distinct_reviewers": len(latest),
        }


def submit(poster_id: str, version: int, annotation: SubmitAnnotation) -> dict:
    with storage.LOCK:
        source = _source(poster_id, version)
        if annotation.image_sha256 != source["image_sha256"]:
            raise AnnotationConflict("海报图片已变化，请刷新页面后重新审核")
        if annotation.spec_sha256 != source["spec_sha256"]:
            raise AnnotationConflict("海报规格已变化，请刷新页面后重新审核")

        expected_fields = set(get_context(poster_id, version)["fields_to_review"])
        supplied = set(annotation.fields)
        if supplied != expected_fields:
            raise ValueError(f"必须准确标注当前版本的所有字段：{sorted(expected_fields)}")

        review_id = uuid4().hex
        record = {
            "review_id": review_id, "poster_id": poster_id, "version": version,
            "reviewer": annotation.reviewer,
            "image_sha256": source["image_sha256"],
            "spec_sha256": source["spec_sha256"],
            "fields": {field: val.model_dump(mode="json")
                       for field, val in annotation.fields.items()},
            "created_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "origin": "manual_review",
        }
        directory = _review_dir(poster_id, version)
        directory.mkdir(parents=True, exist_ok=True)
        # Every submission is immutable; follow-up correction adds a new review.
        target = directory / f"{review_id}.json"
        with target.open("x", encoding="utf-8") as out:
            out.write(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        return record


def export_labels(reviewer: str) -> tuple[str, dict]:
    if not re.fullmatch(REVIEWER_PATTERN, reviewer):
        raise ValueError("reviewer 只能使用 2–40 位字母、数字、下划线或短横线")
    rows = []
    skipped_uncertain = 0
    considered = 0
    with storage.LOCK:
        for directory in sorted(storage.root().glob("*")):
            if not directory.is_dir() or not VALID_ID.fullmatch(directory.name):
                continue
            poster_id = directory.name
            versions = directory / "versions"
            if not versions.is_dir():
                continue
            for image in sorted(versions.glob("v*.png")):
                try:
                    version = int(image.stem[1:])
                    latest = _latest_by_reviewer(poster_id, version).get(reviewer)
                    if latest is None:
                        continue
                    source = _source(poster_id, version)
                    if latest["image_sha256"] != source["image_sha256"] or latest["spec_sha256"] != source["spec_sha256"]:
                        raise AnnotationConflict(f"{poster_id} v{version}: 人工审核结果与图片/规格已不一致")
                    considered += 1
                    for field, result in latest["fields"].items():
                        if result["judgment"] == "unsure":
                            skipped_uncertain += 1
                            continue
                        rows.append({
                            "poster_id": poster_id,
                            "version": version,
                            "field": field,
                            "defective": "true" if result["judgment"] == "defective" else "false",
                            "image_sha256": source["image_sha256"],
                            "reviewer": reviewer,
                        })
                except FileNotFoundError as exc:
                    raise AnnotationConflict(f"{poster_id}: 本地审核数据对应版本丢失") from exc
    if not rows:
        raise ValueError("当前审核员尚无确定性字段标注，不能导出空评测集")
    handle = io.StringIO()
    writer = csv.DictWriter(handle, fieldnames=[
        "poster_id", "version", "field", "defective", "image_sha256", "reviewer",
    ])
    writer.writeheader()
    writer.writerows(rows)
    return handle.getvalue(), {
        "poster_versions": considered, "field_labels": len(rows),
        "uncertain_excluded": skipped_uncertain,
    }
