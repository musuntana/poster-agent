# M3c — Web Human Review for Real Exported Poster Versions

## What was added

The web app now lets a local operator inspect a generated PNG, select correct / defective / unsure for each visible field, and save a per-version human review. The original exported PNG and PosterSpec each have a SHA-256 checksum bound into the review. Subsequent corrections append new immutable JSON records; they do not overwrite previous submissions.

These reviews are **self-reported manual labels**, NOT independent human adjudication and NOT OCR model results.

## How to use

1. Update the repository, run pytest, and start FastAPI on 127.0.0.1.
2. Upload a real product photo, optionally a brand logo, and generate a poster.
3. In section **05 · 人工标注**, inspect the actual displayed PNG carefully.
4. Enter a reviewer alias such as operator-01 (2–40 ASCII letters, numbers, underscore or hyphen).
5. Select correct / defective / unsure for all displayed text fields, enter a reason if helpful, and save.
6. Download the reviewer-specific CSV using **导出该审核员 CSV**.
7. If PaddleOCR can run in the local environment, first generate a PaddleOCR acceptance report for each reviewed image, then evaluate against these manual labels.

After refreshing, the page restores the most recent poster ID from browser local storage.

Human reviews are saved next to the PNG, under data/posters/<poster_id>/versions/vN.human_reviews/<review_id>.json. Each review is append-only. Export selects the latest submission for the requested reviewer on each version; different reviewers are never treated as automatic consensus.

## HTTP endpoints

- GET /api/posters/{poster_id}/versions/{version}/human-review?reviewer=operator-01: context (spec, image/spec checksums, field list, existing review).
- POST /api/posters/{poster_id}/versions/{version}/human-review: submit an immutable manual review. Missing fields return 422; stale image/spec hashes return 409.
- GET /api/annotations/export.csv?reviewer=operator-01: download the latest decisive labels for this reviewer, across local poster versions.

The CSV columns are exactly:

    poster_id,version,field,defective,image_sha256,reviewer

An unsure judgment is excluded, **not silently mapped to false**. Response header X-Uncertain-Skipped counts exclusions. With no decisive labels the endpoint refuses to export an empty dataset.

Example payload (replace both digest placeholders with the GET response values, and supply every relevant field):

    {
      "reviewer": "operator-01",
      "image_sha256": "64-hex-digest",
      "spec_sha256": "64-hex-digest",
      "fields": {
        "title": {"judgment": "correct", "reason": ""},
        "price": {"judgment": "defective", "reason": "Picture price differs from brief"},
        "cta": {"judgment": "unsure", "reason": "Text too small to inspect"}
      }
    }

## Compute model metrics only after REAL OCR

Download labels to data/my-real-labels.csv and use:

    python -m scripts.evaluate_real_reports --annotations data/my-real-labels.csv --ocr-mode paddle --output-dir data/eval_runs/m3c_human

The existing evaluator verifies actual saved PNG and OCR report SHA-256 before calculating field-level precision/recall/F1. If the real PaddleOCR model has not been run and saved, it will not invent numbers. Current CI uses simulated OCR.

## Limits

- **Single-machine, no authentication, no tenant isolation; never expose the API publicly.** Reviewer aliases do not establish identity.
- Local append-only JSON history is not a production audit trail or transaction system.
- Only the current poster version can be labeled in the UI; no batch review queue or bounding-box annotation.
- Human labels are not independently adjudicated; sample second-reviewer agreement has not been measured.
- Real PaddleOCR inference still requires an environment compatible with the model and its dependencies.
- Performance results on CI samples do not measure customer-poster OCR performance.

## Developer validation

Run: python -m pytest -q

The added tests cover version/hash checks, complete annotation requirements, history preservation, reviewer isolation, CSV exclusion of unsure labels, and direct integration of exported labels with the M3b real-report scorer (using a mocked OCR response).
