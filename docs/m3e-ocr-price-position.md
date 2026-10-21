# M3e: price correctness needs spatial OCR evidence

## Why it exists

The previous Acceptance Gate checked whether the target sale price appeared **anywhere** in the OCR transcript. Example: a poster should say sale ¥199 and original ¥399. If the renderer or layout erroneously swapped their positions, OCR text still contained both prices, so both text-only fields could PASS.

M3e adds two independent template-specific checks:

- `ocr_price_position`: sale price must be recognized with a usable OCR bounding box inside the known sale-price area.
- `ocr_original_price_position`: if original price exists, it must be recognized in its own area.

The check is integrated into each version's Acceptance Gate. Text-only checks are intentionally retained as separate signals for comparisons with previous M3b benchmarks. If any spatial check FAILs, the overall auto-check result is FAIL and the poster is BLOCKED. If spatial proof is missing, it is UNVERIFIED rather than assumed correct.

## Coordinates and conservative decisions

The current Pillow template is **1024×1024** and has fixed price positions in `app/compositor.py`.

| Field | Region (x1,y1,x2,y2) |
| --- | --- |
| Sale price | (45, 645, 512, 790) |
| Original price | (60, 790, 512, 858) |

A currency OCR line is accepted only with a valid pixel XYXY bounding box, finite confidence score >=0.40, and a parseable currency amount. The center of its box must lie within the target zone.

- **PASS**: expected amount recognized within the expected price zone, with no conflicting amount and no ambiguous money tokens.
- **FAIL**: different amounts coexist in that price zone; or expected amount appears **only outside** the appropriate price zone.
- **UNVERIFIED**: OCR wasn't executed, amount or coordinates are missing/unreliable, score is too low, or evidence is insufficient.

Unverified does **not** count as a positive pass.

These coordinates apply **only to the current hard-coded Pillow template**. For new layouts, multiple aspect ratios, rotation, or template revisions, update the region contracts and add tests before claiming checks are applicable. Position checks are heuristic: a large OCR bounding box can overlap zones; no OCR-based result is guaranteed to prove exact text placement.

## Reproducible tests (no real model)

```bash
cd ~/repos/poster-agent
git status
git pull --ff-only origin main
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q tests/test_ocr_price_layout.py tests/test_acceptance.py
```

Scenarios include:
- Correct sale ¥199 and original ¥399 in their normal zones;
- Two prices swapped: text-only matches both, new zone checks FAIL;
- A correct price plus an additional incorrect number in the sale-price zone: FAIL;
- Correct price appearing only in the original-price zone: FAIL;
- Missing bounding boxes, invalid coordinates or low confidence: UNVERIFIED;
- Decimal mismatch (¥199.90 vs ¥199): FAIL;
- API-level persisted Acceptance Gate report has `BLOCKED` when spatial checks FAIL.

All tests use **mock OCR rows**. They prove evaluator logic, not PaddleOCR model quality.

## Actual Mac M1 validation

Once PaddleOCR is installed and `scripts.ocr_probe --infer` returns OK on a real exported poster:

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000, create a poster, run the Acceptance Gate in PaddleOCR mode, and inspect both `ocr_price` and `ocr_price_position` in the report. If OCR returns missing or malformed bounding boxes, the spatial check should be UNVERIFIED. That is a limitation to investigate, not an invitation to fabricate boxes.

## Evaluation caveat

Existing M3b `_field_checks` benchmark metrics remain **text-field only** for backwards comparability. This M3e addition changes full Acceptance Gate pass/block decisions but **does not retroactively improve or establish existing precision/recall numbers**. To claim spatial gate benefit, build a separately labeled dataset of layout faults and evaluate new predictions against manually checked PNGs. Real PaddleOCR inference on your Mac is still unverified.

The system still needs manual approval and is local-only without authentication; do not expose it to the public Internet.
