# M4c — 对真正的 4:5 原生导出单独验收

M4b 已经能从保存的商品、Logo、背景和**选定版本**的 PosterSpec 绘制 1080×1350 竖版。但原有 M3 Acceptance Gate 只检查 1024×1024 方图，不能把方图的 OCR 区域或 PASS 结果直接复制到 4:5。

M4c 专门针对 **实际派生的 1080×1350 PNG 像素**新增一个独立质量报告，不改变老方形海报的验收接口。

## 使用方式

在网页的 **04 · 多渠道导出** 选择「4:5 原生排版」→ 选择「规则检查」或「真实 PaddleOCR」→ 点击「检查当前 4:5 导出」。两种检查互相区分：

- **规则检查（免费，不运行 OCR）：** 核实画布像素尺寸、已保存的商品素材与结构化规格，所有需要独立识别图中文字/价格位置的条目都为 `UNVERIFIED`。不声称图片文案正确。
- **真实 PaddleOCR（需要本地安装）：** 把当前生成的 1080×1350 PNG 写入一次性临时文件交给已存在的 PaddleOCR 3.x 适配器；OCR 成功后核对标题、活动价、原价、CTA、副标题以及位置证据。缺少坐标或模型不可用则明确标为未验证，价格颠倒则 FAIL 并 BLOCKED。识别完成后临时文件删除，不保存或共享商品图到外部云模型。

4:5 模板专属价格区域（坐标单位是像素）：

| 字段 | 有效区域 xyxy |
| --- | --- |
| 活动价 | (54, 866, 515, 1014) |
| 原价 | (54, 1014, 515, 1097) |

与原来的 1024px 方形海报区域**完全分离**。旧方图的 OCR 检查仍沿用其原始配置，并由回归测试保护。

## API

```text
POST /api/posters/{poster_id}/versions/{version}/exports/feed_4x5_native/acceptance
Content-Type: application/json
{"ocr_mode":"none"}     # 或 paddle

GET /api/posters/{poster_id}/versions/{version}/exports/feed_4x5_native/acceptance?ocr_mode=none
```

报告包含：原方图 SHA-256、当前版本 Spec SHA-256、原 SKU/Logo/背景 SHA-256、**原生 4:5 输出 PNG** SHA-256、画布尺寸、每条规则及 OCR 证据、`auto_checks`、`verdict`、`ocr_status`。

在 `data/posters/<id>/versions/vN.feed_4x5_native.acceptance.<ocr_mode>.json` 保存报告，不创建 vN+1，不修改原图片、价格规格或 M3 的方图验收。

GET 读取时重新核实当前版本、素材和重新渲染的输出摘要，发现过期证据返回 HTTP 409；报告未生成返回 404。不会为 GET 自动调用 OCR。

## 严格限定的可信结论

- `PASS` 仅证明相应规则或 OCR 文本匹配成功，**不是**运营可直接投放的批准。
- `INCOMPLETE` 表示至少一项未验证；默认不安装 OCR 时仍然是未验证。
- `FAIL` 会阻断自动验收，但模型可能误识别，仍需人工确认。
- `verdict` 只可能为 `BLOCKED` 或 `REVIEW_REQUIRED`，不存在跳过人工审查的自动放行。
- OCR 坐标框与文案匹配是启发式，尚未证明商品保真度、二维码准确性、视觉对比度、品牌规范或各广告平台规则。
- 目前的 CI **mock OCR**，未对你的 Mac M1 跑新的真实 4:5 OCR；Linux 之前成功验证的仅是 M3g 方图示例，不可引用为这个新模板的准确率。

## 本阶段验证

```bash
uv run --locked python -m pytest -q tests/test_native_acceptance.py tests/test_native_feed.py tests/test_ocr_price_layout.py
```

核心回归：原生 PNG 尺寸确实为 1080×1350；OCR 适配器拿到真正渲染的原生图片；活动价、原价在新区域的 PASS 与颠倒时的 FAIL；无 OCR/未安装模型不冒充 PASS；版本不串用；保存的源 SKU/背景/价格和实际导出摘要被绑定到报告；读取报告不会再次运行 OCR，也不调用 Qwen；变更素材或版本规格后旧报告返回 409。

下一步是收集真实商品图片，真正运行 4:5 PaddleOCR 并加入第二人工复核，统计位置规则的 FP/FN，再讨论 VLM 或 9:16 原生排版。
