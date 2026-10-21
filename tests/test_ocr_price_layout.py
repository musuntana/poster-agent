"""M3e spatial-price regressions: OCR boxes are MOCKS, not PaddleOCR measurements."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.ocr_price_layout import assess_price_zones
from app.schemas import PosterSpec

client = TestClient(app)


def make_spec(original="399"):
    return PosterSpec(title="双十一限时优惠", price="199",
                      original_price=original, cta="立即抢购")


def line(text, bbox, score=0.99):
    return {"text": text, "bbox": bbox, "score": score}


def checks(lines, original="399", status="OK"):
    return {c["name"]: c for c in assess_price_zones(make_spec(original), status, lines)}


def clean():
    return [
        line("¥199", [75, 672, 280, 752]),
        line("原价 ¥399", [77, 802, 330, 836]),
    ]


def test_correct_amounts_in_correct_regions_pass():
    data = checks(clean())
    assert data["ocr_price_position"]["status"] == "PASS"
    assert data["ocr_original_price_position"]["status"] == "PASS"
    assert data["ocr_price_position"]["evidence"]["located_amounts"] == ["199"]


def test_swapped_prices_fail_even_if_global_text_checks_would_pass():
    data = checks([
        line("¥399", [75, 672, 280, 752]),
        line("原价 ¥199", [77, 802, 330, 836]),
    ])
    assert data["ocr_price_position"]["status"] == "FAIL"
    assert data["ocr_original_price_position"]["status"] == "FAIL"


def test_conflicting_price_in_sale_region_fails_even_with_correct_amount():
    data = checks(clean() + [line("¥299", [80, 698, 300, 754])])
    assert data["ocr_price_position"]["status"] == "FAIL"
    assert data["ocr_price_position"]["evidence"]["located_amounts"] == ["199", "299"]
    assert data["ocr_original_price_position"]["status"] == "PASS"


def test_expected_price_only_in_original_region_is_not_a_sale_pass():
    data = checks([line("¥199", [77, 802, 330, 836])], original=None)
    assert data["ocr_price_position"]["status"] == "FAIL"
    assert "ocr_original_price_position" not in data
    assert data["ocr_price_position"]["evidence"]["outside_expected_count"] == 1


def test_missing_boxes_low_score_and_invalid_boxes_abstain():
    no_box = checks([line("¥199", None), line("¥399", None)])
    assert no_box["ocr_price_position"]["status"] == "UNVERIFIED"
    low_score = checks([line("¥199", [75, 672, 280, 752], score=0.13)])
    assert low_score["ocr_price_position"]["status"] == "UNVERIFIED"
    invalid_box = checks([line("¥199", [75, 672, 280, 2000])])
    assert invalid_box["ocr_price_position"]["status"] == "UNVERIFIED"
    missing_score = checks([line("¥199", [75, 672, 280, 752], score=None)])
    assert missing_score["ocr_price_position"]["status"] == "UNVERIFIED"


def test_uncertain_currency_token_prevents_unearned_pass():
    data = checks(clean() + [line("¥299", None)])
    assert data["ocr_price_position"]["status"] == "UNVERIFIED"
    assert data["ocr_original_price_position"]["status"] == "UNVERIFIED"


def test_wrong_decimal_price_in_sale_area_fails():
    data = checks([line("¥199.90", [75, 672, 300, 753])], original=None)
    assert data["ocr_price_position"]["status"] == "FAIL"


def test_ocr_not_run_cannot_pass_even_with_fake_lines():
    data = checks(clean(), status="NOT_RUN")
    assert data["ocr_price_position"]["status"] == "UNVERIFIED"
    assert data["ocr_original_price_position"]["status"] == "UNVERIFIED"


def test_acceptance_gate_blocks_swapped_money_coordinates(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.delenv("POSTER_BRAND_RULES_FILE", raising=False)
    created = client.post(
        "/api/posters",
        files={"product": ("sku.png", product_bytes, "image/png")},
        data={"title": "双十一限时优惠", "price": "199",
              "original_price": "399", "cta": "立即抢购", "background_mode": "local"},
    )
    assert created.status_code == 201, created.text
    poster_id = created.json()["poster_id"]

    # These fake OCR strings include BOTH correct prices but in the WRONG regions.
    monkeypatch.setattr("app.acceptance.recognize", lambda image_path: [
        line("双十一限时优惠", [75, 270, 415, 340]),
        line("¥399", [75, 672, 280, 752]),
        line("原价 ¥199", [77, 802, 330, 836]),
        line("立即抢购", [78, 880, 380, 930]),
    ])
    result = client.post(
        f"/api/posters/{poster_id}/versions/1/acceptance",
        json={"ocr_mode": "paddle"},
    )
    assert result.status_code == 200, result.text
    report = result.json()
    by_name = {c["name"]: c for c in report["checks"]}
    assert by_name["ocr_price"]["status"] == "PASS"  # legacy text-only result
    assert by_name["ocr_original_price"]["status"] == "PASS"
    assert by_name["ocr_price_position"]["status"] == "FAIL"
    assert by_name["ocr_original_price_position"]["status"] == "FAIL"
    assert report["auto_checks"] == "FAIL"
    assert report["verdict"] == "BLOCKED"


def test_acceptance_gate_requires_box_evidence_for_complete_pass(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    brand = tmp_path / "brand.json"
    brand.write_text('{"forbidden_terms":[]}', encoding="utf-8")
    monkeypatch.setenv("POSTER_BRAND_RULES_FILE", str(brand))
    created = client.post("/api/posters",
        files={"product": ("sku.png", product_bytes, "image/png"),
               "logo": ("logo.png", product_bytes, "image/png")},
        data={"title": "双十一限时优惠", "price": "199",
              "original_price": "399", "cta": "立即抢购",
              "subtitle": "", "background_mode": "local"})
    assert created.status_code == 201, created.text
    poster_id = created.json()["poster_id"]
    monkeypatch.setattr("app.acceptance.recognize", lambda _: [
        {"text": "双十一限时优惠", "score": .99},
        {"text": "¥199", "score": .99},
        {"text": "¥399", "score": .99},
        {"text": "立即抢购", "score": .99},
    ])
    report = client.post(
        f"/api/posters/{poster_id}/versions/1/acceptance",
        json={"ocr_mode": "paddle"}).json()
    assert report["auto_checks"] == "INCOMPLETE"
    assert report["verdict"] == "REVIEW_REQUIRED"
    statuses = {c["name"]: c["status"] for c in report["checks"]}
    assert statuses["ocr_price"] == "PASS"
    assert statuses["ocr_price_position"] == "UNVERIFIED"
