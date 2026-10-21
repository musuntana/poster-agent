# M5e — 一条命令盘点原生 4:5 评测准备度

M5c 保存人工标注，M4c 保存原生 4:5 OCR 报告，M5d 能比较二者并算 TP/FP/FN/TN。但过去需要先手工导出 CSV，再检查哪些图片缺 OCR，最后执行 M5d。M5e 把这些步骤组织为**本地试验盘点 + 显式评分**，最大程度复用现有实现，不新增模型服务或收费调用。

## 第一步：免费、只读盘点（默认）

```bash
uv run --locked python -m scripts.native_trial \
  --reviewer local-operator \
  --output-dir data/eval_runs/m5e_trial
```

仅分析本地 `POSTER_DATA_DIR` 中的已保存海报版本。不会执行 OCR，不生成商品海报，也不会自动评分或声称模型准确率。

生成：
- `native_trial_readiness.json` — 每种状态数量、可评测版本数量、**不同原始商品图 SHA-256 数量**、是否达到建议的 20 张不同商品素材，以及能否启动评分。
- `native_trial_manifest.csv` — 每个海报版本、状态、待处理动作、标签数、商品和原生 PNG 摘要。

### 状态含义

| 状态 | 含义 | 如何处理 |
| --- | --- | --- |
| `NEEDS_REVIEW` | 指定审核员还没有原生 4:5 标注 | 浏览器中打开真实 4:5 导出并逐项标注 |
| `REVIEW_UNSURE` | 所有字段均是 `unsure` | 继续人工判断或保留弃判，不纳入评分 |
| `OCR_MISSING` | 有确定性人工标签，但无原生 PaddleOCR 报告 | 本地手动触发 M4c `ocr_mode=paddle` |
| `OCR_NOT_OK` | OCR 报告声明未成功执行 | 检查引擎和原生图片，再次运行 |
| `OCR_STALE` | 规格、素材或原生图与报告摘要不一致 | 重新核查并执行 OCR |
| `OCR_CHECKS_INVALID` | 缺少字段或活动价/原价空间检查 | 检查报告内容或重跑 M4c |
| `REVIEW_STALE` / `SOURCE_INVALID` | 人工标注过期或原始素材不可用 | 重新核验源数据后再评测 |
| `READY` | 已有对应人工标签及有效的原生 OCR 报告 | 可纳入 M5d 离线评分 |

同一商品图的 v1/v2 会被统计为两个版本，但**只算一种源商品图片**；推荐不少于 20 种不同商品素材以减少重复商品导致的样本数虚增。这不等于 20 种独立商品类别或足够的统计代表性。

## 第二步：显式执行评分

只有当对应审核员的所有**有确定性标签**的版本都准备好成功的原生 PaddleOCR 报告、而且至少存在可评测的文字字段时，才运行：

```bash
uv run --locked python -m scripts.native_trial \
  --reviewer local-operator \
  --output-dir data/eval_runs/m5e_trial \
  --score
```

`--score` 直接调用已有的 M5c 人工标签导出和 M5d 评分器，不重写混淆矩阵逻辑。除盘点文件外，还生成：

- `native_trial_labels.csv` — **本次评分实际使用的标签快照**；后续新的人工标注不会改变这一批评测输入。JSON 中的 `label_csv_sha256` 对应此文件；
- `native_real_summary.json` — M5d 的评测指标、按字段混淆矩阵、缺陷统计与源报告摘要；
- `native_real_cases.csv` — 逐项错误与弃判明细。

缺少 OCR、哈希过期、尚未人工审核或者只有 `unsure` 标签时，不会凭空生成准确率。若失败，仍会保存**盘点与缺口清单**，用于继续完成尚缺的步骤。

## 可信边界

- 本命令只读取已保存的本地资料，**不会执行 PaddleOCR，也不会请求 Qwen**；只能核对已有报告的声明与素材 SHA，并**不能从报告中独立证明它一定来自真实 PaddleOCR**。
- 自动化测试使用实际 Pillow 海报和模拟 OCR 返回值，只验证接口/证据契约，不代表线上准确率。
- 只有一个审核员代号参与一次实验；这不构成专家身份认证或标注一致性。
- 评测数据、标签和报告中可能含客户商品内容；结果默认放在被 Git 忽略的本地 `data/eval_runs`，不要把实际商品素材或审核日志推送到公开仓库。
- 目前输出按**版本**计算字段级指标，只有 `ready_distinct_product_sha256_count` 用于提示同一商品的版本重复。不同商品 SKU 和数据集划分仍需人工检查。

## 测试

```bash
uv run --locked python -m pytest -q tests/test_native_trial.py
```

覆盖无样本盘点、未审/缺 OCR、过期报告、OCR 结果异常、缺价格检查、多个版本共享一种 SKU、审核员隔离、全 `unsure`、标签 CSV 快照的 SHA 校验和有证据的端到端评分链路。CI 不安装真实 OCR。
