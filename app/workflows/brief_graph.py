"""M2a 有状态数据传递的 LangGraph 编译工作流。

暂不使用 Checkpointer 或 interrupt；用户确认在前端进行。
后续 M2b 才会实现任务级持久化中断/恢复。
"""
from typing import Literal, TypedDict

from langgraph.graph import StateGraph, START, END

from app.brief_compiler import BriefRequest, compile_brief


class BriefState(TypedDict, total=False):
    request: dict
    draft: dict
    status: Literal["WAITING_INPUT", "WAITING_CONFIRMATION"]


async def compile_node(state: BriefState) -> dict:
    request = BriefRequest.model_validate(state["request"])
    draft = await compile_brief(request)
    return {"draft": draft.model_dump(mode="json")}


def review_node(state: BriefState) -> dict:
    return {"status": "WAITING_INPUT" if state["draft"]["missing_fields"] else "WAITING_CONFIRMATION"}


_builder = StateGraph(BriefState)
_builder.add_node("compile", compile_node)
_builder.add_node("review", review_node)
_builder.add_edge(START, "compile")
_builder.add_edge("compile", "review")
_builder.add_edge("review", END)
brief_graph = _builder.compile()
