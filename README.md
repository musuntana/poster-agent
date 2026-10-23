# Poster Agent · 电商营销海报生成与验收

> **开源许可：** 本项目采用 [Apache License 2.0（Apache-2.0）](https://www.apache.org/licenses/LICENSE-2.0)，完整许可文本见根目录 [`LICENSE`](LICENSE)。正式发布前仍需核实代码、第三方依赖、字体、素材、示例数据及历史提交的权属和授权；Apache-2.0 不会自动授权你无权再许可的内容。详见 [发布准备清单](docs/open-source-release.md)、[贡献指南](CONTRIBUTING.md)、[行为准则](CODE_OF_CONDUCT.md)、[安全披露](SECURITY.md)。

本项目面向电商运营，将 **Brief → 人工确认 → 商品图海报生成 → 多版本修改 → OCR/规则验收 → 人工标注和可复现评测** 串成一个单机可运行的工作流。

> **适用范围：本地研究与产品 Demo。** 尚无登录、权限隔离、可靠任务队列、真实商品保真度验证和生产级事务；仅绑定 `127.0.0.1`，不要直接公网部署。真实 Qwen 图片 API 可能产生费用，本地渐变背景不是 AI 生图。

## 快速启动

要求 **Python 3.12**（由 uv 根据 `.python-version` 管理）与中文字体；Mac 使用 PingFang，Linux 使用 Noto Sans CJK。先按 [uv 官方说明](https://docs.astral.sh/uv/getting-started/installation/) 安装 uv，不需要手动创建/激活 venv。

```bash
git clone git@github.com:musuntana/poster-agent.git
cd poster-agent
uv python install 3.12
uv sync --locked
cp .env.example .env
uv run --locked python -m scripts.generate_demo
uv run --locked python -m pytest -q
uv run --locked ruff check app scripts evals tests
uv run --locked uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

项目依赖以 `pyproject.toml` 为唯一声明、`uv.lock` 为可复现版本锁，开发依赖置于 `dev` 组。请用 `uv add` 更新依赖并同时提交锁文件；完整说明见 [uv 使用手册](docs/uv-workflow.md)。

打开 <http://127.0.0.1:8000>；API 文档在 <http://127.0.0.1:8000/docs>。首次示例会在 `data/examples/` 生成测试素材及预览图。若中文缺字，在 `.env` 中设置本机存在的 `POSTER_FONT_PATH`。

## 已实现能力（以当前 main 为准）

| 模块 | 当前能力 | 可信边界 |
| --- | --- | --- |
| M1 渲染与修订 | SKU/Logo 上传、1024×1024 预览、改价产生新版本、PNG 导出；本地背景或 Qwen-Image API | 10 MiB/1600 万像素上传限制，ICC 色彩先转 sRGB 再移除元数据；仅本地 Demo |
| M2 Brief 与人工审核 | 规则/Qwen 结构化需求、LangGraph SQLite Checkpoint、规格人工确认 | 本地单进程工作流，没有登录和生产并发隔离 |
| M2 执行可靠性 | SQLite 幂等键、事件日志、已完成复用、结果未知保护 | 不等于跨系统 Exactly-Once；不传幂等键的旧接口仍可重复 |
| M3 Acceptance Gate | 文案/OCR、品牌词表、价格区域位置与冲突检查、报告 SHA-256、可视化诊断 PNG | OCR 缺失与不确定必须标 `UNVERIFIED`，不可冒充 PASS |
| M3 评测 | 合成 OCR 样本、20 张程序渲染 PNG、FP/FN/F1、逐版人工标注及 CSV | 合成数据不是真实业务精度；人工标签尚无独立审计 |
| M4a/M4b 多尺寸派生 | 三种安全缩放尺寸；新增 4:5 原生 SKU/标题/价格/CTA 排版 | 原生布局目前仅 4:5；其它仍安全缩放，所有派生图需人工验收 |
| 真实 OCR 证据 | 独立 GitHub Linux CPU PaddleOCR 3.3.0 成功识别一张程序海报（7 行/7 坐标框） | [Actions 证据](https://github.com/musuntana/poster-agent/actions/runs/39586650764)；Mac M1 和真实客户图片尚未实测 |

**M4a 多尺寸安全派生已实现：** 从已保存的 1024×1024 PNG 离线导出 1080×1350（4:5）、1080×1920（9:16）、1200×675（16:9）。原海报整体等比例缩放、居中，空白部分模糊延展；不会重调 AI 或裁切中央内容。这**不是自适应重排**，新尺寸必须人工重新验收。详见 [M4a 说明](docs/m4a-channel-exports.md)。

**M4b 已新增原生 4:5 布局：** 1080×1350，基于已保存的商品素材、Logo、背景和选定版本规格单独重排，不再次调用收费模型。旧三种比例安全缩放依然可选；尚无针对原生 4:5 的独立 OCR/人工验收基准。详见 [M4b 开发说明](docs/m4b-native-feed.md)。

**M4c 已新增独立 4:5 验收：** 可选对原生 1080×1350 PNG 执行真实 PaddleOCR，核查价格位置、标题和 CTA，并将报告绑定到当前 SKU、背景、Spec 与导出 PNG 的 SHA-256；默认无 OCR 时明确未验证，不继承方形版验收结果。详见 [M4c 说明](docs/m4c-native-acceptance.md)。

**M5c 已新增原生 4:5 人工质量标注：** 在独立的 1080×1350 导出上审查商品完整度、可读性、构图及价格、标题、CTA。审核与原方图标签隔离，并绑定具体 PNG、源素材和规格 SHA-256；可导出专用 CSV，无法判断的标签不会被算作正确。详见 [M5c 说明](docs/m5c-native-human-review.md)。

**M5d 已新增 4:5 原生报告评测：** 对已有原生人工 CSV 与对应成功 PaddleOCR 报告做逐字段 TP/FP/FN/TN、弃判和覆盖率统计；活动价/原价必须同时核对文字及专属坐标，SKU 完整度、构图等仅做人工统计，不伪装为自动模型精度。没有真实报告则拒绝评分。参见 [M5d 评测说明](docs/m5d-native-evaluation.md)。

**M5h 新增本地验收队列：** 在网页右侧点击「刷新本地验收队列」，即可按 M5e 状态查看每张海报最新版本，点击「打开审核」直接使用既有 4:5 预览、M5c 人工审核和 M4c 单独验收。刷新仅读取本地数据，既不运行 OCR，也不自动放行。详见 [M5h 审核队列说明](docs/m5h-local-review-queue.md)。

**M5g 新增真实商品批量导入：** CSV 列出 1–100 个本地商品图，默认完整预检，显式 `--apply` 才使用原有 FastAPI 安全上传、PosterSpec 和 SQLite 幂等账本生成海报。固定本地免费背景，支持中断后继续，保留原商品图哈希及 SKU→海报 ID 关系；不执行 OCR，不虚构真实商品评测成绩。参见 [M5g 本地批量导入](docs/m5g-local-batch-import.md)。

**M5f 已加固试验证据：** 原生 OCR 报告记录运行适配器、PaddleOCR/PaddlePaddle 包版本及架构（**仅诊断，不可当作真实模型执行证明**）；每次盘点或评分会把旧指标归档到 `history/`，通过 `latest_score_status` 避免旧 F1 被当成最新结果。参见 [M5f 试验防混淆说明](docs/m5f-evidence-run-safety.md)。

**M5e 已提供本地评测盘点：** 使用 `uv run --locked python -m scripts.native_trial --reviewer local-operator` 自动找出未审核、缺真实 OCR 或报告过期的原生 4:5 版本；显式加入 `--score` 才会复用 M5d 生成指标和完整 CSV 标签快照。会提示不同商品素材数量不足，避免多个改价版本被误当成独立商品。参见 [M5e 试验手册](docs/m5e-native-trial.md)。

**M5k 加固未完成批次检测：** M5g 导入账本写入预期 SKU 清单与最终完成状态，M5j 拒绝将中断后仅完成的部分 SKU 当作完整评测批次。旧 M5g 账本需幂等续跑补写完成状态，不增加 OCR/AI 调用。

**M5j 已支持按导入批次独立评分：** 使用 `uv run --locked python -m scripts.native_trial --reviewer local-operator --batch-journal data/eval_runs/m5g_import/batch_import_results.json` 仅盘点本批次最新 SKU 版本；全批 READY 后显式 `--score`，结果附带批次账本 SHA 和 SKU 清单。缺一个样本也不会自动缩小分母。详见 [M5j 批次独立评测](docs/m5j-batch-evaluation.md)。

**M6 真实 SKU 评测准备：** 已提供 M5g 生成清单、授权治理表、人工审核工作表和本地证据门禁；`scripts.prepare_m6_review` 可从完整 M5g 批次离线导出方图及原生 4:5 图，并生成全部为 `UNVERIFIED` 的本地审核草稿，`scripts.validate_m6_cohort` 则检查版本、图片/规格哈希和 M5j 证据范围。两者都不运行真实 OCR、不调用收费模型、不替代人工验收。详见 [M6 评测协议](docs/m6-real-sku-review-rubric.md)。

**公开目录 SKU Smoke Test（不是业务质量评测）：** 可从 Amazon Berkeley Objects（CC BY 4.0）数据集选取 20 张目录商品图，在本地跑通批量导入与原生 4:5 海报导出。价格为确定性合成值；该流程不抓取淘宝、拼多多或京东，不执行真实 OCR 或人工评分，也不能证明真实价格准确性。操作步骤与限制见 [公开 SKU Smoke Test](docs/public-abo-sku-smoke-test.md)。

**M5i 已支持按导入批次筛选审核队列：** 在现有 M5h 页面选择 M5g 的 `batch_import_results.json`，浏览器仅在内存中筛选对应 SKU，不上传该文件，也不改变全量 M5e 评分样本范围。详见 [M5i 本地批次筛选](docs/m5i-batch-review-scope.md)。

所有自动化质量检查仍需人工最终审核；尚未实现 VLM 修复、SKU 视觉保真验证或所有渠道原生响应式排版。

## 常用操作

**无需云端 Key：** 网页选择本地背景，生成、修改并导出；`uv run --locked python -m scripts.generate_demo` 可检查本地字体与渲染。

**可选 Qwen 真实背景：** 在 `.env` 设置 `DASHSCOPE_API_KEY`、对应区域/Workspace 的 `QWEN_IMAGE_ENDPOINT`，以及 `QWEN_IMAGE_MODEL`。确保 Key、服务地域和模型互相匹配；勿将 `.env` 提交。详细变量模板见 [`.env.example`](.env.example)。Qwen 结构化 Brief 可按模板配置 `QWEN_CHAT_ENDPOINT`。

**OCR 环境诊断及真实单图推理（可选）：** 先按 [uv + OCR 安装说明](docs/uv-workflow.md#ocr-重依赖按需安装) 执行 `uv sync --locked --extra ocr`，再安装对应平台的 PaddlePaddle 推理引擎。

```bash
uv run --no-sync python -m scripts.ocr_probe
uv run --locked python -m scripts.generate_demo
uv run --no-sync python -m scripts.ocr_probe --infer --image data/examples/sample_preview.png
```

`--infer` 需额外安装真实 PaddlePaddle / PaddleOCR，使用 `uv run --no-sync` 保留平台专属推理包；安装见 [uv 工作流](docs/uv-workflow.md) 和 [本地 OCR 说明](docs/m3d-ocr-runtime.md)。Linux CI 的成功记录**不能当成 Mac 本机成功记录**。

**质量评测：** `uv run --locked python -m scripts.evaluate_acceptance` 重放合成文本测试；`uv run --locked python -m scripts.run_image_benchmark --ocr none` 生成 20 张 PNG 并标注 OCR 未运行；`--ocr paddle` 才调用真实本地 OCR。对实际海报请在网页中先人工审核并导出 CSV，再运行 `uv run --no-sync python -m scripts.evaluate_real_reports --annotations data/my-real-labels.csv --ocr-mode paddle`（要求已有与图片哈希一致的真实 OCR 报告）。

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


## License

The project license is Apache License 2.0 (`Apache-2.0`). See [`LICENSE`](LICENSE). Third-party dependencies, fonts, images, datasets, and other assets may have separate terms; review their provenance and permissions before redistribution.
