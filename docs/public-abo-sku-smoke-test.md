# Public SKU smoke test using Amazon Berkeley Objects (ABO)

The official Amazon Berkeley Objects dataset contains 147,702 product listings and 398,212 catalog images. The official page documents a CC BY 4.0 license and requires attribution.

- Official dataset: https://amazon-berkeley-objects.s3.amazonaws.com/index.html
- AWS Open Data Registry: https://registry.opendata.aws/amazon-berkeley-objects/
- License: https://creativecommons.org/licenses/by/4.0/
- Attribution: Amazon.com and the dataset authors listed on the official page.

## Why this source

Taobao SKU APIs and JD product APIs commonly require an approved developer application and seller/shop authorization. Pinduoduo data should also be accessed through authorized platform interfaces. A publicly viewable product page does not automatically grant permission to bulk-scrape or reuse images. This workflow uses a dataset with an explicitly published bulk-download path and license; it does not scrape Taobao, Pinduoduo, or JD.

## Run locally

The downloader uses Python standard-library networking and existing Pillow. It downloads only 20 selected original images, not the multi-gigabyte image archive. Data is written to a Git-ignored local directory and must not be committed.

First download and validate the sample without generating posters:

~~~bash
uv run --locked python -m scripts.import_abo_sample --limit 20 --output-dir data/real_trial/abo_public_sample
~~~

Then generate posters with the existing M5g local pipeline and create an M5j readiness audit:

~~~bash
uv run --locked python -m scripts.import_abo_sample --limit 20 --output-dir data/real_trial/abo_public_sample --apply
~~~

Use a fresh output directory for each new cohort. The importer refuses to overwrite a non-empty directory. Downloads are staged in a temporary sibling directory and published only after the full cohort and attribution file are written; a network or decode failure cleans up the staging directory rather than leaving a partial cohort that blocks retries. If you run the first command to inspect the downloaded sample, the second command reuses that existing manifest and attribution file instead of downloading the cohort again. If you explicitly pass `--limit` on the reuse step, it must match the downloaded row count; omit `--limit` to reuse the cohort's recorded size without ambiguity. Before reuse, the importer now verifies the manifest SHA-256 recorded in the attribution file, confirms every SKU is represented by exactly one attribution row, checks that each image path stays inside the cohort directory, and recomputes every source-image SHA-256. If a manifest or image was changed, deleted, or moved, reuse fails closed; restore the original cohort or choose a fresh output directory. These local digest comparisons detect accidental drift but are not a signed authenticity certificate.

Outputs include real_skus.csv, images/, PUBLIC_DATASET_ATTRIBUTION.md, and—when --apply is supplied—batch/batch_import_results.json plus m5j readiness artifacts.

## Interpretation limits

ABO is a public product catalog, not a current marketplace price feed. The importer creates deterministic **synthetic** price/original-price pairs only to exercise price rendering and consistency checks. These are not actual Amazon prices and must not be represented as actual selling prices.

The --apply path generates posters and audits M5j readiness. It deliberately does **not** run PaddleOCR inference, fabricate human reviews, or compute a product-quality score. This is a technical smoke test, not Issue #31's final real-marketplace acceptance cohort.

For Taobao, Pinduoduo, and JD, use an authorized seller's own export or approved official API access. Record source, authorization evidence, retrieval date, and image hashes. Do not bypass access controls or commit seller/customer data.
