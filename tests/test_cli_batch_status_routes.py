"""Boundary validation and read-only contract for CLI batch status."""
from __future__ import annotations

import json
from importlib import import_module

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _state(*, completed=False, rows=None):
    ids = ["sku-one", "sku-two"]
    return {
        "schema_version": "m5g-1",
        "mode": "apply",
        "completed": completed,
        "manifest_sha256": "a" * 64,
        "expected_sku_ids": ids,
        "expected_sku_count": len(ids),
        "rows": rows or {},
    }


def test_cli_batch_status_is_read_only_and_reports_partial_progress(tmp_path, monkeypatch):
    routes = import_module("app.cli_batch_status_routes")
    journal = tmp_path / "batch_import_results.json"
    monkeypatch.setattr(routes, "JOURNAL_PATH", journal)

    missing = client.get("/api/cli-batches/status")
    assert missing.status_code == 200
    assert missing.json()["available"] is False

    state = _state(rows={
        "sku-one": {"poster_id": "b" * 32, "version": 1,
                    "request_sha256": "c" * 64},
    })
    journal.write_text(json.dumps(state), encoding="utf-8")
    partial = client.get("/api/cli-batches/status")
    assert partial.status_code == 200, partial.text
    body = partial.json()
    assert body["source"] == "cli_default_journal"
    assert body["completed"] is False
    assert body["completed_sku_count"] == 1
    assert body["pending_sku_count"] == 1
    assert body["rows"] == [
        {"sku_id": "sku-one", "status": "COMPLETED"},
        {"sku_id": "sku-two", "status": "PENDING"},
    ]
    assert str(tmp_path) not in partial.text
    assert not (tmp_path / "posters").exists()


def test_cli_batch_status_requires_completion_flag_and_every_sku(tmp_path, monkeypatch):
    routes = import_module("app.cli_batch_status_routes")
    journal = tmp_path / "batch_import_results.json"
    monkeypatch.setattr(routes, "JOURNAL_PATH", journal)
    state = _state(rows={
        "sku-one": {"poster_id": "b" * 32, "version": 1},
        "sku-two": {"poster_id": "c" * 32, "version": 1},
    })
    journal.write_text(json.dumps(state), encoding="utf-8")
    assert client.get("/api/cli-batches/status").json()["completed"] is False

    state["completed"] = True
    journal.write_text(json.dumps(state), encoding="utf-8")
    result = client.get("/api/cli-batches/status")
    assert result.status_code == 200
    assert result.json()["completed"] is True
    assert result.json()["pending_sku_count"] == 0


def test_cli_batch_status_rejects_malformed_json_and_non_object_payload(tmp_path, monkeypatch):
    routes = import_module("app.cli_batch_status_routes")
    journal = tmp_path / "batch_import_results.json"
    monkeypatch.setattr(routes, "JOURNAL_PATH", journal)

    journal.write_text("{broken", encoding="utf-8")
    assert client.get("/api/cli-batches/status").status_code == 422
    journal.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    assert client.get("/api/cli-batches/status").status_code == 422
    journal.write_text(json.dumps({"schema_version": "m5g-1"}), encoding="utf-8")
    assert client.get("/api/cli-batches/status").status_code == 422


def test_cli_batch_status_rejects_unhashable_sku_ids_and_invalid_rows(tmp_path, monkeypatch):
    routes = import_module("app.cli_batch_status_routes")
    journal = tmp_path / "batch_import_results.json"
    monkeypatch.setattr(routes, "JOURNAL_PATH", journal)

    invalid_ids = _state()
    invalid_ids["expected_sku_ids"] = [["not", "hashable"]]
    invalid_ids["expected_sku_count"] = 1
    journal.write_text(json.dumps(invalid_ids), encoding="utf-8")
    assert client.get("/api/cli-batches/status").status_code == 422

    invalid_row = _state(rows={"sku-one": {"poster_id": "../secret", "version": 1}})
    journal.write_text(json.dumps(invalid_row), encoding="utf-8")
    assert client.get("/api/cli-batches/status").status_code == 422


def test_cli_batch_status_does_not_collide_with_web_batch_routes(tmp_path, monkeypatch):
    routes = import_module("app.cli_batch_status_routes")
    journal = tmp_path / "batch_import_results.json"
    monkeypatch.setattr(routes, "JOURNAL_PATH", journal)
    journal.write_text(json.dumps(_state()), encoding="utf-8")

    assert client.get("/api/cli-batches/status").status_code == 200
    # /api/batches/{batch_id} remains the web batch route and does not take the CLI endpoint.
    assert client.get("/api/batches/" + "d" * 32).status_code == 404
