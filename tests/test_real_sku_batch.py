"""M5g: batch real-SKU workflow uses actual FastAPI/Pillow; zero model calls."""
from __future__ import annotations

import csv
import io
import json

import pytest
from PIL import Image

from app import storage
from evals.native_trial import audit_trial
from evals.native_batch_scope import load_batch_scope
from scripts import real_sku_batch as batch


def _png(color):
    img = Image.new("RGBA", (128, 160), color)
    stream = io.BytesIO()
    img.save(stream, "PNG")
    return stream.getvalue()


def _manifest(folder, entries):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "real_skus.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        fields = ["sku_id", "product_image", "title", "price",
                  "original_price", "logo_image"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(entries)
    return path


@pytest.fixture
def batch_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    samples = tmp_path / "samples"
    samples.mkdir()
    (samples / "sku_one.png").write_bytes(_png((60, 110, 195, 255)))
    (samples / "sku_two.png").write_bytes(_png((195, 70, 45, 255)))
    (samples / "logo.png").write_bytes(_png((50, 200, 100, 180)))
    return samples


def _rows():
    return [
        {"sku_id": "sku-one", "product_image": "sku_one.png",
         "title": "电动牙刷促销", "price": "199",
         "original_price": "399", "logo_image": "logo.png"},
        {"sku_id": "sku-two", "product_image": "sku_two.png",
         "title": "智能生活好物", "price": "89",
         "original_price": "129", "logo_image": ""},
    ]


def test_default_preflight_checks_entire_cohort_without_creating_posters(
    batch_dir, tmp_path, monkeypatch,
):
    manifest = _manifest(batch_dir, _rows())
    output = tmp_path / "results"
    plan = batch.run_batch(manifest, output)
    assert plan["mode"] == "check"
    assert plan["total_skus"] == 2
    assert plan["unique_source_files"] == 2
    assert plan["no_paid_model"] is True
    assert (output / "batch_preflight.json").is_file()
    assert not (output / "batch_import_results.json").exists()
    assert not storage.root().exists()
    assert not (tmp_path / "executions.sqlite3").exists()


def test_apply_creates_two_local_posters_and_rerun_reuses_ids(
    batch_dir, tmp_path, monkeypatch,
):
    manifest = _manifest(batch_dir, _rows())
    output = tmp_path / "results"
    def paid_model_forbidden(*a, **kw):
        raise AssertionError("Qwen must NEVER be called by local batch")
    monkeypatch.setattr("app.main.generate_ai_background", paid_model_forbidden)
    first = batch.run_batch(manifest, output, apply=True)
    assert first["created_or_reused"] == 2
    assert len(set(first["poster_ids"].values())) == 2
    assert len(list(storage.root().iterdir())) == 2
    journal = json.loads((output / "batch_import_results.json").read_text())
    assert journal["completed"] is True
    assert journal["expected_sku_count"] == 2
    assert journal["expected_sku_ids"] == ["sku-one", "sku-two"]
    assert set(journal["rows"]) == set(journal["expected_sku_ids"])
    assert load_batch_scope(output / "batch_import_results.json")["batch_sku_count"] == 2
    for poster in first["poster_ids"].values():
        meta = storage.view(poster)
        assert meta["spec"]["background_mode"] == "local"
        assert meta["version"] == 1
        assert storage.version_path(poster, 1).read_bytes().startswith(b"\x89PNG")
        native = audit_trial("trial-reviewer")
        assert native[0]["status_counts"]["NEEDS_REVIEW"] == 2
    second = batch.run_batch(manifest, output, apply=True)
    assert second["poster_ids"] == first["poster_ids"]
    assert len(list(storage.root().iterdir())) == 2
    assets = storage.poster_dir(first["poster_ids"]["sku-one"]) / "assets"
    assert (assets / "logo.png").is_file()


def test_crash_after_first_row_then_resume_without_duplication(
    batch_dir, tmp_path, monkeypatch,
):
    manifest = _manifest(batch_dir, _rows())
    output = tmp_path / "results"
    original = batch._file_parts
    called = {"n": 0}
    def broken_second(item):
        called["n"] += 1
        if called["n"] == 2:
            raise batch.BatchError("simulated operator interruption")
        return original(item)
    monkeypatch.setattr(batch, "_file_parts", broken_second)
    with pytest.raises(batch.BatchError, match="interruption"):
        batch.run_batch(manifest, output, apply=True)
    journal = output / "batch_import_results.json"
    state = json.loads(journal.read_text())
    assert len(state["rows"]) == 1
    assert state["completed"] is False
    assert state["expected_sku_count"] == 2
    with pytest.raises(ValueError, match="尚未完成"):
        load_batch_scope(journal)
    # A forged completed=True flag cannot silently shrink the denominator.
    journal.write_text(json.dumps({**state, "completed": True}), encoding="utf-8")
    with pytest.raises(ValueError, match="不完整"):
        load_batch_scope(journal)
    journal.write_text(json.dumps(state), encoding="utf-8")
    poster = state["rows"]["sku-one"]["poster_id"]
    monkeypatch.setattr(batch, "_file_parts", original)
    result = batch.run_batch(manifest, output, apply=True)
    assert result["poster_ids"]["sku-one"] == poster
    assert len(list(storage.root().iterdir())) == 2
    assert load_batch_scope(journal)["batch_sku_count"] == 2
    assert json.loads(journal.read_text())["completed"] is True


def test_old_complete_journal_requires_idempotent_refinalization(
    batch_dir, tmp_path,
):
    manifest = _manifest(batch_dir, _rows())
    output = tmp_path / "results"
    first = batch.run_batch(manifest, output, apply=True)
    path = output / "batch_import_results.json"
    state = json.loads(path.read_text())
    for key in ("completed", "expected_sku_ids", "expected_sku_count"):
        state.pop(key)
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="尚未完成"):
        load_batch_scope(path)

    after = batch.run_batch(manifest, output, apply=True)
    assert after["poster_ids"] == first["poster_ids"]
    assert len(list(storage.root().iterdir())) == 2
    assert load_batch_scope(path)["batch_sku_count"] == 2


def test_resume_rejects_forged_or_foreign_expected_cohort(batch_dir, tmp_path):
    manifest = _manifest(batch_dir, _rows())
    output = tmp_path / "results"
    batch.run_batch(manifest, output, apply=True)
    path = output / "batch_import_results.json"
    state = json.loads(path.read_text())
    state["expected_sku_ids"] = ["sku-two", "sku-one"]
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(batch.BatchError, match="预期 SKU"):
        batch.run_batch(manifest, output, apply=True)



def test_lost_local_journal_recovers_from_existing_execution_ledger(
    batch_dir, tmp_path,
):
    manifest = _manifest(batch_dir, _rows()[:1])
    output = tmp_path / "results"
    first = batch.run_batch(manifest, output, apply=True)
    (output / "batch_import_results.json").unlink()
    # The existing API idempotency database still knows the successful result.
    again = batch.run_batch(manifest, output, apply=True)
    assert again["poster_ids"] == first["poster_ids"]
    assert len(list(storage.root().iterdir())) == 1


def test_existing_sku_cannot_be_overwritten_by_changed_manifest(
    batch_dir, tmp_path,
):
    manifest = _manifest(batch_dir, _rows()[:1])
    output = tmp_path / "results"
    batch.run_batch(manifest, output, apply=True)
    updated = _rows()[:1]
    updated[0]["price"] = "299"
    _manifest(batch_dir, updated)
    with pytest.raises(batch.BatchError, match="清单已经变化"):
        batch.run_batch(manifest, output, apply=True)
    assert len(list(storage.root().iterdir())) == 1


def test_preflight_rejects_invalid_rows_before_generating_any_poster(
    batch_dir, tmp_path,
):
    rows = _rows()
    rows[1]["price"] = "0"  # Pydantic rejects before applying first row
    manifest = _manifest(batch_dir, rows)
    with pytest.raises(batch.BatchError, match="第 3 行"):
        batch.run_batch(manifest, tmp_path / "out", apply=True)
    assert not storage.root().exists()


def test_preflight_rejects_traversal_and_unknown_columns(
    batch_dir, tmp_path,
):
    rows = _rows()[:1]
    rows[0]["product_image"] = "../outside.png"
    (tmp_path / "outside.png").write_bytes(_png((20, 80, 60, 255)))
    manifest = _manifest(batch_dir, rows)
    with pytest.raises(batch.BatchError, match="目录之外"):
        batch.preflight(manifest)
    # The caller cannot set any AI mode in the CSV.
    with manifest.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["sku_id", "product_image", "title", "price", "background_mode"]
        )
        writer.writeheader()
        writer.writerow({"sku_id": "sku-one", "product_image": "sku_one.png",
                         "title": "商品促销", "price": "199", "background_mode": "ai"})
    with pytest.raises(batch.BatchError, match="background_mode"):
        batch.preflight(manifest)


def test_preflight_rejects_crafted_big_pixel_image_before_model(
    batch_dir, tmp_path,
):
    # Reuse an existing synthetic bad header with valid PNG CRC instead of
    # allocating 5000x5000 pixels in CI.
    import struct
    import zlib
    raw = bytearray((batch_dir / "sku_one.png").read_bytes())
    raw[16:24] = struct.pack(">II", 5000, 5000)
    raw[29:33] = struct.pack(">I", zlib.crc32(raw[12:29]) & 0xFFFFFFFF)
    (batch_dir / "sku_one.png").write_bytes(raw)
    manifest = _manifest(batch_dir, _rows()[:1])
    with pytest.raises(batch.BatchError, match="HTTP 413"):
        batch.run_batch(manifest, tmp_path / "out", apply=True)
    assert not storage.root().exists()


def test_preflight_rejects_duplicate_ids_and_more_than_100(
    batch_dir,
):
    manifest = _manifest(batch_dir, [_rows()[0], _rows()[0]])
    with pytest.raises(batch.BatchError, match="重复"):
        batch.preflight(manifest)
    manifest = _manifest(batch_dir, [
        {**_rows()[0], "sku_id": f"sku-{i}"} for i in range(101)
    ])
    with pytest.raises(batch.BatchError, match="1–100"):
        batch.preflight(manifest)
