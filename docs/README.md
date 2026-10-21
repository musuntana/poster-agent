# Poster Agent 文档导航

以 [项目 README](../README.md) 为**当前功能、启动方式与阶段状态的唯一入口**。这里保留需要查阅的专项文档；早期阶段记录已移入 [archive](archive/README.md)，不应作为最新行为说明。

## 当前工程手册

| 内容 | 文档 | 适用范围 |
| --- | --- | --- |
| 平台与配置入口 | [根 README](../README.md) | 当前运行、限制、模块、下一步 |
| uv 开发环境与锁依赖 | [uv-workflow.md](uv-workflow.md) | Python 3.12 / pyproject.toml / uv.lock、开发组、按需 OCR |
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

## 证据边界

- **模拟测试**：pytest、手工编写的 OCR 文本样本及程序渲染的 PNG。不能宣称为真实客户准确率。
- **真实模型执行**：Linux CPU PaddleOCR 3.3.0 已完成单图 7 行/7 框的推理验证（[Actions run](https://github.com/musuntana/poster-agent/actions/runs/39586650764)）。20 图真实 OCR 需要手动触发；Mac M1 的本地成功状态未核实。
- **人工数据**：已实现界面与 CSV 评分器，但没有独立审计的真实业务标注集。不要把合成集分数冒充产品准确率。
- **安全**：仍为单机、无鉴权版本；不得部署公开互联网。真实 Qwen API 需要自己的 Key，并可能产生费用。

## 维护约定

新文档优先更新当前指南而不是继续增加 M3h/M3i 的碎片化阶段文件；若内容仅记录一次实验或里程碑，请放入 `docs/archive/`，并在当前索引中说明证据时效。保留 API、测试、复现实验的关键记录，但移除已经被后续版本覆盖的实施建议。
