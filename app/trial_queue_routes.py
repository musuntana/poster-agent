"""M5h: read-only, local-first native 4:5 review queue.

The status computation intentionally reuses M5e audit_trial, so the UI cannot
quietly invent a different meaning for READY/OCR_STALE/REVIEW_UNSURE. The
browser navigates to the existing review controls; there is no new reviewer
or acceptance database.
"""
from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, HTTPException, Query

from app import storage
from app.native_acceptance import NativeVariant, VARIANT
from evals.native_trial import audit_trial

router = APIRouter(prefix="/api/trials", tags=["local-trial-queue"])

PRIORITY = {
    "REVIEW_STALE": 0,
    "SOURCE_INVALID": 1,
    "NEEDS_REVIEW": 2,
    "REVIEW_UNSURE": 3,
    "OCR_MISSING": 4,
    "OCR_NOT_OK": 5,
    "OCR_STALE": 6,
    "OCR_CHECKS_INVALID": 7,
    "READY": 8,
}


@router.get("/review-queue")
def local_native_review_queue(
    reviewer: str = Query(default="local-operator", min_length=2, max_length=40),
    variant: NativeVariant = VARIANT,
):
    """Return latest poster versions for a chosen alias; no writes, no OCR runs.

    This is intentionally limited to locally stored posters. 'READY' only
    means enough evidence exists for *offline scoring*, not ad publication.
    """
    try:
        summary, rows = audit_trial(reviewer, variant=variant)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    latest: dict[str, dict] = {}
    for row in rows:
        pid = row["poster_id"]
        if pid not in latest or row["version"] > latest[pid]["version"]:
            latest[pid] = row
    result = []
    for pid, row in latest.items():
        try:
            item = storage.view(pid)
            title = item["spec"]["title"]
            if item["version"] != row["version"]:
                # A local concurrent writer changed a version during audit;
                # don't point the operator to an inconsistent snapshot.
                status = "SOURCE_INVALID"
                action = "版本已变化，请重新刷新审核队列"
            else:
                status, action = row["status"], row["action"]
        except (OSError, ValueError, KeyError, TypeError):
            title = "规格无法读取"
            status = "SOURCE_INVALID"
            action = "原始海报规格或元数据不可读取"
        result.append({
            "poster_id": pid, "version": row["version"],
            "title": title, "status": status, "action": action,
            "definite_labels": row["definite_labels"],
            "unsure_labels": row["unsure_labels"],
            "ocr_text_labels": row["ocr_text_labels"],
            "native_image_url": (
                f"/api/posters/{pid}/versions/{row['version']}"
                f"/exports/{variant}"
            ),
            "poster_url": f"/api/posters/{pid}",
        })
    result.sort(key=lambda x: (
        PRIORITY.get(x["status"], 99), x["poster_id"]
    ))
    counts = Counter(item["status"] for item in result)
    return {
        "reviewer": reviewer,
        "variant_id": variant,
        "read_only": True, "runs_ocr": False, "automatically_approved": False,
        "total_posters": len(result),
        "total_versions_in_cohort": summary["total_versions"],
        "latest_status_counts": dict(sorted(counts.items())),
        "items": result,
        "note": "只展示每张海报的最新版本；READY 表示可评测，不表示允许投放",
    }
