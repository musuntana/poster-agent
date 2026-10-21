"""M3c human-review endpoints (local-only, without authentication)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response

from app import human_annotations as reviews

router = APIRouter(tags=["human-review"])


@router.get("/api/posters/{poster_id}/versions/{version}/human-review")
def review_context(poster_id: str, version: int, reviewer: str | None = Query(default=None)):
    try:
        return reviews.get_context(poster_id, version, reviewer)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="海报版本不存在") from exc


@router.post("/api/posters/{poster_id}/versions/{version}/human-review", status_code=201)
def submit_review(poster_id: str, version: int, payload: reviews.SubmitAnnotation):
    try:
        return reviews.submit(poster_id, version, payload)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="海报版本不存在") from exc
    except reviews.AnnotationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/annotations/export.csv")
def export_annotations(reviewer: str = Query(min_length=2, max_length=40)):
    try:
        content, counts = reviews.export_labels(reviewer)
    except reviews.AnnotationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(
        content="\ufeff" + content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="poster-human-labels.csv"',
            "X-Labels-Exported": str(counts["field_labels"]),
            "X-Uncertain-Skipped": str(counts["uncertain_excluded"]),
        },
    )
