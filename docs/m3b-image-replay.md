# M3b 扩展：20 张真实 PNG 像素回放 + 可选 PaddleOCR

## 与上一版 20 条文本模拟测试的区别

原来的 `evals/datasets/m3b_synthetic_ocr_v1.json` 是**手写 OCR 文本样本**，没有图片。

本次新增的 `scripts/run_image_benchmark.py` 会使用项目自带的 **Pillow 合成器**，实际渲染 20 张 1024×1024 PNG。输入是自制的教学商品素材、不同颜色的背景、故意写错的价格/标题/按钮。渲染过程确实经过真实图像像素，但**不调用云端 AI 图像生成模型**。

这些图片是 **code-rendered / programmatically labeled fixtures**，不是人工标注的真实业务图片。即便它们通过真实 PaddleOCR 推理，也不能代表自然场景/真实电商 SKU 的模型泛化准确率。

## 1. 不安装 OCR 就运行（CI 也运行此模式）

```bash
cd ~/repos/poster-agent
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m scripts.run_image_benchmark --ocr none
```

结果：
- `data/eval_runs/m3b_images/images/F01.png` ... `F20.png`：20 张实际渲染 PNG
- `data/eval_runs/m3b_images/images/manifest.json`：期望规格、实际渲染规格、程序性缺陷标签、逐图 SHA-256
- `data/eval_runs/m3b_images/image_benchmark_summary.json`：混淆矩阵、评测覆盖率等
- `data/eval_runs/m3b_images/image_benchmark_cases.csv`：逐字段和逐样本的判断记录

**本模式不运行 OCR**。所有 OCR 判断必须为 `UNVERIFIED`，覆盖率应为 0，Precision / Recall / F1 应是 `null`。如果这个模式显示模型精准率，请视为 Bug。

GitHub PR 的 CI 只执行此无模型模式；Actions 将生成的 PNG 和 JSON/CSV 报告作为构建 artifact 保存，供下载目视检查。

## 2. 本机 PaddleOCR 实测（需要你配置环境）

确认本机 PaddleOCR 3.x 可正常加载后，再运行：

```bash
python -m pip install -r requirements-ocr.txt
python -m scripts.run_image_benchmark --ocr paddle
```

- 模型真正读取了 20 张 PNG 并输出 OCR 文本后，才能计算 **本机 OCR + 当前 Gate 对这些程序渲染图** 的 Precision/Recall/F1。
- 如果 PaddleOCR 缺失、初始化失败或执行失败，命令会抛出明确异常，不会静默回退到模拟文本或 `PASS`。
- 使用原始图片 SHA-256 验证输入未改动；篡改图片会被拒绝。
- 优先检查 Mac M1 对 PaddlePaddle 引擎支持情况；如果安装失败，可在支持的 Linux CPU 环境执行，不应该为了让测试通过而伪造输出。
- PaddleOCR 首次使用可能要下载权重，因此需要网络访问。不要把模型文件或 API 密钥提交到 GitHub。

## 3. 用户应亲自做的验收

打开 `F01.png`、`F05.png`、`F07.png`、`F10.png`、`F14.png`、`F20.png`，逐张对照 `manifest.json`：

- 预期价：199；F05 实际价：189，应标记为价格错误
- F07 实际价：199.90，不能因为包含“199”就判正确
- F10 标题从“双十一优惠”变成“双十二优惠”
- F14 CTA 从“立即抢购”变成“立即购买”
- F20 包含标题、价格和 CTA 的多项错误

模型 OCR 是否能读出这些错误，必须通过真正的 `--ocr paddle` 才能验证。规则和标签是一回事，OCR 模型效果又是另一回事。

## 4. 真正的真实业务验证仍然缺什么？

这些图不是用户上传的产品、没有品牌复杂规范、背景也不是生成模型的真实输出。要取得用于简历的可验证实测指标，还需要：

1. 用实际商品图片、品牌素材和云端图像模型生成海报；
2. 固定一批图片并进行人工标注；
3. 用 PaddleOCR 实际运行，并与人工真值做误差对比；
4. 按商品类别、背景类型、字体、文字位置和错误类型汇总；
5. 单独保留未参与 Prompt/规则开发的测试集。

已有真实图片评测工具：`scripts.evaluate_real_reports`。不要把这套程序性标签数据灌进“人工真实标注”评测中以冒充业务实测。
