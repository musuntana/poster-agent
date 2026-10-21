"""M3a Acceptance Gate: per-version persisted reports, never fake OCR results."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _create(tmp_path, monkeypatch, product_bytes, with_logo=True, title="电动牙刷促销"):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    files = {"product": ("product.png", product_bytes, "image/png")}
    if with_logo:
        files["logo"] = ("logo.png", product_bytes, "image/png")
    response = client.post("/api/posters", files=files,
        data={"title": title, "price": "199", "original_price": "399",
              "cta": "立即抢购", "subtitle": "品质生活 · 限时优惠"})
    assert response.status_code == 201, response.text
    return response.json()["poster_id"]


def _url(poster_id, version=1):
    return f"/api/posters/{poster_id}/versions/{version}/acceptance"


def test_no_ocr_never_claims_visual_text_pass(tmp_path, monkeypatch, product_bytes):
    monkeypatch.delenv("POSTER_BRAND_RULES_FILE", raising=False)
    poster_id = _create(tmp_path, monkeypatch, product_bytes)
    response = client.post(_url(poster_id), json={"ocr_mode": "none"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ocr_status"] == "NOT_RUN"
    assert result["auto_checks"] == "INCOMPLETE"
    assert result["verdict"] == "REVIEW_REQUIRED"
    assert result["requires_human_review"] is True
    checks = {check["name"]: check for check in result["checks"]}
    assert checks["image_dimensions"]["status"] == "PASS"
    assert checks["source_price"]["status"] == "PASS"
    assert checks["ocr_price"]["status"] == "UNVERIFIED"
    assert checks["forbidden_terms_in_spec"]["status"] == "UNVERIFIED"
    saved = client.get(_url(poster_id))
    assert saved.status_code == 200
    assert saved.json()["image_sha256"] == result["image_sha256"]
    assert len(result["image_sha256"]) == 64


def test_mocked_ocr_and_brand_rules_can_pass_checks_but_still_need_review(tmp_path, monkeypatch, product_bytes):
    policy = tmp_path / "brand-rules.json"
    policy.write_text('{"forbidden_terms":["国家级","保证治愈"]}', encoding="utf-8")
    monkeypatch.setenv("POSTER_BRAND_RULES_FILE", str(policy))
    poster_id = _create(tmp_path, monkeypatch, product_bytes)
    def fake_ocr(image_path):
        assert image_path.is_file()
        return [{"text": t, "score": .98, "bbox": None}
                for t in ["电动牙刷促销", "¥199", "¥399", "立即抢购", "品质生活 · 限时优惠"]]
    monkeypatch.setattr("app.acceptance.recognize", fake_ocr)
    result = client.post(_url(poster_id), json={"ocr_mode": "paddle"}).json()
    assert result["ocr_status"] == "OK"
    assert result["auto_checks"] == "PASS"
    assert result["verdict"] == "REVIEW_REQUIRED"
    assert all(check["status"] == "PASS" for check in result["checks"])
    assert client.get(_url(poster_id) + "?ocr_mode=paddle").status_code == 200


def test_ocr_wrong_price_blocks_and_can_be_reviewed(tmp_path, monkeypatch, product_bytes):
    policy = tmp_path / "brand-rules.json"
    policy.write_text('{"forbidden_terms":[]}', encoding="utf-8")
    monkeypatch.setenv("POSTER_BRAND_RULES_FILE", str(policy))
    poster_id = _create(tmp_path, monkeypatch, product_bytes)
    monkeypatch.setattr("app.acceptance.recognize", lambda path: [
        {"text": "电动牙刷促销", "score": .9}, {"text": "¥189", "score": .9},
        {"text": "¥399", "score": .9}, {"text": "立即抢购", "score": .9},
        {"text": "品质生活·限时优惠", "score": .9},
    ])
    report = client.post(_url(poster_id), json={"ocr_mode": "paddle"}).json()
    assert report["auto_checks"] == "FAIL"
    assert report["verdict"] == "BLOCKED"
    price_check = next(c for c in report["checks"] if c["name"] == "ocr_price")
    assert price_check["status"] == "FAIL"
    assert price_check["evidence"]["expected"] == "¥199"


def test_unavailable_ocr_and_absent_logo_are_unverified(tmp_path, monkeypatch, product_bytes):
    from app.ocr_provider import OCRUnavailable
    monkeypatch.delenv("POSTER_BRAND_RULES_FILE", raising=False)
    poster_id = _create(tmp_path, monkeypatch, product_bytes, with_logo=False)
    def unavailable(_):
        raise OCRUnavailable("PaddleOCR not installed")
    monkeypatch.setattr("app.acceptance.recognize", unavailable)
    report = client.post(_url(poster_id), json={"ocr_mode": "paddle"}).json()
    assert report["ocr_status"] == "UNAVAILABLE"
    assert report["auto_checks"] == "INCOMPLETE"
    assert next(c for c in report["checks"] if c["name"] == "logo_asset_present")["status"] == "UNVERIFIED"
    assert next(c for c in report["checks"] if c["name"] == "ocr_price")["status"] == "UNVERIFIED"


def test_forbidden_terms_blocks_spec(tmp_path, monkeypatch, product_bytes):
    policy = tmp_path / "brand-rules.json"
    policy.write_text('{"forbidden_terms":["顶级"]}', encoding="utf-8")
    monkeypatch.setenv("POSTER_BRAND_RULES_FILE", str(policy))
    poster_id = _create(tmp_path, monkeypatch, product_bytes, title="顶级电动牙刷促销")
    report = client.post(_url(poster_id), json={"ocr_mode": "none"}).json()
    assert report["verdict"] == "BLOCKED"
    c = next(c for c in report["checks"] if c["name"] == "forbidden_terms_in_spec")
    assert c["evidence"]["violations"] == ["顶级"]


def test_reports_are_version_specific_and_invalid_inputs_rejected(tmp_path, monkeypatch, product_bytes):
    monkeypatch.delenv("POSTER_BRAND_RULES_FILE", raising=False)
    poster_id = _create(tmp_path, monkeypatch, product_bytes)
    assert client.get(_url(poster_id)).status_code == 404
    report1 = client.post(_url(poster_id), json={"ocr_mode": "none"}).json()
    change = client.post(f"/api/posters/{poster_id}/revisions", json={"price": "189"})
    assert change.status_code == 201
    assert client.get(_url(poster_id, version=2)).status_code == 404
    report2 = client.post(_url(poster_id, version=2), json={"ocr_mode": "none"}).json()
    assert report1["image_sha256"] != report2["image_sha256"]
    assert report1["spec_sha256"] != report2["spec_sha256"]
    assert client.get(_url(poster_id)).json()["image_sha256"] == report1["image_sha256"]
    assert client.post(_url(poster_id), json={"ocr_mode": "fake"}).status_code == 422
    assert client.post(_url("not-a-valid-id"), json={"ocr_mode": "none"}).status_code == 404
    assert client.get(_url(poster_id, version=555)).status_code == 404
