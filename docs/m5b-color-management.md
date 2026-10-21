# M5b — 上传素材的 ICC → sRGB 颜色归一化

原 M5a 会将任何输入图片直接 `convert("RGBA")` 后清除 `icc_profile`。这会丢掉部分商品图携带的色彩空间描述，导致从宽色域相机或其它带 ICC 的图片转换成网页海报时颜色解释不一致。

## 工程实现

- 仍沿用 M5a 的上传文件 10 MiB、解码前 1600 万像素和单边 8192 像素限制。
- 经 JPEG EXIF Orientation 旋正后，先读取原图片的 ICC bytes，再通过 Pillow 的 `ImageCms.profileToProfile`（LittleCMS）从图片的源色彩空间转换到标准 sRGB。
- 商品图/Logo 的透明度（Alpha）独立保留；对 PNG/WebP 的 RGBA 数据，只转换 RGB，不让颜色管理变换改变 Alpha。
- 支持存在有效源 ICC 的 RGB/透明 RGB 图；CMYK JPEG 保留 CMYK 像素格式进入 ICC→RGB 转换，**不先做无色彩管理的 CMYK→RGB**。
- 无 ICC 的普通 Web 图片按 sRGB 解释，这是一项假设，不是对图像真实色彩空间的独立验证。
- 完成转换后所有像素统一存为 RGBA PNG，移除图片元数据（EXIF、ICC、PNG 文本），不会在保存的素材里残留客户原始相机信息。
- ICC 为空、超过 256 KiB、文件损坏或与像素模式不兼容：上传返回 **HTTP 422**，不静默忽略 ICC。校验发生在可能计费的 Qwen 背景生成之前。
- 无额外第三方依赖：重用项目现有 Pillow/ImageCms；无需更新 `pyproject.toml` 或 `uv.lock`。

## 测试

```bash
uv run --locked python -m pytest -q tests/test_color_management.py tests/test_image_upload_safety.py
```

新回归覆盖：使用 Pillow 生成真实 sRGB ICC；上传商品和 Logo 后检查图像不带 ICC，sRGB 像素近似不变且 Alpha 不变；无 ICC 素材继续支持；错误/空/超大 ICC fail-closed；不匹配的源 ICC 不可被忽略；无效 ICC 会在收费 API 调用之前拒绝并且不生成商品版本。

这些是颜色转换路径的单元/HTTP 行为测试，**不等于**已经使用真实广色域商品样张或专业显示器测量色差。实现还没有商业级品牌色彩验收、显示器校色或印刷打样功能。输出 sRGB 对普通网页展示更合适，但某些宽色域颜色在映射时可能失去原来的饱和度。

官方接口说明：[Pillow ImageCms](https://pillow.readthedocs.io/en/stable/reference/ImageCms.html)。
