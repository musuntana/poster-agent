# M3a — Acceptance Gate：真实版本检查 + 可选 PaddleOCR + 结构化报告

> 本阶段不是 M3 全部完成：尚无 SKU 相似度模型、Logo 定位、VLM Judge 或自动修复。**自动规则通过 ≠ 广告可投放。**

## 为什么这样设计

- **业务真实值**：价格、主标题来自经过 Pydantic 校验的 PosterSpec。
- **渲染证据**：打开导出的 PNG，检查目标尺寸并计算 SHA-256。
- **OCR 证据**：只有真的执行 PaddleOCR 3.x 推理且有结果，才给对应文案字段 PASS/FAIL；没有依赖/没有调用/调用失败均标记 UNVERIFIED。
- **品牌约束**：从用户自己配置的 forbidden_terms 检查结构化文案。未配置品牌词表标记 UNVERIFIED，不冒充官方法规检查。
- **人工审核**：无论自动规则结果如何，都必须由运营再次核对，M3a 不自动发布。

## Web 页面操作

1. 更新仓库，`python -m pip install -r requirements-dev.txt`，启动 FastAPI。
2. 上传产品和 Logo，生成海报。
3. 在右侧找到 **04 · Acceptance Gate**。
4. 使用“规则检查（不运行 OCR）”查看规则检查；注意文字的 OCR 状态是 NOT_RUN、字段为 UNVERIFIED。
5. 如本机可运行 PaddleOCR，则选择“PaddleOCR 3.x 真实文字识别”重跑，查看字段证据与合格情况。
6. 修改价格后会生成新版本 v2。旧版和新版的验收报告互不覆盖。

接口：

```bash
# 首次执行规则验收（无 OCR）
curl -X POST http://127.0.0.1:8000/api/posters/你的poster_id/versions/1/acceptance \
  -H 'Content-Type: application/json' \
  -d '{"ocr_mode":"none"}'

# 读取已保存报告
curl 'http://127.0.0.1:8000/api/posters/你的poster_id/versions/1/acceptance?ocr_mode=none'

# 请求真正的 PaddleOCR；未安装时返回 OCR UNAVAILABLE 和 UNVERIFIED 字段，而不是虚假的 PASS
curl -X POST http://127.0.0.1:8000/api/posters/你的poster_id/versions/1/acceptance \
  -H 'Content-Type: application/json' \
  -d '{"ocr_mode":"paddle"}'
```

报告保存在：`data/posters/{poster_id}/versions/v{version}.acceptance.{mode}.json`

## 品牌规则（用户提供）

```bash
cp data/examples/brand_rules.sample.json data/brand_rules.json
```

然后在本地 `.env` 中添加：

```ini
POSTER_BRAND_RULES_FILE=data/brand_rules.json
```

这里只对 Spec 中的文案做关键词匹配；它既不等于 Logo 视觉检查，也不等于正式广告法合规审核。实例规则不是权威的品牌规范。

## PaddleOCR 安装（可选）

M1/M2/M3a 规则模式均无需安装 PaddleOCR，默认的 `requirements-dev.txt` 和 GitHub CI 也不会下载模型权重。

要启用真实 OCR，先核对当前平台的 PaddlePaddle 推理引擎支持情况并安装适合的 CPU 包（特别是 **Mac M1 ARM64** 上的 Python/PaddlePaddle 版本匹配），然后执行：

```bash
python -m pip install -r requirements-ocr.txt
python -c 'import paddle; import paddleocr; print(paddle.__version__)'
```

若 `import paddle` 不成功，请按官方环境矩阵配置，或在受支持的 Linux CPU/Docker 环境运行 OCR；不要把安装成功当成保证。首次推理可能下载权重，需网络访问。

官方资料：
- https://www.paddleocr.ai/latest/en/quick_start.html
- https://www.paddleocr.ai/main/en/version3.x/installation.html
- https://paddlepaddle-static.cdn.bcebos.com/documentation/docs/en/install/pip/macos-pip_en.html

## 可核对的数据契约

`AcceptanceReport`：
- `poster_id`, `version`；
- `image_sha256`, `spec_sha256`；
- `ocr_mode`, `ocr_status`；
- `checks[].name/status/detail/evidence`；
- `auto_checks=PASS/FAIL/INCOMPLETE`；
- `verdict=BLOCKED/REVIEW_REQUIRED`，以及恒为 `true` 的 `requires_human_review`。

自动检查通过只说明**已配置且已运行的检查没有检测出问题**，不代表 SKU 保真、图像质量、品牌合规、法律合规等维度都已通过。

## 误判与边界

- OCR 漏字或识别错字可能产生假阳性错误；`FAIL` 表示检测不匹配，仍需人工核对。
- 如果品牌词表未设置，`forbidden_terms_in_spec=UNVERIFIED`。
- `logo_asset_present=PASS` 只意味着原始 Logo 资产存在，不证明最终图像中的位置和视觉完整性。
- 商品相似度、视觉质量、OCR 区域定位阈值调整以及人工一审通过率暂未实现；下一阶段 M3b 将加入 OCR 困难样本与误判分析。
- OCR 首次加载可能耗时，并在服务进程中占用大量 CPU/内存；单用户本地运行。
- 此版本没有用户身份/权限隔离，不能公网部署。

## 验收与 CI

```bash
python -m pytest -q
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

CI 使用模拟 OCR 输出验证：字段匹配、故意错误价格、品牌禁词、无 OCR、无 Logo、单独版本报告、PaddleOCR 3.x 响应解析。**不代表真实 OCR 模型已在 Mac M1 完成推理测试。**
