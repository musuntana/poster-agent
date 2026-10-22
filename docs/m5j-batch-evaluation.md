# M5j — M5g 批次级、独立原生 4:5 评分

M5i 让网页只展示某个 M5g 批次的 SKU，但此前命令 `scripts.native_trial --score` **仍扫描整套本地海报库**。如果包含旧 Demo、历史改价版本或另一次客户试验的标签，分母和误报/漏报会被污染。

M5j 使用现有 M5c 人工 CSV 导出与 M5d 评分实现，在原命令上增添**显式可选**的 `--batch-journal` 参数，不重复开发指标数学或模型引擎。

## 先确认批次已导入

M5g `--apply` 后会生成：

```text
data/eval_runs/m5g_import/batch_import_results.json
```

不能使用只有 `batch_preflight.json` 的批次：其尚未创建任何海报。

## 一键检查指定批次（不评分、不运行 OCR）

```bash
uv run --locked python -m scripts.native_trial \
  --reviewer local-operator \
  --batch-journal data/eval_runs/m5g_import/batch_import_results.json \
  --output-dir data/eval_runs/m5j_batch_001
```

`native_trial_manifest.csv` 逐 SKU 列出当前海报版本、审核状态、缺口和动作；`native_trial_readiness.json` 包含源 CSV manifest SHA-256、实际导入账本文件 SHA-256、SKU 数、已完成的版本数、不同原始图片数量和是否 `can_score`。

规则如下：

- 仅使用批次导入结果中 1–100 个合法 `sku_id → poster_id`；校验 `m5g-1`、`mode=apply`、记录摘要、幂等键、无重复海报 ID；拒绝伪造/格式错误的批次格式，但本地 JSON **不是签名来源证明**。
- 每个 SKU **只选海报的当前最新版本**。如原 v1 改价后产生 v2，需要对 v2 重新人工审核、重新运行原生 OCR，不能借用 v1 的结果。
- 缺少的海报 ID、源数据损坏、非 local 背景等明确标记为 `SOURCE_INVALID`，**不会从分母中悄悄剔除**。
- 任意一个批次 SKU 没有确定性人工标签、缺少有效原生 OCR 或证据过期，则整个批次 `can_score=false`；有 `unsure` 的字段仍可弃判，但必须至少有可评测的 OCR 文字字段。
- 批次**以 SKU 数而不是改价版本数计入样本单位**。不同源图片 SHA-256 数量单列，仍不代表不同商品类别或统计独立性。

## 显式评分（整批 READY 后）

```bash
uv run --locked python -m scripts.native_trial \
  --reviewer local-operator \
  --batch-journal data/eval_runs/m5g_import/batch_import_results.json \
  --output-dir data/eval_runs/m5j_batch_001 \
  --score
```

M5j 让 M5c CSV 导出函数**只读取这批 SKU 的所选最新版本**，然后交给现有 M5d 哈希校验与 TP/FP/FN/TN、Precision/Recall/F1、弃判率计算。批次外其他海报即使存在坏掉的旧人工标注，也不会污染本次输出。

`native_real_summary.json` 明确包含：

- `scope_mode=batch_latest_per_sku`
- `source_batch_journal_sha256`
- `source_manifest_sha256`
- `batch_sku_count`
- `batch_unique_raw_product_sha256_count`
- `scored_sku_to_poster_id`
- `cohort_requires_all_skus_ready=true`

并继续保存 `native_trial_labels.csv`（本次实际评分人工标签快照）及 `native_real_cases.csv`（逐项错误详情）。M5f 旧评分归档、失败不暴露半成品结果的机制继续使用。

**重要：没有传 `--batch-journal` 时行为保持 M5e 原样**：会扫描该审核员在本地全部海报和历史版本。不要把全量分数命名为某个批次的准确率。

## 证据限制

本地导入账本是操作员可编辑文件，文件名、SHA-256 和幂等字段能帮助发现非预期变化，但不能证明真实商品授权、独立商品类别，也不能独立证明 OCR 确实由 PaddleOCR 模型推理得到。既有 M5f `attested=false` 仍然适用。

CI 使用小型 Pillow 合成图片及模拟 OCR，不包含真实商用样本或真实 OCR；不要从 CI 的成功测试数推断商业投放准确率。应用仍然没有鉴权和可信审计，真实客户样本只能在受信环境中处理。

## 复现性与回归

```bash
uv run --locked python -m pytest -q \
  tests/test_native_batch_scope.py \
  tests/test_native_trial.py \
  tests/test_real_sku_batch.py
```

测试包括：有效及无效导入账本、额外历史 Demo 不混入、其中一个 SKU 缺审核不得评分、最新 v2 代替 v1、移除某个批次 ID 时保留失败项、批次外过期人工记录不影响本批次评分。
