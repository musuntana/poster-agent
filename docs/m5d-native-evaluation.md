# M5d — 原生 4:5 的人工标签 × OCR 真实报告评测

本阶段**不生成海报、不调用 OCR、不标注图片**。复用 M5c 已保存的真实 1080×1350 图片人工审阅标签、M4c 已保存的 `ocr_mode=paddle` 原生图片验收报告，以及项目现有 `evals.benchmark.metrics` 混淆矩阵统计实现。目标是将“有了独立证据”推进到“能够审计和复算的逐字段评测”。

## 对真实商品图片执行

1. 在本地启动产品，通过网页上传**实际商品图片**，选择「4:5 原生排版」，人工检查所有字段并保存 M5c 标注。
2. 确认本机已经正确安装 PaddleOCR 3.x / PaddlePaddle，选择「真实 PaddleOCR」运行**原生 4:5 的独立 Acceptance Gate**。看到 `ocr_status=OK` 后保存报告。注意：这只能证明报告声明执行成功，不等于独立证明模型来源可信。
3. 从网页下载**原生 4:5** 的审核员 CSV（不能使用原方图 CSV）。
4. 执行：

```bash
uv run --no-sync python -m scripts.evaluate_native_real_reports \
  --annotations data/my-native-4x5-labels.csv \
  --output-dir data/eval_runs/m5d_native_real
```

本脚本没有任何 AI API 调用；`--no-sync` 可兼容先前单独安装到 uv 环境中的 PaddlePaddle 引擎，但评分过程自身不调用 OCR，也不需要启动模型。

生成结果：

- `native_real_summary.json`：字段混淆矩阵、弃判比例、按字段指标、人工视觉缺陷数量、CSV 与各报告 SHA-256；
- `native_real_cases.csv`：逐字段 OCR 检测与人工标签的核对结果，方便逐项检查误报、漏报。

**必须有有效的原生 PaddleOCR 成功报告**。没有报告、只有规则检查 `none`、`ocr_status=UNAVAILABLE/ERROR`、图片和报告摘要过期、或 CSV 来自方图时，脚本直接失败，**不会生成伪造的模型准确率**。

## 字段匹配规则（正类 = 人工认为“有缺陷”）

| 人工字段 | OCR 预测逻辑 |
| --- | --- |
| 标题、CTA、副标题 | 对应 `ocr_<field>` 的 PASS/FAIL/UNVERIFIED |
| 活动价 | `ocr_price` **并且** `ocr_price_position` |
| 原价 | `ocr_original_price` **并且** `ocr_original_price_position` |
| SKU 完整、整体可读性、构图 | 只统计人工 correct/defective，无自动识别预测，不计算 OCR 精度 |

价格只有在文本**与**坐标均为 PASS 才 PASS；任一 FAIL 则 FAIL；其余情况 UNVERIFIED，进入弃判统计。缺坐标不允许变为“正确价格”。这正是原始方图 M3b 评测只检查文本时留下的重要差别。

混淆矩阵按现有项目语义：

- TP：人工发现缺陷，检测器报告 FAIL；
- FP：人工确认正确，检测器误报 FAIL；
- FN：人工发现缺陷，检测器却报告 PASS；
- TN：人工确认正确，检测器报告 PASS；
- ABSTAIN_DEFECT / ABSTAIN_GOOD：检测器无法验证，**不归为 FN/TN**。查看 `recall_including_abstentions` 与 `coverage`。
- 人工的 `unsure` 字段由 M5c CSV 导出时剔除，不能当作无缺陷标签。

指标是**字段级**、在人工已给出确定标签的样本上计算；未将不同审核员拼成虚假的共识。样本少于 20 个海报版本时，`small_sample_warning=true`。即使样本较多，也不能未经独立核对就宣称商用或跨商品泛化准确率。

## 证据绑定与失败策略

- `variant_id` 必须是 `feed_4x5_native`；必须包含原生 PNG SHA-256 与原方图摘要；
- 每个版本调用 M4c 的 `verified_report`，复核最新规格、商品/Logo/背景素材和**实际重渲染原生 4:5 PNG** 的 SHA；
- 检查原生 `1080×1350`、`ocr_mode=paddle`、`ocr_status=OK`，每个必需字段检查项不得缺失或重复；
- 不允许同一版本和字段重复标注，不能混用审核员；
- 对整批样本先校验再写输出；只要任何一个版本的证据无效，整批评分失败，不会输出部分指标；
- 报告摘要文件中还保存输入 CSV 的 SHA 和每份原生 OCR 报告的 SHA，方便后续复算。

## 验证

```bash
uv run --locked python -m pytest -q tests/test_native_real_reports.py
```

自动化测试使用**真实 Pillow 生成的原生 PNG + 模拟 OCR 检测结果**，只验证评测契约、弃判/位置计算及哈希限制，**不代表已对任何客户数据运行 PaddleOCR**。真实样本集尚未提供；这一里程碑实现的是运行可信实验所需的评分链路，而非真实业务准确率结论。

项目仍为单机、无登录和权限边界，CSV 中的审核员代号不能证明身份。
