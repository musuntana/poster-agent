"""Persistence boundary for M2b: SQLite checkpointer + one-process serialization.

Deliberately single-process. Multi-worker production requires a transactional DB,
explicit ownership and authorization, and per-task locking across workers.
"""
from __future__ import annotations

import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from app.brief_compiler import BriefRequest
from app.schemas import PosterSpec
from app.workflows.review_graph import make_review_graph

LOCK = threading.RLock()
_RUN_ID = re.compile(r"^[a-f0-9]{32}$")


class ReviewNotFound(LookupError):
    pass


class ReviewConflict(RuntimeError):
    pass


def database_path() -> Path:
    db = Path(os.getenv("POSTER_CHECKPOINT_DB", "data/checkpoints.sqlite3")).resolve()
    db.parent.mkdir(parents=True, exist_ok=True)
    return db


@contextmanager
def open_graph():
    # Opening a new connection on each request also proves checkpoints can be
    # rehydrated across connections/restarts. No in-memory saver is used.
    with SqliteSaver.from_conn_string(str(database_path())) as checkpointer:
        yield make_review_graph(checkpointer)


def config(run_id: str) -> dict:
    if not _RUN_ID.fullmatch(run_id):
        raise ReviewNotFound("未找到该审核任务")
    return {"configurable": {"thread_id": run_id}}


def _view(run_id: str, graph) -> dict:
    snapshot = graph.get_state(config(run_id))
    values = snapshot.values or {}
    if not values.get("draft"):
        raise ReviewNotFound("未找到该审核任务")
    status = values.get("status", "WAITING_CONFIRMATION")
    return {
        "run_id": run_id,
        "status": status,
        "draft": values["draft"],
        "confirmed_spec": values.get("confirmed_spec"),
        "pending_approval": "approval" in snapshot.next,
        "background_generation_started": False,
    }


def create_run(request: BriefRequest) -> dict:
    run_id = uuid4().hex
    with LOCK:
        with open_graph() as graph:
            # Note: the approval node interrupts; compile results are checkpointed.
            graph.invoke({"request": request.model_dump(mode="json")}, config(run_id))
            result = _view(run_id, graph)
            if not result["pending_approval"]:
                raise RuntimeError("编译流程未正确进入人工审核节点")
            return result


def get_run(run_id: str) -> dict:
    with LOCK:
        with open_graph() as graph:
            return _view(run_id, graph)


def decide_run(run_id: str, approved: bool, spec: PosterSpec | None) -> dict:
    if approved and spec is None:
        raise ValueError("审核通过前必须提供完整 PosterSpec")
    with LOCK:
        with open_graph() as graph:
            before = _view(run_id, graph)
            if not before["pending_approval"]:
                if before["status"] == "APPROVED" and approved and spec is not None:
                    if before["confirmed_spec"] == spec.model_dump(mode="json"):
                        return before  # exact duplicate is idempotent
                if before["status"] == "REJECTED" and not approved:
                    return before
                raise ReviewConflict("该审核任务已经结束，不能覆盖原有确认结果")
            graph.invoke(
                Command(resume={"approved": approved, "spec": spec.model_dump(mode="json") if spec else None}),
                config(run_id),
            )
            result = _view(run_id, graph)
            if result["pending_approval"]:
                raise RuntimeError("审核恢复后仍处于中断状态")
            return result
