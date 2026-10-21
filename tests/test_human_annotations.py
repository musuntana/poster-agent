"""M3c human-label API tests: actual local PNG, version/hash binding and exports.

OCR is intentionally mocked in the bridge test; these tests do not prove
human-review accuracy or PaddleOCR runtime compatibility.
"""
from __future__ import annotations

import io
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from evals.real_reports import evaluate_real_reports, load_labels

client = TestClient(app)


def poster(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.delenv("POSTER_BRAND_RULES_FILE", raising=False)
    resp = client.post("/api/posters",
        files={"product": ("product.png", product_bytes, "image/png")},
        data={
            "title": "双十一优惠", "price": "199", "original_price": "399",
            "subtitle": "限时精选", "cta": "立即抢购", "background_mode": "local",
        })
    assert resp.status_code == 201, resp.text
    return resp.json()["poster_id"]


def route(pid, version=1):
    return f"/api/posters/{pid}/versions/{version}/human-review"


def payload(context, *, price_judgment="correct", reviewer="local-operator"):
    return {
        "reviewer": reviewer,
        "image_sha256": context["image_sha256"],
        "spec_sha256": context["spec_sha256"],
        "fields": {
            name: {"judgment": "unsure" if name == "subtitle"
                   else price_judgment if name == "price"
                   else "correct", "reason": ""}
            for name in context["fields_to_review"]
        },
    }


def test_save_reviews_and_export_latest_selected_reviewer(tmp_path, monkeypatch, product_bytes):
    pid = poster(tmp_path, monkeypatch, product_bytes)
    url = route(pid)
    context = client.get(url+"?reviewer=local-operator").json()
    assert set(context["fields_to_review"]) == {
        "title", "price", "cta", "subtitle", "original_price",
    }
    assert context["review_count"] == 0
    assert context["image_url"].endswith("/image")
    review1 = client.post(url, json=payload(context))
    assert review1.status_code == 201, review1.text
    assert review1.json()["origin"] == "manual_review"

    # A correction appends immutable history, selecting the latest for export.
    review2 = client.post(url, json=payload(context, price_judgment="defective"))
    assert review2.status_code == 201, review2.text
    assert review1.json()["review_id"] != review2.json()["review_id"]
    read = client.get(url+"?reviewer=local-operator").json()
    assert read["review_count"] == 2
    assert read["existing_review"]["review_id"] == review2.json()["review_id"]

    resp = client.get("/api/annotations/export.csv?reviewer=local-operator")
    assert resp.status_code == 200, resp.text
    assert resp.headers["x-labels-exported"] == "4"  # unsure subtitle excluded
    assert resp.headers["x-uncertain-skipped"] == "1"
    annotation_file = tmp_path / "labels.csv"
    annotation_file.write_bytes(resp.content)
    labels = load_labels(annotation_file)
    assert len(labels) == 4
    assert len({row["field"] for row in labels}) == 4
    assert next(row for row in labels if row["field"] == "price")["defective"] is True
    assert all(row["reviewer"] == "local-operator" for row in labels)


def test_review_bridge_to_existing_ocr_evaluator(tmp_path, monkeypatch, product_bytes):
    pid = poster(tmp_path, monkeypatch, product_bytes)
    context = client.get(route(pid)).json()
    assert client.post(route(pid), json=payload(context)).status_code == 201

    # Mock real PaddleOCR integration while leaving actual source PNG + hashes intact.
    monkeypatch.setattr("app.acceptance.recognize", lambda image: [
        {"text": text, "score": 0.99}
        for text in ["双十一优惠", "¥199", "¥399", "限时精选", "立即抢购"]
    ])
    report = client.post(
        f"/api/posters/{pid}/versions/1/acceptance", json={"ocr_mode": "paddle"}
    )
    assert report.status_code == 200, report.text
    labels = client.get("/api/annotations/export.csv?reviewer=local-operator")
    assert labels.status_code == 200
    path = tmp_path / "manual.csv"
    path.write_bytes(labels.content)
    results = evaluate_real_reports(path, tmp_path / "report_outputs", "paddle")
    assert results["field_labels"] == 4
    assert results["metrics"]["confusion"]["TN"] == 4
    assert (tmp_path / "report_outputs/real_summary.json").exists()


def test_review_rejects_stale_hash_and_missing_fields(tmp_path, monkeypatch, product_bytes):
    pid = poster(tmp_path, monkeypatch, product_bytes)
    context = client.get(route(pid)).json()
    wrong_hash = payload(context)
    wrong_hash["image_sha256"] = "0" * 64
    assert client.post(route(pid), json=wrong_hash).status_code == 409

    missing = payload(context)
    del missing["fields"]["cta"]
    assert client.post(route(pid), json=missing).status_code == 422
    assert client.get(route(pid)).json()["review_count"] == 0

    bad_reviewer = payload(context, reviewer="../../secrets")
    assert client.post(route(pid), json=bad_reviewer).status_code == 422
    assert client.get("/api/annotations/export.csv?reviewer=not-me").status_code == 422


def test_version_isolation_and_immutable_review_history(tmp_path, monkeypatch, product_bytes):
    pid = poster(tmp_path, monkeypatch, product_bytes)
    context1 = client.get(route(pid)).json()
    assert client.post(route(pid), json=payload(context1)).status_code == 201
    changed = client.post(f"/api/posters/{pid}/revisions", json={"price": "189"})
    assert changed.status_code == 201, changed.text
    context2 = client.get(route(pid, 2)).json()
    assert context2["image_sha256"] != context1["image_sha256"]
    assert context2["review_count"] == 0
    assert client.post(route(pid, 2), json=payload(context1)).status_code == 409
    assert client.post(route(pid, 2), json=payload(context2)).status_code == 201
    export = client.get("/api/annotations/export.csv?reviewer=local-operator")
    assert export.status_code == 200
    output = tmp_path / "all.csv"
    output.write_bytes(export.content)
    labels = load_labels(output)
    assert len(labels) == 8
    assert {(r["poster_id"], r["version"]) for r in labels} == {(pid, 1), (pid, 2)}


def test_reviewer_specific_exports_do_not_merge_independent_opinions(tmp_path, monkeypatch, product_bytes):
    pid = poster(tmp_path, monkeypatch, product_bytes)
    context = client.get(route(pid)).json()
    assert client.post(route(pid), json=payload(context, reviewer="reviewer-A")).status_code == 201
    assert client.post(route(pid), json=payload(context, reviewer="reviewer-B", price_judgment="defective")).status_code == 201
    a = client.get("/api/annotations/export.csv?reviewer=reviewer-A")
    b = client.get("/api/annotations/export.csv?reviewer=reviewer-B")
    assert a.status_code == b.status_code == 200
    pa, pb = tmp_path / "a.csv", tmp_path / "b.csv"
    pa.write_bytes(a.content)
    pb.write_bytes(b.content)
    assert not next(r for r in load_labels(pa) if r["field"] == "price")["defective"]
    assert next(r for r in load_labels(pb) if r["field"] == "price")["defective"]
    # Multiple reviewers do not become a fake consensus.
    assert client.get(route(pid)+"?reviewer=reviewer-A").json()["distinct_reviewers"] == 2


def test_nonexistent_version_is_404(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    assert client.get("/api/posters/" + "0"*32 + "/versions/1/human-review").status_code == 404
