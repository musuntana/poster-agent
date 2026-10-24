"""Native 4:5 human annotations: isolated from legacy square image reviews."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response

from app import native_human_reviews as reviews
from app.native_acceptance import NativeVariant, PRODUCT_FOCUS_VARIANT, VARIANT

router = APIRouter(tags=["native-human-review"])


@router.get("/api/posters/{poster_id}/versions/{version}/exports/{variant}/human-review")
def review_context(
    poster_id: str, version: int, variant: NativeVariant,
    reviewer: str | None = Query(default=None),
):
    try:
        return reviews.context(poster_id, version, reviewer, variant=variant)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="海报版本不存在") from exc
    except reviews.NativeReviewConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="无法读取当前原生版本") from exc


@router.post("/api/posters/{poster_id}/versions/{version}/exports/{variant}/human-review", status_code=201)
def submit_review(
    poster_id: str, version: int, variant: NativeVariant,
    payload: reviews.NativeReviewSubmission,
):
    try:
        return reviews.submit(poster_id, version, payload, variant=variant)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="海报版本不存在") from exc
    except reviews.NativeReviewConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _export_labels(reviewer: str, variant: NativeVariant, filename: str):
    try:
        content, counts = reviews.export(reviewer, variant=variant)
    except reviews.NativeReviewConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(
        content="\ufeff" + content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Labels-Exported": str(counts["field_labels"]),
            "X-Uncertain-Skipped": str(counts["uncertain_excluded"]),
        },
    )


@router.get("/api/annotations/native-4x5/export.csv")
def export_native_labels(reviewer: str = Query(min_length=2, max_length=40)):
    return _export_labels(reviewer, VARIANT, "poster-native-4x5-labels.csv")


@router.get("/api/annotations/native-4x5-product-focus/export.csv")
def export_product_focus_labels(reviewer: str = Query(min_length=2, max_length=40)):
    return _export_labels(
        reviewer, PRODUCT_FOCUS_VARIANT, "poster-native-product-focus-4x5-labels.csv"
    )
