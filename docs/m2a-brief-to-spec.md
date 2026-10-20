# M2a：自然语言需求 → PosterSpec 草稿（尚未加入持久化 Agent 运行时）

> 本阶段是 M2 的**第一步**，不是 M2 全部完成。不在未经用户确认的情况下调用图片生成，也不把价格猜测当成权威事实。

## 流程

```text
运营 Brief
   ↓
/api/briefs/compile
   ↓
LangGraph(compile_node → review_node)
   ↓
规则 Demo 或 Qwen LLM JSON Object
   ↓
原文价格标签再核对 + Pydantic 字段验证
   ↓
WAITING_INPUT / WAITING_CONFIRMATION
   ↓
网页展示草稿 → 用户修改 / 确认
   ↓
M1 /api/posters 生成真实海报
```

- **Demo 模式**：简单正则抽取，只识别如 `主标题：...，活动价199元` 这种带标签的字段；不是 AI。
- **Qwen 模式**：通过 `/chat/completions` 和 `response_format: {"type":"json_object"}` 调用 Qwen，随后再用 Pydantic 校验字段。
- **价格保护**：对 `price` 和 `original_price`，只允许从 Brief **明确标签**中抽取的数值流入草稿，覆盖 LLM 的自报价格；没有明确标签则要求人工补充。
- **不自动投放**：即使 `missing_fields=[]`，草稿也总返回 `requires_confirmation=true`。
- **LangGraph 当前使用范围**：2 节点无 Checkpointer 工作流，只验证状态和节点抽象；没有持久化暂停/恢复，不要称作生产级 Durable Agent。

## 无 Key 体验

打开 Web 页面，在上方输入：

```text
主标题：双十一限时优惠，副标题：智能声波清洁，
活动价199元，原价399元，风格：蓝色科技风，CTA：立即预定
```

选择 `本地规则演示`，点击“编译为海报规格”。检查填充到原有表单中的数据，上传商品图，再点击“生成海报”。

## Qwen 模式

复制 `.env.example` 并在本地 `.env` 补充（与图片 API 使用对应区域工作空间）：

```ini
DASHSCOPE_API_KEY=...
QWEN_CHAT_ENDPOINT=https://你的WorkspaceId.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions
QWEN_CHAT_MODEL=qwen-plus
```

查看阿里云文档确认当前账号允许调用的模型与 JSON 格式能力：<https://help.aliyun.com/en/model-studio/qwen-structured-output>

注意：Qwen 模式发起 **真实付费模型请求**。本仓库的测试仅模拟其输出，没有使用用户 Key 做端到端在线测试。

## API 演示

```bash
curl -X POST http://127.0.0.1:8000/api/briefs/compile \
  -H 'Content-Type: application/json' \
  -d '{"brief":"主标题：双十一商品海报，活动价199元，原价399元，风格：科技风","compiler":"demo"}'
```

期望得到 `status = WAITING_CONFIRMATION` 和 `draft.fields.price = "199"`。

## 学习 / 面试时正确描述

本阶段能够说明：
- LangGraph StateGraph/Node/Edge 的基本职责；
- 结构化输出不是业务事实正确性，必须做 Pydantic + 来源核对；
- Agent 不能直接把缺失价格编造出来；
- LLM 处理与后端生成必须解耦。

后续 M2b 才实现保存草稿、Checkpoint、中断恢复、状态审计、确认后生成任务；M3 再接 OCR/VLM 自动验收与修复。
