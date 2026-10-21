# M3g — Real PaddleOCR execution evidence on GitHub-hosted Linux CPU

## Why this stage exists

Previous CI runs exercised **mock OCR** responses, while `ocr_probe` could only run a real model manually on a compatible machine. M3g adds a separate, on-demand GitHub Actions pipeline which **installs PaddlePaddle + PaddleOCR, downloads actual weights, runs inference on a synthetic PNG and validates the output**. No API key or Qwen image-generation request is required.

This is **Linux CPU** evidence. It **does not** validate Mac M1/ARM64 or human-reviewed customer images.

## Two intentionally separate workflows

- `CI` (existing): fast unit tests, no OCR model; must be green for PR merge.
- `Real PaddleOCR CPU` (new): opt-in `workflow_dispatch` on `main`. It is also triggered for **pushes to the development branch** `feature/m3g-real-paddleocr-ci` to bootstrap and verify the workflow itself.

The new workflow uses Ubuntu 22.04 x86_64, Python 3.12, official PaddlePaddle 3.2.0 CPU wheels and a pinned PaddleOCR 3.3.0 package. It selects the official `PADDLE_PDX_MODEL_SOURCE=BOS` model download source. If package installation/model download fails, the run FAILS; it will not silently substitute mock OCR.

Official sources:
- https://www.paddleocr.ai/main/en/quick_start.html
- https://www.paddleocr.ai/v3.3.0/en/version3.x/installation.html
- https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/FAQ.en.md

## After this PR is merged

On the GitHub website, open **Actions → Real PaddleOCR CPU → Run workflow**, choose `main` and optionally check `full_benchmark`.

The **default smoke run**:
1. installs PaddlePaddle/PaddleOCR;
2. generates `data/examples/sample_preview.png` from the existing local compositor;
3. executes `python -m scripts.ocr_probe --infer --include-text --image data/examples/sample_preview.png`;
4. verifies result status OK, real model invocation completion, positive recognized line count, at least one valid OCR bounding box, installed runtime versions, and image SHA-256 matching;
5. uploads only **synthetic generated image and evidence JSON**.

The optional **20-image full run** additionally executes:
```bash
python -m scripts.run_image_benchmark --ocr paddle \
  --output-dir data/eval_runs/real_ocr_ci/benchmark
python -m scripts.verify_real_ocr_evidence \
  --probe data/eval_runs/real_ocr_ci/probe.json \
  --image data/examples/sample_preview.png \
  --expected-platform Linux \
  --benchmark-dir data/eval_runs/real_ocr_ci/benchmark \
  --output data/eval_runs/real_ocr_ci/verified.json
```
The extra report verifies all 20 image hashes and their matching OCR observation records. It does **not** claim OCR accuracy on customer posters.

## Evidence

Successful workflow should upload an artifact named `real-ocr-linux-cpu-evidence`, containing:
- `probe.json` — runtime details, recognized strings, scores, coordinates, image digest and elapsed time;
- `verified.json` — evidence integrity checks and package versions;
- `sample_preview.png` — actual input image used by probe;
- if enabled, a `benchmark/` folder with 20 synthetically rendered PNGs, raw OCR observations, predictions and metrics.

Only runs where the real smoke + verifier steps COMPLETE count as verified evidence. A green PR unit-test badge, a diagnostic NOT_RUN report, or a mocked integration test is **not** proof of real inference. GitHub-hosted Actions minutes, storage and downloads depend on repository/account limits.

## Failures and next steps

If the real job fails, inspect the installation or model download/initialization logs. Correct the environment before claiming results; **do not change `mode` or return fake OCR rows to make the run green**.

After Linux is verified, separately run the same probe on the Mac M1 in a compatible native Python 3.12 environment. Then label at least 20 actual user-product poster PNGs in the existing human-review UI and run `scripts.evaluate_real_reports` to measure errors with real human labels.

**Security:** workflow uses only locally generated synthetic assets; do not upload private user product images to CI artifacts. The web service still has no authentication and remains local-only.
