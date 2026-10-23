# Poster Agent 文档导航

以 [项目 README](../README.md) 为**当前功能、启动方式与阶段状态的唯一入口**。这里保留需要查阅的专项文档；早期阶段记录已移入 [archive](archive/README.md)，不应作为最新行为说明。

## 当前工程手册

| 内容 | 文档 | 适用范围 |
| --- | --- | --- |
| 平台与配置入口 | [根 README](../README.md) | 当前运行、限制、模块、下一步 |
| 本地启动与网络暴露边界 | [local-only-deployment.md](local-only-deployment.md) | 仅回环 IP 启动器、禁止非本机暴露、已知绕过边界 |
| uv 开发环境与锁依赖 | [uv-workflow.md](uv-workflow.md) | Python 3.12 / pyproject.toml / uv.lock、开发组、按需 OCR |
| 付费生图幂等与恢复 | [paid-call-idempotency.md](paid-call-idempotency.md) | AI 模式强制幂等键、重复请求冲突、UNKNOWN 结果处理与单进程边界 |
| 公开仓库与开源发布准备（M5k） | [open-source-release.md](open-source-release.md) | Apache-2.0 已选定；源码/素材授权与历史审查仍待完成 |
| GitHub Actions 节省额度 | [ci-budget.md](ci-budget.md) | PR Ready 一次 quick；full 与真实 OCR 手动触发 |
| 人工标注和标签导出 | [m3c-human-review.md](m3c-human-review.md) | PNG 逐版审核、人工标注、导出 CSV |
| Mac / Linux 单图 OCR 诊断 | [m3d-ocr-runtime.md](m3d-ocr-runtime.md) | 本地 OCR 环境、诊断与结果证据 |
| 价格区域校验 | [m3e-ocr-price-position.md](m3e-ocr-price-position.md) | 1024×1024 固定模板的启发式区域验证 |
| OCR 识别框叠加 | [m3f-ocr-visual-diagnostics.md](m3f-ocr-visual-diagnostics.md) | 图像证据可视化与导出 |
| GitHub 真正 OCR 推理 | [m3g-real-ocr-actions.md](m3g-real-ocr-actions.md) | Linux CPU 手动模型验证及实验产物 |
| 多渠道安全派生 | [m4a-channel-exports.md](m4a-channel-exports.md) | 4:5、9:16、16:9 PNG，无裁内容；非响应式重新排版 |
| 4:5 原生排版 | [m4b-native-feed.md](m4b-native-feed.md) | 1080×1350 分别布局 SKU/文案/价格/CTA；无收费模型调用 |
| 4:5 原生图独立验收 | [m4c-native-acceptance.md](m4c-native-acceptance.md) | 专用 OCR/价格坐标、源资产与导出哈希绑定，不自动放行 |
| 商品图与 Logo 上传保护 | [m5a-upload-safety.md](m5a-upload-safety.md) | 解码前像素/尺寸限制、EXIF 旋正、元数据清理 |
| 商品图 ICC → sRGB 处理 | [m5b-color-management.md](m5b-color-management.md) | Pillow ImageCms 色彩转换、Alpha 保留、无效 ICC 拒绝 |
| 4:5 原生人工质量标注 | [m5c-native-human-review.md](m5c-native-human-review.md) | SKU、可读性、构图、文案的版本级独立审核/CSV |
| 4:5 原生 OCR × 人工评测 | [m5d-native-evaluation.md](m5d-native-evaluation.md) | 独立报告/图片哈希核验、空间价格规则与弃判指标 |
| 一键盘点原生 4:5 试验 | [m5e-native-trial.md](m5e-native-trial.md) | 审核 / OCR 缺口清单、不同 SKU 计数、显式评分与标签快照 |
| OCR 环境证据与旧结果归档 | [m5f-evidence-run-safety.md](m5f-evidence-run-safety.md) | OCR 包版本/架构记录（非防伪）；评分产物归档与最新运行状态 |
| 真实商品 CSV 安全导入 | [m5g-local-batch-import.md](m5g-local-batch-import.md) | 默认预检、显式 --apply、本地免费背景、执行账本与中断恢复 |
| 本地原生 4:5 审核队列 | [m5h-local-review-queue.md](m5h-local-review-queue.md) | 最新版状态/标题、打开已有预览与人工审核；不运行 OCR |
| 按 M5g 导入批次筛选审核队列 | [m5i-batch-review-scope.md](m5i-batch-review-scope.md) | 浏览器本地读取导入账本，SKU 级筛选已存在的审核队列 |
| M5g 批次独立评测 | [m5j-batch-evaluation.md](m5j-batch-evaluation.md) | 指定导入账本，逐 SKU 最新版本评分，禁止外部海报污染分母 |
| M6 真实 SKU 评测协议 | [m6-real-sku-review-rubric.md](m6-real-sku-review-rubric.md) | cohort 模板、授权治理、逐输出人工验收与本地证据门禁 |

## 证据边界

- **模拟测试**：pytest、手工编写的 OCR 文本样本及程序渲染的 PNG。不能宣称为真实客户准确率。
- **真实模型执行**：Linux CPU PaddleOCR 3.3.0 已完成单图 7 行/7 框的推理验证（[Actions run](https://github.com/musuntana/poster-agent/actions/runs/39586650764)）。20 图真实 OCR 需要手动触发；Mac M1 的本地成功状态未核实。
- **人工数据**：已实现界面与 CSV 评分器，但没有独立审计的真实业务标注集。不要把合成集分数冒充产品准确率。
- **安全**：仍为单机、无鉴权版本；不得部署公开互联网。真实 Qwen API 需要自己的 Key，并可能产生费用。

## 维护约定

新文档优先更新当前指南而不是继续增加 M3h/M3i 的碎片化阶段文件；若内容仅记录一次实验或里程碑，请放入 `docs/archive/`，并在当前索引中说明证据时效。保留 API、测试、复现实验的关键记录，但移除已经被后续版本覆盖的实施建议。
