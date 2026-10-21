# M3b — OCR 错误诊断、评测指标与真实人工标注协议

## 能力边界（必须先读）

当前附带的 20 条样本是 **handcrafted synthetic contract tests（手工构造的合成测试）**，不是 20 张真实的海报，也不是 PaddleOCR 实测数据。场景故意制造 OCR 误报/漏报，作用是验证 Acceptance Gate 的规则及评测程序是否正确，**不能把得出的 Precision、Recall、F1 当成生产算法准确率，也不能写入简历当成已实现的真实业务指标**。

此阶段同时新增“真实海报验收报告 + 人工标签 CSV”的评分工具，但必须先由你亲自核对实际 PNG、完成标注、运行真实 OCR、生成匹配报告后才会有真实指标。工具不会凭空创建标签。

## A. 运行第一批 20 条合成场景

```bash
cd ~/repos/poster-agent
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m scripts.evaluate_acceptance
```

输出：
- `data/eval_runs/m3b/benchmark_summary.json`：综合字段级/海报案例级混淆矩阵与 Precision、Recall、F1、弃判比例、分类统计。
- `data/eval_runs/m3b/benchmark_cases.csv`：逐条记录 ground truth、模型规则预测、错误类别。
- 输入文件：`evals/datasets/m3b_synthetic_ocr_v1.json`（20 条）。

每条样本包含：
- `spec`：统一的期望 `PosterSpec`，配置于顶层 `base_spec`；
- `visible_text`：手工设定的“假设真实画面中的文字”，用于读者理解场景；**不传给检测器**；
- `ocr_lines`：人为设计的 OCR 返回结果（不是实际 PaddleOCR 识别）；
- `truth_defects`：手工判定哪些字段在画面中有错误；
- `category`：错误原因类别（如 missing_text / false_alarm / missed_defect）。

## B. 看懂四种错误与弃判

在本项目中，**正类 = 有真实文字错误（defective）**，不是“识别成功”：

| 判定 | 含义 |
|---|---|
| TP | 图像文字确有错误，Gate 判断 FAIL |
| FP | 图像文字正确，但 Gate 判断 FAIL（误报） |
| FN | 图像文字错误，但 Gate 判断 PASS（漏检） |
| TN | 图像文字正确，Gate 判断 PASS |
| ABSTAIN_GOOD | 图像文字正确，但是未验收（UNVERIFIED） |
| ABSTAIN_DEFECT | 图像文字错误，但是未验收（UNVERIFIED） |

由于 `UNVERIFIED` 不等于 PASS，报告同时给出：
- `coverage`：`(TP+FP+FN+TN)/全部标签数`；
- `precision_on_assessed`：`TP/(TP+FP)`；
- `recall_on_assessed`：`TP/(TP+FN)`（只包括有判定的样本）；
- `f1_on_assessed`：`2TP/(2TP+FP+FN)`；
- `recall_including_abstentions`：`TP/(TP+FN+ABSTAIN_DEFECT)`，防止大量弃判让召回率看起来虚高；
- `false_alarm_rate_on_good_assessed`：`FP/(FP+TN)`。

无分母时输出 `null`，不错误地宣称 0%。

字段级、案例级**口径不同**。案例级只汇总此套 benchmark 的目标文字字段，不包括真实商品外观、版式和广告合规。

## C. 用真实海报创建人工标注评测

1. 用 Web 页面生成若干张真实 PNG，逐张审核标题、价格、CTA。
2. 选择 PaddleOCR 真实运行模式，生成 `vX.acceptance.paddle.json`。OCR 不可用就保持 `UNVERIFIED`，不能假装已经识别。
3. 复制 `evals/templates/real_annotations_template.csv`，自己填写**实际 PNG**的审核结论：

```csv
poster_id,version,field,defective,image_sha256,reviewer
```

每行一个字段：`field` 仅允许 `title/price/cta/subtitle/original_price`，`defective` 只填写 `true` 或 `false`；`reviewer` 是你的评审代号（不需要真实姓名）；`image_sha256` 从该版本报告提取；**必须亲眼检查 PNG，再决定标签**。

4. 执行：

```bash
python -m scripts.evaluate_real_reports \
  --annotations data/my-real-labels.csv \
  --ocr-mode paddle \
  --output-dir data/eval_runs/m3b_real
```

脚本校验图片与报告 SHA-256、版本以及是否存在指定模式的报告；如果报告失效或漏标，不会静默跳过。结果分别保存到 `real_summary.json` 和 `real_cases.csv`。

**测试框架不能自动证明人工标注正确**。至少由一人标注，最好抽样二次复核；每个品牌、商品类别、背景类型分别采样，后续评测应固定测试集，避免一边调规则一边用同一批样本报告最终效果。别把预览中看到的 Spec 当成图片中文字的真值。

## D. 本阶段发现并修复的错误

M3a 只阻止了 `¥1990` 错误匹配 `¥199`，但 `¥199.90` 会被误认为 `¥199`。M3b 为价格匹配增加小数后缀保护（不再将末尾的小数视为完全相同的价格）；把这一失败模式纳入基准样本 S06 和回归测试。

已知不足：
- 未优化 OCR 跨行文字合并、数字分组、价格归属（活动价与原价可能误匹配）。
- OCR 识别分数尚未做校准，0.4 是启发式阈值而非科学结论。
- 当前 20 条是合成样本，不足以量化真实模型在真实场景下的效果。
- 无高质量 SKU 保真度基准，没有视觉/布局自动判断。
- 无人工审核界面的标注与审计；现在通过 CSV 人工整理。
- OCR 模型与运行平台的本地推理结果仍取决于安装状况；CI 只跑合成场景。

下一阶段应该**优先收集真实的 20 张海报并做实际 OCR、人审和错例归因**，再决定是否调整阈值、增加行聚合或引入 VLM-as-Judge。
