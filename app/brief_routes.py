"""自然语言 Brief 编译 API。用户必须在原有创建表单中确认并提交。"""
from fastapi import APIRouter, HTTPException
from app.brief_compiler import BriefRequest, BriefCompilerError
from app.workflows.brief_graph import brief_graph

router = APIRouter(prefix="/api/briefs", tags=["briefs"])


@router.post("/compile")
async def compile_brief_endpoint(request: BriefRequest):
    try:
        result = await brief_graph.ainvoke({"request": request.model_dump()})
        return {"draft": result["draft"], "status": result["status"]}
    except BriefCompilerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
