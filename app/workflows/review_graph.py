"""M2b durable workflow: compile once, interrupt for human approval, resume once.

The same LangGraph thread_id must be used for every invocation. No image generation
happens inside this graph. The node containing interrupt has no external side effects.
"""
from __future__ import annotations

import asyncio
from typing import Literal, TypedDict

from langgraph.graph import START, END, StateGraph
from langgraph.types import interrupt

from app.brief_compiler import BriefRequest, compile_brief
from app.schemas import PosterSpec


class ReviewState(TypedDict, total=False):
    request: dict
    draft: dict
    status: Literal["WAITING_INPUT", "WAITING_CONFIRMATION", "APPROVED", "REJECTED"]
    confirmed_spec: dict | None


def extract_node(state: ReviewState) -> dict:
    """Sync graph node runs inside FastAPI's threadpool; Qwen extraction is async."""
    request = BriefRequest.model_validate(state["request"])
    draft = asyncio.run(compile_brief(request))
    return {
        "draft": draft.model_dump(mode="json"),
        "status": "WAITING_INPUT" if draft.missing_fields else "WAITING_CONFIRMATION",
    }


def approval_node(state: ReviewState) -> dict:
    # Important: no writes or payable API calls before interrupt, because the
    # interrupted node starts again from its beginning on resume.
    decision = interrupt({
        "action": "human_review",
        "draft": state["draft"],
        "message": "请核对标题、商品价格及营销文案。必须由运营人员明确确认。",
    })
    if not isinstance(decision, dict) or not decision.get("approved"):
        return {"status": "REJECTED", "confirmed_spec": None}
    spec = PosterSpec.model_validate(decision["spec"])
    return {"status": "APPROVED", "confirmed_spec": spec.model_dump(mode="json")}


def make_review_graph(checkpointer):
    graph = StateGraph(ReviewState)
    graph.add_node("extract", extract_node)
    graph.add_node("approval", approval_node)
    graph.add_edge(START, "extract")
    graph.add_edge("extract", "approval")
    graph.add_edge("approval", END)
    return graph.compile(checkpointer=checkpointer)
