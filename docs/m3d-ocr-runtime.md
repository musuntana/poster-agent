# M3d：PaddleOCR 本地诊断与真实推理证据（uv 环境）

> 本项目开发与 CI 已采用 **uv + Python 3.12 + pyproject.toml + uv.lock**。请先查看 [uv 使用手册](uv-workflow.md)，不要执行历史 pip requirements 的安装命令。

## 1. Mac M1 / Linux 本地准备

安装 [uv](https://docs.astral.sh/uv/getting-started/installation/) 并确认 `uv --version`。uv 会安装与 CPU 架构匹配的 Python 3.12，不必覆盖 Mac 系统 Python。

```bash
cd ~/repos/poster-agent
uv python install 3.12
uv sync --locked
uv run --locked python -m scripts.ocr_probe
```

这一步只做 **NOT_RUN** 环境诊断，没有模型推理，也不测 OCR 准确率。

需要真正加载 OCR 时，先加入锁定的可选 PaddleOCR 包，然后从官方 CPU 索引安装 PaddlePaddle 3.2.0：

```bash
uv sync --locked --extra ocr
uv pip install --python .venv/bin/python paddlepaddle==3.2.0 \
  --index-url https://www.paddlepaddle.org.cn/packages/stable/cpu/
uv run --no-sync python -c 'import platform,paddle,paddleocr; print(platform.machine(), paddle.__version__, paddleocr.__version__)'
```

Mac M1 请确认官方有对应的 ARM64 wheel。若平台不支持或初始化失败，属于 **UNAVAILABLE/ERROR**，不能宣称模型已成功运行。PaddlePaddle CPU 引擎单独装于 uv 环境，不属于默认锁依赖；后续用 `uv run --no-sync`，避免同步覆盖这个非默认包。

## 2. 在一张真实 PNG 上执行推理

```bash
uv run --no-sync python -m scripts.generate_demo
uv run --no-sync python -m scripts.ocr_probe \
  --infer --image data/examples/sample_preview.png
```

输出 `data/eval_runs/ocr_probe.json`，记录 Python、架构、Paddle/PaddleOCR 包版本、图片 SHA-256、像素尺寸、是否完成 OCR 调用、耗时和识别行数。

若有明确需要保存真实识别文本和坐标框，可以额外加 `--include-text`；识别文字可能涉及客户隐私，不要上传到公开仓库。无 `--infer` 的 `NOT_RUN` 不算推理。

## 3. 20 张程序生成 PNG 的批量实验

```bash
uv run --no-sync python -m scripts.run_image_benchmark --ocr paddle
```

`data/eval_runs/m3b_images_paddle/` 保存程序生成的 PNG、`image_benchmark_summary.json`、`image_benchmark_cases.csv` 以及含原始 OCR 文本的 `ocr_observations.jsonl`。这是**程序合成图的真实模型推理**，不是客户商品数据集的准确率。

`--ocr none` 则只是离线回放，报告必须显示模型未执行。不要将两类实验的报告混用。

## 4. 当前已有证据及下一步

独立的 GitHub Actions [Real PaddleOCR CPU run #39586650764](https://github.com/musuntana/poster-agent/actions/runs/39586650764) 已在 **Linux x86_64 CPU** 上真实执行 PaddleOCR 3.3.0（PaddlePaddle 3.2.0），一张程序海报得到 7 行、7 个文字框。这是 Linux 单图运行的记录，**不能证明 Mac M1 已完成推理**，也不能当作真实商用海报的 OCR 精度。

对真实 SKU，应先在网页生成海报、执行 Acceptance Gate 和独立人工标注，再用：

```bash
uv run --no-sync python -m scripts.evaluate_real_reports \
  --annotations data/my-real-labels.csv \
  --ocr-mode paddle \
  --output-dir data/eval_runs/m3d_real
```

保存并核对图片与报告的 SHA-256，分开记录空结果、缺少 OCR、人工不确定标签。默认 GitHub 快速 CI 不下载模型；真实 OCR 工作流只能手动触发。

官方：<https://www.paddleocr.ai/> · <https://docs.astral.sh/uv/concepts/projects/sync/>
