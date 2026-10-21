# M5c — 原生 4:5 海报人工质量标注

M4b 已经拥有真正的 1080×1350 原生导出，M4c 已有独立的可选 OCR/价格区域验收；但原 M3c 人工标注仍只针对 1024×1024 方图。M5c 补充一个**独立的原生 4:5 人工标注链路**，让真实运营审核记录能够用于后续误差分析，避免把方图的正确与否误当成原生版结果。

## 网页使用流程

1. 本地启动 FastAPI，上传真实商品图、创建海报并选择 **04 · 多渠道导出 → 4:5 原生排版**。
2. 对照页面中的**实际 1080×1350 导出预览**，在“4:5 原生海报人工验收”填写审核员代号。
3. 对每个显示的项选择 `correct`（正确）、`defective`（有缺陷）或 `unsure`（无法判断），必要时记录错误位置与原因。必须检查：
   - 商品图是否完整/无遮挡（`sku_integrity`）
   - 整体文字可读性（`text_legibility`）
   - 构图与层次（`composition`）
   - 标题、活动价、CTA；只有存在副标题、原价时才出现相应字段
4. 点击“保存 4:5 人工验收”：每次提交生成新的 JSON，不覆盖历史。
5. 点击“导出该审核员 4:5 CSV”：只导出此审核员**最新一次**有明确结论的字段。不同审核员不会虚构成专家共识。

页面和接口不需要真实 OCR、Qwen API 或额外模型。人工提交的判断是自述标签，不是专家鉴定，也不自动触发投放。

## HTTP 与证据绑定

```text
GET  /api/posters/{poster_id}/versions/{version}/exports/feed_4x5_native/human-review?reviewer=operator-01
POST /api/posters/{poster_id}/versions/{version}/exports/feed_4x5_native/human-review
GET  /api/annotations/native-4x5/export.csv?reviewer=operator-01
```

GET 上下文返回原生导出 URL、预计字段、指定审核员最近的标注及：

- `image_sha256`：**实际原生 4:5 PNG** 的 SHA-256；
- `source_image_sha256`：对应原方图摘要，仅作为来源身份证；
- `spec_sha256`：当前版本海报规格的摘要；
- `asset_sha256`：原 SKU、Logo 和背景素材摘要。

POST 必须提交上述全部摘要与所有当期字段，任意不一致返回 **409**；字段缺失或格式错误返回 **422**；版本不存在返回 **404**。保存到：

```text
data/posters/<id>/versions/vN.feed_4x5_native.human_reviews/<review_id>.json
```

这与方图 `vN.human_reviews/` **物理隔离**。任何已保存审核记录都不能使后续改价版本自动通过人工审核；修改源素材或规格后，读取当前审核员旧记录、导出其旧标签都会返回 409。

## CSV 导出

```text
poster_id,version,variant_id,field,defective,image_sha256,source_image_sha256,reviewer
```

`variant_id` 始终是 `feed_4x5_native`。人工 `unsure` 会被剔除，不能转为 `false`；全为 `unsure` 时拒绝输出空评测集。CSV 带 BOM 以方便使用中文办公软件。CSV 本身不提供 OCR 预测值，因此**不能仅凭这个 CSV 声称模型的准确率/F1**。

原 M3c 方图标签与评测接口完全不变；旧的 `scripts.evaluate_real_reports` 面向方图报告，不应直接把这份 4:5 CSV 喂给该评测器冒充原版指标。后续要做 4:5 的 FP/FN/F1，需先运行真实 4:5 OCR 并开发明确关联 `variant_id` 的评测器。

## 工程检查及可信边界

```bash
uv run --locked python -m pytest -q tests/test_native_human_reviews.py
```

回归包含：实际导出 PNG 哈希一致、SKU/背景/spec 哈希绑定、两位审核员独立记录、追加更正、未审字段拒绝、无法判断不参与导出、v1/v2 版本隔离、旧素材被改变后的 409、原方图 CSV 不被原生标签污染。全程复用已保存商品素材，**不会请求收费 AI 模型**。

**部署限制：** 应用无身份认证、多租户隔离与正式审计权限，审核员代号不可证明真实身份；客户商品图/原始数据都只应在受信本机保管，不得公网开放。当前没有真实消费者海报的人工审核样本、独立专家一致性统计，不能声称产品已具备上线所需的广告审核准确率。
