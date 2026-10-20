# M2b：SQLite Checkpoint + Interrupt/Resume + 人工确认

## 真实已实现的能力

- `POST /api/briefs/runs`：创建独立运行线程（随机 `run_id`），LangGraph 编译 Brief，保存检查点并在人工审核节点执行 `interrupt()`。
- `GET /api/briefs/runs/{run_id}`：通过 Checkpointer 重新读取持久化状态。不同请求使用新的 SQLite 连接；进程重启后可使用相同 run_id 读取。
- `POST /api/briefs/runs/{run_id}/decision`：`{"approved": true, "spec": {...}}` 显式审批，调用 `Command(resume=...)` 继续完成工作流；或者 `{"approved": false}` 拒绝。
- `WAITING_INPUT`、`WAITING_CONFIRMATION`、`APPROVED`、`REJECTED` 四种状态；价格缺失只能等待人工填写。
- 完全重复的审批可以幂等重试，不同审批数据返回 HTTP 409，避免悄悄覆盖已确认的版本。
- 审批完成后仍需用户主动点击“生成海报”；没有后台自动付费调用。
- 原有 M1 直接手动填写表单生图的方式保持可用。
- 如果通过 Brief 审批流程发起生图，请求会传送 `review_run_id`，后端再次核对已批准的 PosterSpec，防止确认后偷偷改价再投放。

## 重要限制

- SQLite + 进程级 Lock：仅支持单进程本地开发；不适合多 Worker / 多租户公网服务。
- 无用户认证和权限模型；`run_id` 只是难以猜测的定位 ID，不是授权令牌。
- 未实现 LangGraph 自动修复/质量验收与 Worker 异步队列。
- 目前编译结果只保存规格，不保存商品图；需要用户上传 SKU 再生成图片。
- Qwen 真正的 API Key 没有由 CI 提供；远端 CI 只覆盖离线模拟及规则模式。

## 本地运行

```bash
git pull --ff-only origin main
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

在网页中选择规则演示模式，输入：

```text
主标题：双十一限时优惠，活动价199元，原价399元，风格：蓝色科技风
```

点击“编译”→ 系统返回等待人工确认 → 修改价格或字段 → 点击“确认规格” → 上传 SKU → 点击“生成海报”。

### 持久化验证

先编译 Brief，记住界面显示的运行 ID。关闭并重新打开服务或刷新网页，用原运行 ID 调用：

```bash
curl http://127.0.0.1:8000/api/briefs/runs/替换为实际run_id
```

应能返回保存的草稿及等待状态，**不会再次调用 Qwen**。

## 对应的主要源码

- `app/workflows/review_graph.py`：`StateGraph` + `interrupt()`
- `app/review_service.py`：`SqliteSaver` + `thread_id` + `Command(resume=...)`
- `app/review_routes.py`：持久化任务 API
- `tests/test_review_workflow.py`：创建、重连、审批、幂等、驳回和异常用例

资料：https://docs.langchain.com/oss/python/langgraph/interrupts
