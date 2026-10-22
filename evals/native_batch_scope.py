"""Validate M5g's local successful import journal for evaluation scoping.

A journal is local operator-supplied metadata, not a signed attestation of
image origin. Never broaden the cohort if a row is invalid or missing.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SCHEMA = "m5g-1"
ID = re.compile(r"^[0-9a-f]{32}$")
SHA = re.compile(r"^[0-9a-f]{64}$")
SKU = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
KEY = re.compile(r"^m5g_[0-9a-f]{56}$")
MAX_JOURNAL_BYTES = 200_000


def load_batch_scope(journal: Path) -> dict:
    path = journal.resolve(strict=True)
    raw = path.read_bytes()
    if not 0 < len(raw) <= MAX_JOURNAL_BYTES:
        raise ValueError("M5g 导入结果文件为空或超过 200 KB")
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("M5g 导入结果不是合法 UTF-8 JSON") from exc
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA or \
            data.get("mode") != "apply" or not isinstance(data.get("rows"), dict) or \
            not isinstance(data.get("manifest_sha256"), str) or \
            not SHA.fullmatch(data["manifest_sha256"]):
        raise ValueError("仅接受 M5g 已执行 --apply 的有效批次结果")

    # M5g writes an incomplete journal after each successful SKU so it can
    # resume safely. A subset must NEVER masquerade as the full experiment.
    # Older M5g journals need an idempotent --apply rerun to be finalized.
    if data.get("completed") is not True:
        raise ValueError("M5g 批次导入尚未完成；请重新运行原 --apply 命令补齐/确认")
    expected = data.get("expected_sku_ids")
    expected_count = data.get("expected_sku_count")
    if type(expected_count) is not int or not 1 <= expected_count <= 100 or \
            not isinstance(expected, list) or len(expected) != expected_count or \
            any(not isinstance(sku, str) or not SKU.fullmatch(sku) for sku in expected) or \
            len(set(expected)) != expected_count:
        raise ValueError("M5g 预期 SKU 清单不合法，拒绝缩小评分分母")
    items = data["rows"]
    if set(items) != set(expected):
        raise ValueError("M5g 批次账本不完整，导入记录与预期 SKU 不一致")
    if not 1 <= len(items) <= 100:
        raise ValueError("批次必须包含 1–100 个实际导入的 SKU")
    sku_by_id: dict[str, str] = {}
    original_product_shas: set[str] = set()
    for sku, record in items.items():
        if not isinstance(sku, str) or not SKU.fullmatch(sku) or not isinstance(record, dict):
            raise ValueError("M5g 批次包含不合法的 SKU 或记录")
        pid = record.get("poster_id")
        if not isinstance(pid, str) or not ID.fullmatch(pid) or pid in sku_by_id:
            raise ValueError("M5g 批次存在非法或重复的海报 ID")
        for k in ("request_sha256", "product_sha256"):
            if not isinstance(record.get(k), str) or not SHA.fullmatch(record[k]):
                raise ValueError(f"{sku}: 缺少有效的 {k}")
        logo = record.get("logo_sha256")
        if logo is not None and (not isinstance(logo, str) or not SHA.fullmatch(logo)):
            raise ValueError(f"{sku}: Logo 摘要不合法")
        if record.get("version") != 1 or not isinstance(record.get("idempotency_key"), str) or \
                not KEY.fullmatch(record["idempotency_key"]):
            raise ValueError(f"{sku}: 不是 M5g 创建的原始版本或幂等键无效")
        sku_by_id[pid] = sku
        original_product_shas.add(record["product_sha256"])

    return {
        "scope_mode": "batch_latest_per_sku",
        "sku_by_id": sku_by_id,
        "source_batch_journal_sha256": hashlib.sha256(raw).hexdigest(),
        "source_manifest_sha256": data["manifest_sha256"],
        "batch_sku_count": len(items),
        "batch_unique_raw_product_sha256_count": len(original_product_shas),
    }
