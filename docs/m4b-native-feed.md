# M4b — 1080×1350 原生 4:5 海报排版

## 本阶段真正解决什么

M4a 的三个比例虽然可以直接下载，但只是将整张 1024×1024 方图压缩后居中，文字在某些比例中可能过小。

M4b 在**保留 M4a 全部安全缩放导出**的前提下，新增一个原生 `1080×1350` 模板：读取当前海报版本的 **PosterSpec**，复用初次保存的**原商品图、可选 Logo 和背景图**，用 Pillow 重新计算标题、商品、价格、原价、CTA 的位置。不是对成品 PNG 做缩放套壳。

## 新导出选项

`feed_4x5_native` — **4:5 原生排版（推荐）**，1080×1350

仍保留旧接口：
- `feed_4x5`：原版完整安全缩放，1080×1350
- `story_9x16`：原版完整安全缩放，1080×1920
- `landscape_16x9`：原版完整安全缩放，1200×675

访问：
```text
GET /api/posters/{poster_id}/versions/{version}/exports
GET /api/posters/{poster_id}/versions/{version}/exports/feed_4x5_native
```

导出列表为兼容旧客户端，`formats` 和顶层 `reflows_text=false` 只描述原有 M4a 安全缩放选项；新方案单独列在 `native_formats`，`reflows_text=true`。所有方案均是 `automatically_accepted=false`。

响应带 `X-Export-Mode: native_layout`、`X-Source-Image-SHA256`、尺寸和下载文件名。

## 布局约束

- 商品主体保留原始 PNG 透明通道；不做假抠图，不把模型生图当原商品素材。
- 标题最多 4 行并在固定区域内根据文字长度选择字体。
- 价格、原价、CTA 在独立区域；若输入无法安全排版，返回 422，**不静默截断**。
- 背景从保存的原始背景图覆盖到新画布（背景可能被裁剪，但不是商品或文案）。
- 每次读取源版本的规格文件，不会把 v2 修改的价格错误渲染到 v1。
- 不创建新海报版本、不保存新一份商品素材、不请求 Qwen，也不修改已有 OCR 报告和人工标签。

## 浏览器操作

更新 main 后重新启动 FastAPI，生成或打开已有海报。在右侧 **04 · 多渠道导出** 下拉框默认选择“4:5 原生排版”，就可以预览和下载。也可切换回旧的安全缩放选项进行对比。

由于这是新的画布布局，**1024×1024 方图的旧 OCR 坐标区域不能复用，原方图的 Acceptance Gate PASS 不等于原生 4:5 PASS**。目前还没有针对新 4:5 的独立验收规则或人工准确率报告。请目视审核标题、价签、原价、CTA、SKU 完整度、渠道内容规范。

## 工程验证

```bash
uv run --locked python -m pytest -q tests/test_native_feed.py tests/test_channel_exports.py
```

测试覆盖：
- 真实 Pillow RGB 像素渲染 1080×1350；
- 与 M4a 的简单 contain 结果不同；
- 改变售价、商品/Logo 后原生图像跟随变化；
- 重复渲染确定性；
- HTTP 版本隔离、无效 ID / 未知模板拒绝；
- 读取已保存的原始 SKU/Logo/背景及该版本 Spec，不重复付费调用；
- 过长副标题明示返回 422，而不是被截断；
- 原 PNG、元数据和版本历史未修改。

这些测试不等于在你的 Mac 上实测过，也不等于真实广告渠道平台认可。下一阶段才考虑 9:16、16:9 **真正的原生布局**及针对新布局的 OCR/人工验收。
