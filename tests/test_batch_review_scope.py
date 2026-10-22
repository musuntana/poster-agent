"""M5i: only M5g batch journals scope the existing review queue in-browser.

This verifies the integrated API handoff and JavaScript syntax; no paid
model, actual OCR, backend file browsing or new reviewer database.
"""
from __future__ import annotations

import json
import shutil
import subprocess

from fastapi.testclient import TestClient

from app.main import app
from scripts.real_sku_batch import SCHEMA

client = TestClient(app)


def test_batch_journal_contract_matches_browser_filter(tmp_path, monkeypatch, product_bytes):
    from scripts import real_sku_batch as batch
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.setenv("POSTER_EXECUTION_DB", str(tmp_path / "executions.sqlite3"))
    input_dir = tmp_path / "real-skus"
    input_dir.mkdir()
    (input_dir / "item.png").write_bytes(product_bytes)
    manifest = input_dir / "skus.csv"
    manifest.write_text(
        "sku_id,product_image,title,price\n"
        "sku-sample,item.png,电动牙刷,199\n", encoding="utf-8"
    )
    result = batch.run_batch(manifest, tmp_path / "results", apply=True)
    journal = json.loads((tmp_path / "results/batch_import_results.json").read_text())
    assert journal["schema_version"] == SCHEMA == "m5g-1"
    assert journal["mode"] == "apply"
    assert len(journal["manifest_sha256"]) == 64
    record = journal["rows"]["sku-sample"]
    assert len(record["poster_id"]) == 32
    assert record["version"] == 1
    assert len(record["product_sha256"]) == 64
    assert len(record["request_sha256"]) == 64
    queue = client.get("/api/trials/review-queue?reviewer=local-operator")
    assert queue.status_code == 200
    assert {i["poster_id"] for i in queue.json()["items"]} == {record["poster_id"]}
    assert result["poster_ids"]["sku-sample"] == record["poster_id"]


def test_batch_scope_UI_has_safe_client_only_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert 'id="batchReviewFile"' in html
    assert 'id="clearBatchScope"' in html
    assert 'id="batchReviewScopeStatus"' in html
    assert "file.size>200000" in html
    assert "parseBatchReviewJournal(await file.text())" in html
    assert "data.schema_version!=='m5g-1'" in html
    assert "data.mode!=='apply'" in html
    assert "BATCH_POSTER_ID.test(record.poster_id)" in html
    assert "seen.has(record.poster_id)" in html
    assert "data.items.filter(x=>batchReviewScope.ids.has(x.poster_id))" in html
    assert "label.textContent=(sku?" in html
    assert "renderReviewQueue(data)" in html
    assert "fetch(item.poster_url)" in html
    assert "display(poster)" in html
    assert "FileReader" not in html


def test_embedded_javascript_parses_under_node_if_installed(tmp_path):
    node = shutil.which("node")
    if not node:
        return
    html = client.get("/").text
    script = html.split("<script>", 1)[1].split("</script>", 1)[0]
    path = tmp_path / "poster-ui.js"
    path.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [node, "--check", str(path)], capture_output=True,
        text=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stderr
