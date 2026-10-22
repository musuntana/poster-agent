# M5h — 本地 4:5 商品审核队列

M5g 已支持从 CSV 批量生成本地海报，但人工操作需要自行复制每个 `poster_id` 才能在浏览器继续 M5c/M4c 审核。M5h 提供了一个**直接在已有单机网页内显示的、只读审核队列**，最大化复用 M5e 的状态判断和现有的 4:5 图片/审核控件。

## 操作方式

1. 更新项目并用 `uv run --locked uvicorn app.main:app --reload --host 127.0.0.1 --port 8000` 启动。
2. 在 <http://127.0.0.1:8000/> 页面右侧顶部点击 **刷新本地验收队列**。
3. 队列按需要处理的优先级显示每张海报的**最新版本**、标题、状态及下一步操作建议。审核员代号取自页面的「4:5 原生海报人工验收」区域；如切换代号，请再刷新队列。
4. 点击相应项目旁的 **打开审核**，页面会复用已有的海报预览、原生 4:5 图片、人工标注控件和「原生 4:5 单独验收」按钮。**不会**新建海报、修改版本、自动标注或调用 PaddleOCR。
5. 使用原有控件完成人工审核。需要真实 OCR 时由用户**单独选择 PaddleOCR 并明确触发**；之后再次点击「刷新本地验收队列」查看最新状态。

请注意：`READY` 的准确含义是“这位审核员的确定性标签与已保存 OCR 验收报告满足当前评分器的输入条件”，**不意味着广告内容可自动投放**；既没有建立专业广告法合规检查，也没有真实性认证。

## API

```text
GET /api/trials/review-queue?reviewer=local-operator
```

返回结构：

```json
{
  "reviewer": "local-operator",
  "variant_id": "feed_4x5_native",
  "read_only": true,
  "runs_ocr": false,
  "automatically_approved": false,
  "total_posters": 0,
  "total_versions_in_cohort": 0,
  "latest_status_counts": {},
  "items": []
}
```

当存在海报时，`items` 包含 `poster_id`、`version`、`title`、`status`、`action`、`definite_labels`、`unsure_labels`、`ocr_text_labels`、`native_image_url`、`poster_url`。

后端**复用** `evals.native_trial.audit_trial()` 获得状态：`NEEDS_REVIEW`、`REVIEW_UNSURE`、`OCR_MISSING`、`OCR_NOT_OK`、`OCR_STALE`、`OCR_CHECKS_INVALID`、`REVIEW_STALE`、`SOURCE_INVALID`、`READY`。只选取每张海报最新版本用于网页导航，仍会统计全体历史版本数量。

队列不保存自身数据库，也不新建审核状态。网页通过 `textContent` 写入源标题和状态说明，避免把本地文件中的标题当作 HTML 执行。点击打开前再次向现有 `GET /api/posters/{id}` 取最新规格，如在刷新队列后发生新版本变更则拒绝打开过期的审核项，要求重新刷新。

## 质量与工程边界

- **不新增模型、收费请求或 OCR 推理**。刷新队列只读取当前保存在本地的数据，历史 OCR 报告中的 `OK`、`RECORDED` 仍不构成推理防伪证明。
- 队列只适合**个人电脑、本地少量样本**；M5e 状态计算需要读取每个版本素材并可能重新渲染 4:5 图片，100+ 海报时刷新速度可能较慢。目前没有后台队列、缓存、分页和多租户隔离。
- 应用没有身份认证；审核员代号不是被验证的真实身份，切勿在公网开放该接口。
- 这里并不代替 M5e 的完整试验盘点或 M5d 的混淆矩阵评测，后续仍需整理真实商品素材、人工复核和真实 PaddleOCR 数据。

## 测试

```bash
uv run --locked python -m pytest -q tests/test_trial_review_queue.py
```

新测试验证空队列、最新版本与历史版本计数、审核员隔离、OCR 缺失和 READY 状态、刷新不会再次调用 OCR/模型或修改海报文件、异常审核员代号拒绝，以及页面存在打开入口。测试中的 OCR 检测行**全部模拟**，不构成真实客户验收准确率。
