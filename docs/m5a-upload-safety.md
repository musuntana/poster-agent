# M5a — 图片上传资源限制与元数据清理

本阶段面向真实运营素材中常见的安全问题：**压缩文件小，不代表展开成像素后也小**。例如一张精心构造的 PNG，即使上传字节数远低于 10 MB，其图片头也可能声明几千万像素。Pillow 对超过阈值的图片可能先发出 `DecompressionBombWarning`，如果仅依赖警告而继续解码，可能造成不必要的内存消耗。

## 约束与处理顺序

现有接口 `POST /api/posters` 对商品图和可选品牌 Logo 采用**相同**的校验流程：

1. 上传数据不得超过 10 MiB（原有规则）。
2. 用 Pillow 打开图片头，仅接受 PNG/JPEG/WEBP。
3. **解码前**检查每一边不大于 8192 像素、总像素不大于 **1600 万**、最小边不小于 64 像素。
4. Pillow 的 `DecompressionBombWarning` 作为拒绝处理而不是继续解码的警告。
5. 解码并按 EXIF orientation 修正方向，规范为 RGBA。
6. **M5b 更新：** 如果源图携带有效 ICC，先使用 Pillow ImageCms/LittleCMS 将商品图或 Logo 的颜色转换为 sRGB，单独保留透明 Alpha；配置无效/不匹配或超过 256 KiB 时拒绝上传（422），不静默移除配置继续渲染。无 ICC 的常见 Web 图片仍按 sRGB 假设处理。
7. 完成转换后清理 EXIF/ICC/XMP/PNG 文本等 `Image.info` 元数据，不把原始相机信息或客户自定义文本写入保存的 PNG。
8. 校验完成后才进入执行幂等声明或调用 Qwen，避免无效大图消耗 AI API 费用。

返回：**413** 表示上传文件字节数、像素数或边长超限；**422** 表示文件损坏、不支持格式或尺寸过小。这些错误发生在进入付费生成之前。

## 具体的可信边界

- 图片像素、长宽本身会保留；若相机照片带有 EXIF Orientation，则像素会先旋正再清除元数据。
- 已通过 ICC → sRGB 显式颜色转换避免直接丢失源色彩解释信息，但当前仍**不保证专业印刷级颜色一致性**：无 ICC 原图按 sRGB 假设、宽色域转 sRGB 可能压缩/裁切超出色域的颜色，未提供客户打印机 ICC 打样流程。
- 本功能不检测图片是否侵犯版权、不鉴别色情或违法素材、不自动抠图，也不提供恶意文件扫描。
- 单张文件字节上限不能代替反向代理层请求体、并发上传、全站磁盘配额或用户鉴权。应用仍为本地单机 Demo，不得直接公网开放。

## 测试

```bash
uv run --locked python -m pytest -q tests/test_image_upload_safety.py tests/test_api.py
```

新增测试无需申请超大内存：构造一张实际只有数百字节、但 PNG 头宣称 5000×5000 的合成图片并重算 IHDR CRC，验证 API **在解码前返回 413**，且无存储或 AI 调用；另覆盖过长单边、超限 Logo、现有 10 MiB 限制、产品与 Logo 元数据剥离，以及 JPEG EXIF orientation 的像素旋正。

M5b 另新增 [ICC → sRGB 专项说明](m5b-color-management.md)，测试使用 Pillow 实际生成的 sRGB ICC，而不是靠 mock 声称成功。

CI 只运行正常 pytest，不需要真实模型、PaddleOCR 或商品用户数据。
