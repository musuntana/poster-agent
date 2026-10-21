"""M5c: isolated manual labels for actual native 4:5 pixels.

No OCR inference is performed; checks use a real local Pillow-generated PNG.
"""
from __future__ import annotations

import csv
import hashlib
import io

from fastapi.testclient import TestClient

from app import storage
from app.main import app

client = TestClient(app)
REVIEWER = "native-operator"


def _create(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.delenv("POSTER_BRAND_RULES_FILE", raising=False)
    response = client.post("/api/posters",
        files={"product": ("sku.png", product_bytes, "image/png")},
        data={"title": "电动牙刷限时促销", "price": "199",
              "original_price": "399", "subtitle": "品质生活",
              "cta": "立即抢购", "background_mode": "local"})
    assert response.status_code == 201, response.text
    return response.json()["poster_id"]


def _url(pid, version=1):
    return f"/api/posters/{pid}/versions/{version}/exports/feed_4x5_native/human-review"


def _payload(info, reviewer=REVIEWER, defective=None, unsure=None):
    defective = set(defective or [])
    unsure = set(unsure or [])
    return {
        "reviewer": reviewer,
        "image_sha256": info["image_sha256"],
        "source_image_sha256": info["source_image_sha256"],
        "spec_sha256": info["spec_sha256"],
        "asset_sha256": info["asset_sha256"],
        "fields": {
            key: {
                "judgment": ("unsure" if key in unsure else
                             "defective" if key in defective else "correct"),
                "reason": "需要检查" if key in defective else "",
            }
            for key in info["fields_to_review"]
        },
    }


def _csv_rows(response):
    assert response.status_code == 200, response.text
    return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))


def test_context_is_specific_to_native_pixels_and_includes_visual_checks(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _create(tmp_path, monkeypatch, product_bytes)
    native = client.get(_url(pid)).json()
    square = client.get(f"/api/posters/{pid}/versions/1/human-review").json()
    image = client.get(native["image_url"])
    assert image.status_code == 200
    assert image.headers["x-export-size"] == "1080x1350"
    assert native["variant_id"] == "feed_4x5_native"
    assert native["image_sha256"] == hashlib.sha256(image.content).hexdigest()
    assert native["image_sha256"] != square["image_sha256"]
    assert native["spec_sha256"] == square["spec_sha256"]
    assert set(["sku_integrity", "text_legibility", "composition",
                "title", "price", "cta", "subtitle", "original_price"]) == set(native["fields_to_review"])
    assert native["auto_approved"] is False
    assert native["review_count"] == 0


def test_submit_is_append_only_and_latest_review_restores_without_consensus(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _create(tmp_path, monkeypatch, product_bytes)
    info = client.get(_url(pid)).json()
    original_png = storage.version_path(pid, 1).read_bytes()
    first = client.post(_url(pid), json=_payload(info, defective=["sku_integrity"]))
    assert first.status_code == 201, first.text
    second = client.post(_url(pid), json=_payload(info, defective=["composition"]))
    assert second.status_code == 201, second.text
    assert first.json()["review_id"] != second.json()["review_id"]
    third = client.post(_url(pid), json=_payload(info, reviewer="reviewer-B",
                                                defective=["title"]))
    assert third.status_code == 201
    context = client.get(_url(pid) + "?reviewer=" + REVIEWER).json()
    assert context["review_count"] == 3
    assert context["distinct_reviewers"] == 2
    assert context["existing_review"]["review_id"] == second.json()["review_id"]
    assert context["existing_review"]["fields"]["composition"]["judgment"] == "defective"
    assert storage.version_path(pid, 1).read_bytes() == original_png
    records = list((storage.poster_dir(pid) / "versions" /
                    "v1.feed_4x5_native.human_reviews").glob("*.json"))
    assert len(records) == 3
    assert not list((storage.poster_dir(pid) / "versions").glob("v1.human_reviews/*"))


def test_native_export_has_separate_schema_from_square_csv(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _create(tmp_path, monkeypatch, product_bytes)
    info = client.get(_url(pid)).json()
    data = _payload(info, defective=["price", "sku_integrity"], unsure=["composition"])
    assert client.post(_url(pid), json=data).status_code == 201
    native = client.get("/api/annotations/native-4x5/export.csv?reviewer=" + REVIEWER)
    rows = _csv_rows(native)
    assert len(rows) == len(info["fields_to_review"]) - 1
    assert native.headers["x-uncertain-skipped"] == "1"
    assert all(r["variant_id"] == "feed_4x5_native" for r in rows)
    assert all(r["image_sha256"] == info["image_sha256"] for r in rows)
    assert next(r["defective"] for r in rows if r["field"] == "sku_integrity") == "true"
    assert "composition" not in {r["field"] for r in rows}
    # The existing square CSV is not polluted with native labels.
    assert client.get("/api/annotations/export.csv?reviewer=" + REVIEWER).status_code == 422


def test_missing_fields_and_nonexistent_version_rejected(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _create(tmp_path, monkeypatch, product_bytes)
    context = client.get(_url(pid)).json()
    incomplete = _payload(context)
    del incomplete["fields"]["sku_integrity"]
    assert client.post(_url(pid), json=incomplete).status_code == 422
    assert client.post(_url(pid, 2), json=_payload(context)).status_code == 404
    assert client.get(_url(pid, 2)).status_code == 404
    assert client.get("/api/annotations/native-4x5/export.csv?reviewer=bad!*").status_code == 422


def test_image_or_asset_modified_after_context_is_conflict(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _create(tmp_path, monkeypatch, product_bytes)
    ctx = client.get(_url(pid)).json()
    path = storage.poster_dir(pid) / "assets" / "product.png"
    source = path.read_bytes()
    path.write_bytes(source + b"changed")
    assert client.post(_url(pid), json=_payload(ctx)).status_code == 409
    path.write_bytes(source)
    spec = storage.poster_dir(pid) / "versions" / "v1.json"
    text = spec.read_bytes()
    spec.write_bytes(text + b"\n")
    assert client.post(_url(pid), json=_payload(ctx)).status_code == 409
    spec.write_bytes(text)
    assert client.post(_url(pid), json=_payload(ctx)).status_code == 201
    path.write_bytes(source + b"changed")
    assert client.get(_url(pid) + "?reviewer=" + REVIEWER).status_code == 409
    assert client.get("/api/annotations/native-4x5/export.csv?reviewer=" + REVIEWER).status_code == 409


def test_version_and_reviewer_exports_do_not_mix(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _create(tmp_path, monkeypatch, product_bytes)
    one = client.get(_url(pid)).json()
    assert client.post(_url(pid), json=_payload(one)).status_code == 201
    rev = client.post(f"/api/posters/{pid}/revisions", json={"price": "189"})
    assert rev.status_code == 201
    two = client.get(_url(pid, 2)).json()
    assert two["image_sha256"] != one["image_sha256"]
    assert two["review_count"] == 0
    assert client.post(_url(pid, 2), json=_payload(one)).status_code == 409
    assert client.post(_url(pid, 2), json=_payload(two, defective=["price"])).status_code == 201
    csv_response = client.get("/api/annotations/native-4x5/export.csv?reviewer=" + REVIEWER)
    rows = _csv_rows(csv_response)
    assert {(r["poster_id"], r["version"]) for r in rows} == {(pid, "1"), (pid, "2")}
    assert len({(r["version"], r["image_sha256"]) for r in rows}) == 2


def test_no_human_reviews_means_no_approval_and_no_empty_csv(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _create(tmp_path, monkeypatch, product_bytes)
    context = client.get(_url(pid)).json()
    assert context["existing_review"] is None
    assert context["auto_approved"] is False
    assert client.get("/api/annotations/native-4x5/export.csv?reviewer=" + REVIEWER).status_code == 422
    unsure = _payload(context, unsure=context["fields_to_review"])
    assert client.post(_url(pid), json=unsure).status_code == 201
    assert client.get("/api/annotations/native-4x5/export.csv?reviewer=" + REVIEWER).status_code == 422
