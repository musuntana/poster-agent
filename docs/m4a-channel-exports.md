# M4a — 不裁内容的多渠道 PNG 派生

## 目标

在电商投放中，同一营销图常需信息流竖版、竖屏故事和横向展示素材。本阶段先复用**已经生成并保存的 1024×1024 最终海报**，离线派生 3 种目标尺寸：不新增一次 Qwen 收费请求，不再次上传 SKU，不修改海报版本。

| 导出 ID | 像素尺寸 | 用途示例 |
| --- | --- | --- |
| `feed_4x5` | 1080×1350 | 信息流竖版 |
| `story_9x16` | 1080×1920 | 竖屏故事素材 |
| `landscape_16x9` | 1200×675 | 横向预览、横屏版位 |

**重要定义：** 这是 `contain_with_blurred_background` 的“安全缩放派生”，不是原生自适应重排。已完成的方形海报整张等比例缩放、居中放置，画布其余空间通过该图模糊延展填充。背景区域裁切不会裁掉中央海报的商品、价格或 CTA，但横屏尺寸中文字可能较小，仍需运营人工验收。

## 在网页中使用

1. 生成或恢复已保存的海报；
2. 在 **04 · 多渠道导出** 选 `4:5`、`9:16` 或 `16:9`；
3. 查看浏览器内预览并下载 PNG；支持修改价格生成 v2 后，再对 v2 单独派生；
4. 根据实际渠道审核标题、价格清晰度、留白、对比度与投放规范。

不需要安装 PaddleOCR，不会调用 Qwen，不需要云端 API Key。**不能把原 1024×1024 图片的 Acceptance Gate PASS 当成新尺寸图片的 PASS。** 后续真正的多规格响应式版式需要独立的布局设计和 OCR 区域定义。

## API

```text
GET /api/posters/{poster_id}/versions/{version}/exports
GET /api/posters/{poster_id}/versions/{version}/exports/{preset}
```

列表接口提供支持的 `format`、像素尺寸、下载 URL，以及 `reflows_text=false`、`automatically_accepted=false`。PNG 下载接口返回：

- `Content-Disposition: attachment`：建议保存文件名包括版本与规格；
- `X-Source-Image-SHA256`：所用**源 PNG** 的 SHA-256；
- `X-Export-Size`：派生像素尺寸；
- `X-Content-Box`：完整方形原海报在派生画布中的边界；
- `X-Export-Mode: contain`：明确属于安全缩放，不是重新排版。

非法规格返回 HTTP 422，找不到的原图版本返回 404。导出实时从原版 PNG 计算，当前不创建新的数据库实体、海报版本或持久化缓存。

## 验证

```bash
uv run --locked python -m pytest -q tests/test_channel_exports.py
```

自动化测试验证三种尺寸、生成字节确定性、居中图层**逐像素相等**（即中央原海报没有裁剪）、修改前后版本隔离、来源 SHA、原始 PNG 和 metadata 不变、完全不调用 Qwen，以及无效路径拒绝。

这只是工程可靠性测试，不证明具体广告平台的素材规范或真实广告投放效果。图片版权仍由使用者负责；Web 仍是无用户鉴权的单机版。
