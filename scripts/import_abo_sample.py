"""Download a small CC BY 4.0 ABO cohort and optionally generate posters locally."""
from __future__ import annotations
import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from PIL import Image, UnidentifiedImageError

BASE = "https://amazon-berkeley-objects.s3.us-east-1.amazonaws.com/"
SHARDS = 16
MAX_BYTES = 10 * 1024 * 1024
PRICES = (19.90, 29.90, 39.90, 49.90, 59.90, 79.90, 99.90, 129.90, 159.90, 199.90)
STYLES = ("蓝色科技风", "简约清新", "暖色促销", "极简高级")

def _open(url: str):
    return urllib.request.urlopen(urllib.request.Request(
        url, headers={"User-Agent": "poster-agent-abo-smoke-test/1.0"}), timeout=45)

def _jsonl(url: str) -> Iterator[dict]:
    with _open(url) as response, gzip.GzipFile(fileobj=response) as gz:
        with io.TextIOWrapper(gz, encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    value = json.loads(line)
                    if isinstance(value, dict):
                        yield value

def _title(record: dict) -> str:
    names = record.get("item_name") or []
    value = next((x.get("value", "") for x in names
                  if x.get("language_tag") == "en_US" and x.get("value")), "")
    value = value or next((x.get("value", "") for x in names if x.get("value")), "")
    return re.sub(r"\s+", " ", str(value)).strip()[:35].strip(" -,:;") or "Catalog product"

def _category(record: dict) -> str:
    nodes = record.get("node") or []
    if nodes and isinstance(nodes[0], dict):
        return str(nodes[0].get("node_name") or nodes[0].get("path") or "unknown")
    types = record.get("product_type") or []
    return str(types[0].get("value") or "unknown") if types else "unknown"

def _synthetic_prices(item_id: str) -> tuple[str, str]:
    digest = hashlib.sha256(item_id.encode()).digest()
    price = PRICES[int.from_bytes(digest[:2], "big") % len(PRICES)]
    original = round(price * (1.3, 1.4, 1.5, 1.6)[digest[2] % 4], 2)
    return f"{price:.2f}", f"{original:.2f}"

def _select(limit: int) -> list[dict]:
    selected, seen, categories = [], set(), {}
    cap = max(3, (limit + 4) // 5)
    for shard in range(SHARDS):
        url = f"{BASE}listings/metadata/listings_{shard}.json.gz"
        try:
            for row in _jsonl(url):
                item_id, image_id = str(row.get("item_id") or "").strip(), str(row.get("main_image_id") or "").strip()
                if not item_id or not image_id or item_id in seen:
                    continue
                category = _category(row)
                if categories.get(category, 0) >= cap:
                    continue
                selected.append({"item_id": item_id, "image_id": image_id,
                                 "title": _title(row), "category": category})
                seen.add(item_id)
                categories[category] = categories.get(category, 0) + 1
                if len(selected) == limit:
                    return selected
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            raise RuntimeError(f"ABO listing metadata HTTP {exc.code}: {url}") from exc
    raise RuntimeError(f"Only found {len(selected)} products; requested {limit}")

def _paths(ids: set[str]) -> dict[str, str]:
    found = {}
    url = f"{BASE}images/metadata/images.csv.gz"
    with _open(url) as response, gzip.GzipFile(fileobj=response) as gz:
        with io.TextIOWrapper(gz, encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                key, path = (row.get("image_id") or "").strip(), (row.get("path") or "").strip()
                if key in ids and path:
                    found[key] = path
                    if len(found) == len(ids):
                        break
    if set(found) != ids:
        raise RuntimeError(f"Image metadata missing {len(ids - set(found))} selected IDs")
    return found

def _download(path: str, destination: Path) -> str:
    url = f"{BASE}images/original/{path.lstrip('/')}"
    with _open(url) as response:
        raw = response.read(MAX_BYTES + 1)
    if not raw or len(raw) > MAX_BYTES:
        raise RuntimeError(f"Image empty or larger than 10 MiB; refusing silent compression: {url}")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise RuntimeError(f"Image cannot be decoded: {url}") from exc
    destination.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()

def _import_sample_into(output_dir: Path, limit: int = 20) -> dict:
    if not 1 <= limit <= 100:
        raise ValueError("limit must be in 1..100")
    output_dir = output_dir.expanduser().resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"Refusing to overwrite non-empty directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    image_dir = output_dir / "images"
    image_dir.mkdir()
    products = _select(limit)
    paths = _paths({x["image_id"] for x in products})
    manifest = output_dir / "real_skus.csv"
    rows = []
    fields = ["sku_id", "product_image", "title", "price", "original_price",
              "subtitle", "cta", "style", "logo_image"]
    with manifest.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for index, product in enumerate(products):
            sku = f"ABO-{product['item_id']}"
            source_path = paths[product["image_id"]]
            suffix = Path(source_path).suffix.lower()
            if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
                raise RuntimeError(f"Unsupported image suffix: {source_path}")
            local = f"{sku}{suffix}"
            image_sha = _download(source_path, image_dir / local)
            price, original = _synthetic_prices(product["item_id"])
            writer.writerow({"sku_id": sku, "product_image": f"images/{local}",
                "title": product["title"], "price": price, "original_price": original,
                "subtitle": "公开目录技术验证·模拟价格", "cta": "查看详情",
                "style": STYLES[index % len(STYLES)], "logo_image": ""})
            rows.append(f"| {product['item_id']} | {product['title'].replace('|', '/')} | "
                        f"{product['category'].replace('|', '/')} | {product['image_id']} | {image_sha} |")
    (output_dir / "PUBLIC_DATASET_ATTRIBUTION.md").write_text(
        "# Dataset attribution and limitations\n\n"
        "Dataset: Amazon Berkeley Objects (ABO), managed by Amazon.com.\n"
        "Official page: https://amazon-berkeley-objects.s3.amazonaws.com/index.html\n"
        "License: Creative Commons Attribution 4.0: https://creativecommons.org/licenses/by/4.0/\n"
        "Attribution: Amazon.com and dataset authors listed on the official dataset page.\n"
        f"Retrieved at (UTC): {datetime.now(timezone.utc).isoformat()}\n\n"
        "Changes: selected catalog records, downloaded original images, truncated titles to 35 characters, "
        "and generated deterministic synthetic prices because the dataset is not a current-price feed. "
        "Prices are NOT actual Amazon or Chinese marketplace prices. Keep this cohort local; do not commit it.\n\n"
        "| Item ID | Title | Category | Main image ID | Local image SHA-256 |\n|---|---|---|---|---|\n"
        + "\n".join(rows) + "\n", encoding="utf-8")
    return {"dataset": "Amazon Berkeley Objects", "license": "CC BY 4.0", "count": len(products),
            "category_count": len({x["category"] for x in products}), "manifest": str(manifest),
            "attribution": str(output_dir / "PUBLIC_DATASET_ATTRIBUTION.md"),
            "synthetic_prices": True, "image_hashes_recorded": len(rows)}

def import_sample(output_dir: Path, limit: int = 20) -> dict:
    """Download a cohort atomically so failed network runs leave no partial output."""
    if not 1 <= limit <= 100:
        raise ValueError("limit must be in 1..100")
    output_dir = output_dir.expanduser().resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"Refusing to overwrite non-empty directory: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=".abo-sample-", dir=output_dir.parent) as temp:
        staging_dir = Path(temp) / "cohort"
        result = _import_sample_into(staging_dir, limit)
        if output_dir.exists():
            output_dir.rmdir()
        os.replace(staging_dir, output_dir)

    result["manifest"] = str(output_dir / "real_skus.csv")
    result["attribution"] = str(output_dir / "PUBLIC_DATASET_ATTRIBUTION.md")
    return result


def _existing_sample(output_dir: Path, expected_limit: int | None = None) -> dict | None:
    """Reuse a downloaded cohort, rejecting an explicitly requested size mismatch."""
    output_dir = output_dir.expanduser().resolve()
    manifest = output_dir / "real_skus.csv"
    attribution = output_dir / "PUBLIC_DATASET_ATTRIBUTION.md"
    if not manifest.is_file() or not attribution.is_file():
        return None
    with manifest.open(encoding="utf-8-sig", newline="") as stream:
        count = sum(1 for _ in csv.DictReader(stream))
    if not 1 <= count <= 100:
        raise ValueError("Existing cohort manifest must contain 1..100 SKU rows")
    if expected_limit is not None and count != expected_limit:
        raise ValueError(
            f"Existing cohort has {count} SKUs but --limit {expected_limit} was requested; "
            "use the original limit or choose a fresh --output-dir."
        )
    return {
        "dataset": "Amazon Berkeley Objects",
        "license": "CC BY 4.0",
        "count": count,
        "manifest": str(manifest),
        "attribution": str(attribution),
        "synthetic_prices": True,
        "reused_existing_sample": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a small public ABO cohort for local poster-agent smoke testing")
    parser.add_argument("--limit", type=int, default=None, help="SKU count (default: 20 for a new download; when reusing a cohort, omit to use its recorded row count)")
    parser.add_argument("--output-dir", type=Path, default=Path("data/real_trial/abo_public_sample"))
    parser.add_argument("--apply", action="store_true", help="Generate posters via M5g and audit M5j readiness")
    args = parser.parse_args()
    try:
        imported = _existing_sample(args.output_dir, args.limit) if args.apply else None
        if imported is None:
            imported = import_sample(args.output_dir, args.limit if args.limit is not None else 20)
        print(json.dumps(imported, ensure_ascii=False, indent=2))
        from scripts.real_sku_batch import preflight, run_batch
        manifest = Path(imported["manifest"])
        digest, prepared = preflight(manifest)
        print(json.dumps({"m5g_preflight": "PASS", "sku_count": len(prepared),
                          "manifest_sha256": digest, "generation_applied": args.apply}, ensure_ascii=False, indent=2))
        if not args.apply:
            print("Download and preflight only. Add --apply to generate posters.")
            return 0
        batch_dir = manifest.parent / "batch"
        batch_result = run_batch(manifest, batch_dir, apply=True)
        journal = batch_dir / "batch_import_results.json"
        from evals.native_batch_scope import load_batch_scope
        from evals.native_trial import run_trial
        readiness = run_trial("abo-public-smoke", manifest.parent / "m5j",
                              score=False, batch_scope=load_batch_scope(journal))
        print(json.dumps({"m5g_result": batch_result, "m5j_readiness": readiness,
            "ocr_inference_performed": False, "human_review_performed": False,
            "quality_score_computed": False,
            "warning": "Synthetic prices and readiness audit are not a marketplace quality score."},
            ensure_ascii=False, indent=2))
        return 0
    except (RuntimeError, ValueError, OSError, csv.Error, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
