# M3d: Run real PaddleOCR on Apple Silicon and retain verifiable evidence

This increment does **not** claim your Mac has run OCR. It adds a local workflow to prove whether a specific installation and image actually worked.

## Platform prerequisites

PaddlePaddle 3.2.0 ships a macOS 11+ ARM64 wheel for CPython 3.12 (and versions 3.9–3.13). The preferred starting point for a MacBook M1 is a **native Python 3.12 ARM64 virtual environment**, not a Docker stack or GPU service.

References:
- PaddlePaddle release wheel inventory: https://pypi.org/project/paddlepaddle/3.2.0/
- PaddlePaddle official install: https://www.paddleocr.ai/main/en/version3.x/paddlepaddle_installation.html
- PaddleOCR quick start and supported inference engines: https://www.paddleocr.ai/latest/en/quick_start.html

Do not assume Python 3.14 has a matching PaddlePaddle 3.2.0 ARM64 wheel. If running Python 3.14, create a fresh Python 3.12 environment; do not replace your existing development environment.

## Mac M1: verified installation *instructions* (not yet verified on your device)

Open Terminal in poster-agent:

    uname -m
    python3.12 --version

Expected architecture is arm64; Python 3.12 must be installed. If `python3.12` is missing, install an ARM64 CPython 3.12 interpreter using your normal package manager.

    python3.12 -m venv .venv-ocr
    source .venv-ocr/bin/activate
    python -m pip install --upgrade pip
    python -m pip install -r requirements-dev.txt
    python -m pip install paddlepaddle==3.2.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
    python -m pip install -r requirements-ocr.txt

If that index is unreachable, check the official package / PyPI instructions and try a compatible wheel. Do not switch to an unrelated architecture automatically.

Check:

    python -c 'import platform,sys; print(platform.machine(),sys.version)'
    python -c 'import paddle, paddleocr; print(paddle.__version__,paddleocr.__version__)'

The first PaddleOCR model invocation may download weights. Cache and model artifacts can be large; network and memory constraints apply.

**The source branch and CI cannot access your personal Mac.** These steps must be run locally.

## Phase A: diagnostic only — no model

    python -m scripts.ocr_probe

Produces `data/eval_runs/ocr_probe.json` with Python, CPU architecture and package versions. Its status is deliberately NOT_RUN. It does not download weights or report accuracy.

## Phase B: run actual inference on a known PNG

    python -m scripts.generate_demo
    python -m scripts.ocr_probe --infer --image data/examples/sample_preview.png

The default output `data/eval_runs/ocr_probe.json` contains:
- runtime status OK / UNAVAILABLE / ERROR;
- `model_invocation_completed` true only after the OCR adapter returns;
- Paddle and PaddleOCR distribution versions and CPU architecture;
- PNG dimensions, SHA-256 and byte length;
- elapsed milliseconds, recognized line count and mean OCR confidence.

A non-OK inference exits nonzero (2), without inventing any model precision or score. OCR confidence measures the OCR engine's own estimate, **not ground-truth correctness**.

To intentionally retain the raw text and bounding boxes, pass `--include-text`. These observations may contain customer data. They are written into the gitignored local data/eval_runs directory; do not post private data to a public issue.

## Phase C: run real OCR over 20 rendered test PNGs

Only after the single-image smoke test completes:

    python -m scripts.run_image_benchmark --ocr paddle

The runner generates code-rendered PNG images, calls the actual OCR adapter, scores against programmatically injected text defects and writes:
- `data/eval_runs/m3b_images_paddle/image_benchmark_summary.json`
- `data/eval_runs/m3b_images_paddle/image_benchmark_cases.csv`
- `data/eval_runs/m3b_images_paddle/ocr_observations.jsonl` (recognized text, line scores, coordinates, per-image time)
- `data/eval_runs/m3b_images_paddle/images/` (20 inputs and SHA manifest)

This input set is still synthetic program-rendered images, not real customer posters. The output is useful for testing OCR setup and the detector's behavior on rendered text, not for claiming business accuracy.

Offline `--ocr none` now writes to a **different folder** (`data/eval_runs/m3b_images/`) and never creates OCR observation traces.

## Phase D: evaluate actual user-product images

1. Upload real owned/licensed product images and brand materials.
2. Generate real poster versions; record the source image.
3. In Acceptance Gate, run PaddleOCR on the exported PNG.
4. In **05 · 人工标注**, inspect the PNG and label title, price, CTA and any optional text fields.
5. Export the reviewer-specific CSV and run:

    python -m scripts.evaluate_real_reports --annotations data/my-real-labels.csv --ocr-mode paddle --output-dir data/eval_runs/m3d_real

The real evaluator rejects missing/stale OCR reports and compares picture hashes. The human labels are user-supplied, not automatically audited.

## CI scope

GitHub Actions runs only the diagnosis and mock-based unit tests. It does NOT install PaddlePaddle weights or execute the real model. Green CI is NOT proof that Mac M1 real inference succeeded.

## Next milestone

Once real OCR is working, collect at least 20 actual user-product posters with independent human review; identify cases where price or CTA fails. Then improve text segmentation or bounding-box aware price matching before adding VLM-as-Judge.
