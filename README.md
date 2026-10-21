# Poster Agent · 电商营销海报生成与验收

本项目面向电商运营，将 **Brief → 人工确认 → 商品图海报生成 → 多版本修改 → OCR/规则验收 → 人工标注和可复现评测** 串成一个单机可运行的工作流。

> **适用范围：本地研究与产品 Demo。** 尚无登录、权限隔离、可靠任务队列、真实商品保真度验证和生产级事务；仅绑定 `127.0.0.1`，不要直接公网部署。真实 Qwen 图片 API 可能产生费用，本地渐变背景不是 AI 生图。

## 快速启动

要求 Python 3.11–3.13（推荐 3.12）、可用中文字体；Mac 可使用 PingFang，Linux 可使用 Noto Sans CJK。

```bash
git clone git@github.com:musuntana/poster-agent.git
cd poster-agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
python -m scripts.generate_demo
python -m pytest -q
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>；API 文档在 <http://127.0.0.1:8000/docs>。首次示例会在 `data/examples/` 生成测试素材及预览图。若中文缺字，在 `.env` 中设置本机存在的 `POSTER_FONT_PATH`。

## 已实现能力（以当前 main 为准）

| 模块 | 当前能力 | 可信边界 |
| --- | --- | --- |
| M1 渲染与修订 | SKU/Logo 上传、1024×1024 预览、改价产生新版本、PNG 导出；本地背景或 Qwen-Image API | 本地演示完整；用户自己的 Qwen API 需本地验证/计费 |
| M2 Brief 与人工审核 | 规则/Qwen 结构化需求、LangGraph SQLite Checkpoint、规格人工确认 | 本地单进程工作流，没有登录和生产并发隔离 |
| M2 执行可靠性 | SQLite 幂等键、事件日志、已完成复用、结果未知保护 | 不等于跨系统 Exactly-Once；不传幂等键的旧接口仍可重复 |
| M3 Acceptance Gate | 文案/OCR、品牌词表、价格区域位置与冲突检查、报告 SHA-256、可视化诊断 PNG | OCR 缺失与不确定必须标 `UNVERIFIED`，不可冒充 PASS |
| M3 评测 | 合成 OCR 样本、20 张程序渲染 PNG、FP/FN/F1、逐版人工标注及 CSV | 合成数据不是真实业务精度；人工标签尚无独立审计 |
| M4a/M4b 多尺寸派生 | 三种安全缩放尺寸；新增 4:5 原生 SKU/标题/价格/CTA 排版 | 原生布局目前仅 4:5；其它仍安全缩放，所有派生图需人工验收 |
| 真实 OCR 证据 | 独立 GitHub Linux CPU PaddleOCR 3.3.0 成功识别一张程序海报（7 行/7 坐标框） | [Actions 证据](https://github.com/musuntana/poster-agent/actions/runs/39586650764)；Mac M1 和真实客户图片尚未实测 |

**M4a 多尺寸安全派生已实现：** 从已保存的 1024×1024 PNG 离线导出 1080×1350（4:5）、1080×1920（9:16）、1200×675（16:9）。原海报整体等比例缩放、居中，空白部分模糊延展；不会重调 AI 或裁切中央内容。这**不是自适应重排**，新尺寸必须人工重新验收。详见 [M4a 说明](docs/m4a-channel-exports.md)。

**M4b 已新增原生 4:5 布局：** 1080×1350，基于已保存的商品素材、Logo、背景和选定版本规格单独重排，不再次调用收费模型。旧三种比例安全缩放依然可选；尚无针对原生 4:5 的独立 OCR/人工验收基准。详见 [M4b 开发说明](docs/m4b-native-feed.md)。

所有自动化质量检查仍需人工最终审核；尚未实现 VLM 修复、SKU 视觉保真验证或所有渠道原生响应式排版。

## 常用操作

**无需云端 Key：** 网页选择本地背景，生成、修改并导出；`python -m scripts.generate_demo` 可检查本地字体与渲染。

**可选 Qwen 真实背景：** 在 `.env` 设置 `DASHSCOPE_API_KEY`、对应区域/Workspace 的 `QWEN_IMAGE_ENDPOINT`，以及 `QWEN_IMAGE_MODEL`。确保 Key、服务地域和模型互相匹配；勿将 `.env` 提交。详细变量模板见 [`.env.example`](.env.example)。Qwen 结构化 Brief 可按模板配置 `QWEN_CHAT_ENDPOINT`。

**OCR 环境诊断及真实单图推理（可选）：**

```bash
python -m scripts.ocr_probe
python -m scripts.generate_demo
python -m scripts.ocr_probe --infer --image data/examples/sample_preview.png
```

`--infer` 需要安装真实的 PaddlePaddle / PaddleOCR，默认开发依赖并不包含大型模型；安装建议参见 [本地 OCR 运行手册](docs/m3d-ocr-runtime.md)。Linux CI 的成功记录**不能当成 Mac 本机成功记录**。

**质量评测：** `python -m scripts.evaluate_acceptance` 重放合成文本测试；`python -m scripts.run_image_benchmark --ocr none` 生成 20 张 PNG 并标注 OCR 未运行；`--ocr paddle` 才调用真实本地 OCR。对实际海报请在网页中先人工审核并导出 CSV，再运行 `python -m scripts.evaluate_real_reports --annotations data/my-real-labels.csv --ocr-mode paddle`（要求已有与图片哈希一致的真实 OCR 报告）。

## GitHub Actions 额度策略

**不因每次 push 自动触发 CI。** Draft PR → Ready for review 时运行一次快速测试；修改后需要再次验证，则手动运行 CI quick 或给 PR 添加 `ci:run` 标签。完整离线评测选择手动 `full`；昂贵的 `Real PaddleOCR CPU` 仅手动执行。详细流程见 [docs/ci-budget.md](docs/ci-budget.md)。分支维护采用合并 PR 且 HEAD 精确匹配的保护策略，避免误删未完成工作。

## 代码入口与下一阶段

- `app/main.py` / `app/templates/index.html`：FastAPI 与前端；
- `app/compositor.py`、`app/storage.py`：确定性布局、版本素材；
- `app/review_service.py`、`app/workflows/review_graph.py`：规格确认与恢复；
- `app/execution_store.py`：执行账本与幂等；
- `app/acceptance.py`、`app/ocr_provider.py`、`app/ocr_price_layout.py`、`app/ocr_overlay.py`：质量检查、OCR、可视化；
- `app/human_annotations.py`、`evals/`：人工标签与评测。

**下一优先级：** 收集不少于 20 张真实商品海报、人工复核原生 4:5 与方形版本的文字/商品保真效果，并完成真实 OCR；随后再决定是否为 9:16 和 16:9 实现独立排版。VLM 自动修复、鉴权、多租户和异步队列仍属后续里程碑。

更多细节见 [当前文档索引](docs/README.md)，早期记录见 [历史归档](docs/archive/README.md)。
