"""M2b API: durable review run, reload, explicit approval/rejection."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, model_validator

from app.brief_compiler import BriefCompilerError, BriefRequest
from app.review_service import ReviewNotFound, ReviewConflict, create_run, get_run, decide_run
from app.schemas import PosterSpec

router = APIRouter(prefix="/api/briefs/runs", tags=["brief-review"])


class ReviewDecision(BaseModel):
    approved: bool
    spec: PosterSpec | None = None

    @model_validator(mode="after")
    def require_spec_on_approval(self):
        if self.approved and self.spec is None:
            raise ValueError("审核通过必须提交完整的海报规格")
        return self


@router.post("", status_code=201)
def new_review_run(request: BriefRequest):
    try:
        return create_run(request)
    except BriefCompilerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/{run_id}")
def read_review_run(run_id: str):
    try:
        return get_run(run_id)
    except ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{run_id}/decision")
def review_decision(run_id: str, decision: ReviewDecision):
    try:
        return decide_run(run_id, decision.approved, decision.spec)
    except ReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ReviewConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
