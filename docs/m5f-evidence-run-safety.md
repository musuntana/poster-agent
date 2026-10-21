# M5f — OCR 运行证据与试验结果防混淆

## 为什么要做

M5e 已能盘点试验、保存标签快照并调用 M5d 评分，但有两个可信度缺口：

1. 原生 4:5 OCR 报告只有 `ocr_mode=paddle, ocr_status=OK`；**模拟 OCR 同样可以构造 OK**。目前没有诊断性的包版本、运行架构信息。
2. 如果复用同一个 `--output-dir`，新一轮只盘点或评分因证据不足而拒绝，之前成功的 `native_real_summary.json` / CSV 可能**留在根目录被误当成最新结论**。

M5f 不引入新模型或重写 M5d 指标；主要保护原有试验数据的解释边界。

## OCR 适配器运行记录（仅诊断，不是防伪）

实际的 `app.ocr_provider.recognize()` 仍返回可当作 `list[dict]` 使用的 OCR 文本行，但附带：

- `source=paddleocr_adapter`
- `paddleocr_package_version`、`paddlepaddle_package_version`（未安装可为 null）
- `python_version`、`machine`、`line_count`
- `attested=false`

原生 4:5 Acceptance Gate 将其写入 `ocr_runtime`，并显示 `ocr_runtime_evidence=RECORDED`。旧报告或 mock 返回普通 `list` 时显式为 `UNRECORDED`。

**重要：** 这些版本和平台字段是进程自己声明的普通 JSON；不是可信硬件签名、第三方模型调用证明，也不能可靠地区分恶意伪造或有意注入的模拟结果。M5e 与 M5d 的汇总新增 `ocr_runtime_unrecorded_versions` 等计数，并**始终**保留 `ocr_runtime_attested=false`。单靠 `OK` 或 `RECORDED` 不能宣称真实客户图片的模型精度；必须保留执行日志、真实素材授权/来源和人工核查。

## 防止旧成绩误作新成绩

继续使用原命令：

```bash
# 默认只盘点，不会执行 OCR 或评分
uv run --locked python -m scripts.native_trial \
  --reviewer local-operator \
  --output-dir data/eval_runs/m5e_trial

# 只有确有有效 OCR+人工标签才显式评分
uv run --locked python -m scripts.native_trial \
  --reviewer local-operator \
  --output-dir data/eval_runs/m5e_trial --score
```

现在每次运行都会生成新的 `trial_run_id`、UTC 时间及 `latest_score_status`：

- `AUDIT_ONLY`：本次只盘点，当前根目录无评分产物；
- `BLOCKED`：请求了评分，但当前标签或 OCR 证据未达到要求；
- `FAILED`：证据原先满足条件，但后续导出、验证或写入发生错误；
- `SCORED`：本次评分已成功产生完整结果。

**以前的评分结果不被删除**：如果根目录存在旧 `native_real_summary.json`、`native_real_cases.csv` 或 `native_trial_labels.csv`，会先移动到 `history/<UTC日期>-<随机ID>/`。新盘点或失败时根目录不再残留旧成绩。完成评分时先在临时文件夹准备全部结果，成功后才将结果移到最新输出位置；异常时不会发布部分成功的指标。

`native_trial_readiness.json` 的 `previous_score_archive` 提供旧结果相对路径。历史评分仍与当时的真实商品与人工标签相关，**归档并不重新证明其真实性**。

## 测试边界

```bash
uv run --locked python -m pytest -q \
  tests/test_native_trial.py \
  tests/test_ocr_runtime_evidence.py
```

测试使用程序生成的 Pillow 海报和模拟 OCR，引入假引擎只是确认代码正确传递诊断数据；**没有在 CI 下载、运行 PaddleOCR 模型**。只有 `Real PaddleOCR CPU` 手动工作流或本机执行真正 OCR 才可产出对应运行日志，且仍需核验样本授权、来源与人工数据质量。

本地版本仍不提供用户鉴权和不可篡改审计服务；不要将自述元数据当作生产级防伪证据。
