# M3f — OCR bounding box and price-zone diagnostic image

This milestone adds a **visual explanation**, not a new image model or another OCR call. The overlay makes it easier to diagnose the preceding M3e price-position checks on an actual exported poster.

## Practical use

1. Generate a poster locally at `http://127.0.0.1:8000`.
2. In **04 · Acceptance Gate**, select `PaddleOCR` and click `检查当前版本`, **only if PaddleOCR is really installed and runnable on your machine**.
3. After saving the report, the page displays a **诊断叠加图** beneath the checks. Click **导出诊断 PNG** to inspect it.
4. The diagnostic image shows the original PNG plus:
   - A sale-price zone and (if an original price exists) original-price zone.
   - Color-coded zone states: green PASS, red FAIL, amber UNVERIFIED.
   - Cyan OCR rectangles numbered by the corresponding row order in the saved acceptance report.
   - A banner with `OCR: OK/NOT_RUN/UNAVAILABLE/ERROR`, count of drawn boxes and missing/invalid boxes.

The overlay is **derived from already saved evidence**, not fresh OCR. It does not call PaddleOCR and does not overwrite the source PNG.

## API

```text
GET /api/posters/{poster_id}/versions/{version}/acceptance/overlay?ocr_mode=paddle
```

- Returns `image/png` and `Cache-Control: no-store`.
- Returns `X-Source-Image-SHA256`, `X-OCR-Status`, and `X-OCR-Boxes-Drawn`.
- Returns **404** if no acceptance report for this version and mode exists; run the Acceptance Gate first.
- Returns **409** if the image/spec file's SHA-256 differs from the report or the report belongs to a different poster/version; rerun the Acceptance Gate.
- Returns **422** if the mode or stored report data is invalid.

For offline/none mode, an overlay can show **template zones only**. It must explicitly say `OCR: NOT_RUN` and must never invent OCR boxes.

## Evidence limitations

- The legacy Acceptance Gate embeds only the **first 35 OCR rows** into each text field's evidence. This overlay intentionally draws at most those first 35; it does not claim to show every detected line if a model returned more.
- Missing/malformed boxes are counted but **not guessed or reconstructed**. If the price position check was UNVERIFIED, colored zones illustrate that missing evidence rather than treating it as PASS.
- Sale/original-price region coordinates only apply to the current **1024×1024 fixed Pillow layout**. New poster templates require their own geometry contracts.
- The image remains a diagnostic aid. Colored boundaries and OCR rectangles are heuristics, not authoritative segmentation or legal advertising verification.
- The overlay may contain private product and branding content already visible in the underlying PNG. Keep it on your machine; the application still has **no user authentication** and must not be publicly exposed.

## Developer tests (all mock OCR)

```bash
python -m pytest -q tests/test_ocr_overlay.py tests/test_ocr_price_layout.py tests/test_acceptance.py
```

Test contracts:
- GET overlay must not invoke PaddleOCR; original image bytes remain unchanged.
- A valid stored report produces a well-formed 1024×1024 PNG with price zones and numbered OCR boxes.
- No-OCR and invalid/missing boxes cannot produce invented OCR boxes.
- Missing report, invalid poster/version/mode and stale image/spec report are rejected.
- v1 and v2 use different saved reports, not each other's evidence.

**CI runs mocked OCR rows only.** Green CI does not prove PaddleOCR has actually run on your Mac. To validate the real model, use `python -m scripts.ocr_probe --infer --image data/examples/sample_preview.png`, then trigger PaddleOCR Acceptance Gate on a real poster.
