"""M5e local cohort readiness: real Pillow pixels, fake OCR contract only.

No customer images, no OCR model downloads and no network inference.
"""
from __future__ import annotations

import json
import hashlib

import pytest
from fastapi.testclient import TestClient

from app import storage
from app.main import app
from evals.native_trial import audit_trial, run_trial

client = TestClient(app)
REVIEWER = "trial-reviewer"


def _poster(tmp_path, monkeypatch, product_bytes):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    monkeypatch.delenv("POSTER_BRAND_RULES_FILE", raising=False)
    response = client.post(
        "/api/posters",
        files={"product": ("sku.png", product_bytes, "image/png")},
        data={"title": "电动牙刷促销", "price": "199",
              "original_price": "399", "subtitle": "限时福利",
              "cta": "立即购买", "background_mode": "local"},
    )
    assert response.status_code == 201, response.text
    return response.json()["poster_id"]


def _review(pid, version=1, reviewer=REVIEWER, unsure=()):
    url = f"/api/posters/{pid}/versions/{version}/exports/feed_4x5_native/human-review"
    context = client.get(url).json()
    assert context["variant_id"] == "feed_4x5_native"
    raw = {
        "reviewer": reviewer,
        "image_sha256": context["image_sha256"],
        "source_image_sha256": context["source_image_sha256"],
        "spec_sha256": context["spec_sha256"],
        "asset_sha256": context["asset_sha256"],
        "fields": {
            field: {
                "judgment": "unsure" if field in unsure else "correct",
                "reason": "",
            }
            for field in context["fields_to_review"]
        },
    }
    response = client.post(url, json=raw)
    assert response.status_code == 201, response.text


def _ocr(path):
    from PIL import Image
    with Image.open(path) as img:
        assert img.size == (1080, 1350)
    return [
        {"text": "电动牙刷促销", "score": 0.99, "bbox": [70, 282, 420, 338]},
        {"text": "限时福利", "score": 0.99, "bbox": [70, 350, 252, 384]},
        {"text": "¥199", "score": 0.99, "bbox": [68, 880, 285, 980]},
        {"text": "原价 ¥399", "score": 0.99, "bbox": [69, 1023, 300, 1071]},
        {"text": "立即购买", "score": 0.99, "bbox": [84, 1116, 370, 1160]},
    ]


def _run_ocr(pid, monkeypatch, version=1):
    monkeypatch.setattr("app.native_acceptance.recognize", _ocr)
    r = client.post(
        f"/api/posters/{pid}/versions/{version}/exports/feed_4x5_native/acceptance",
        json={"ocr_mode": "paddle"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ocr_status"] == "OK"


def test_empty_data_returns_honest_audit_without_model_inference(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    summary, rows = audit_trial(REVIEWER)
    assert not rows
    assert summary["total_versions"] == 0
    assert summary["ready_versions"] == 0
    assert summary["can_score"] is False
    out = tmp_path / "result"
    assert run_trial(REVIEWER, out)["model_invoked"] is False
    assert (out / "native_trial_manifest.csv").read_text().startswith("poster_id")
    readiness = json.loads((out / "native_trial_readiness.json").read_text())
    manifest_sha = hashlib.sha256((out / "native_trial_manifest.csv").read_bytes()).hexdigest()
    assert readiness["native_trial_manifest_sha256"] == manifest_sha
    assert not (out / "native_real_summary.json").exists()


def test_no_human_review_or_missing_ocr_stays_unscorable(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _poster(tmp_path, monkeypatch, product_bytes)
    result, rows = audit_trial(REVIEWER)
    assert result["status_counts"]["NEEDS_REVIEW"] == 1
    assert rows[0]["poster_id"] == pid
    _review(pid)
    result, rows = audit_trial(REVIEWER)
    assert result["status_counts"]["OCR_MISSING"] == 1
    assert result["reviewed_versions_with_definite_labels"] == 1
    assert result["ready_versions"] == 0
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="未完成"):
        run_trial(REVIEWER, output, score=True)
    assert (output / "native_trial_readiness.json").exists()
    assert "OCR_MISSING" in (output / "native_trial_manifest.csv").read_text()
    assert not (output / "native_real_summary.json").exists()


def test_one_command_scores_only_explicitly_and_counts_unique_sku(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _poster(tmp_path, monkeypatch, product_bytes)
    _review(pid)
    _run_ocr(pid, monkeypatch)
    # Same product, two independently reviewed price versions.
    rev = client.post(f"/api/posters/{pid}/revisions", json={"price": "189"})
    assert rev.status_code == 201, rev.text
    _review(pid, 2)
    def _ocr_v2(path):
        return [{**line, "text": line["text"].replace("¥199", "¥189")}
                for line in _ocr(path)]
    monkeypatch.setattr("app.native_acceptance.recognize", _ocr_v2)
    resp = client.post(
        f"/api/posters/{pid}/versions/2/exports/feed_4x5_native/acceptance",
        json={"ocr_mode": "paddle"},
    )
    assert resp.status_code == 200, resp.text
    summary, rows = audit_trial(REVIEWER)
    assert summary["total_versions"] == 2
    assert summary["ready_versions"] == 2
    assert summary["ready_distinct_product_sha256_count"] == 1
    assert summary["meets_suggested_minimum"] is False
    assert summary["can_score"] is True
    assert {row["status"] for row in rows} == {"READY"}

    destination = tmp_path / "native-trial"
    assert not destination.exists()
    only_audit = run_trial(REVIEWER, destination)
    assert only_audit["ready_versions"] == 2
    assert not (destination / "native_real_summary.json").exists()
    scored = run_trial(REVIEWER, destination, score=True)
    assert scored["poster_versions"] == 2
    assert scored["ocr_text_labels"] == 10
    assert scored["metrics"]["confusion"]["TN"] == 10
    assert scored["ready_distinct_product_sha256_count"] == 1
    assert hashlib.sha256(
        (destination / "native_trial_labels.csv").read_bytes()
    ).hexdigest() == scored["label_csv_sha256"]
    assert (destination / "native_real_summary.json").exists()
    assert (destination / "native_real_cases.csv").exists()
    assert storage.view(pid)["versions"] == [1, 2]


def test_a_review_from_other_operator_does_not_count_for_selected_reviewer(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _poster(tmp_path, monkeypatch, product_bytes)
    _review(pid, reviewer="someone-else")
    _run_ocr(pid, monkeypatch)
    summary, rows = audit_trial(REVIEWER)
    assert summary["reviewed_versions_with_definite_labels"] == 0
    assert rows[0]["status"] == "NEEDS_REVIEW"


def test_stale_ocr_is_blocked_even_when_human_review_still_valid(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _poster(tmp_path, monkeypatch, product_bytes)
    _review(pid)
    _run_ocr(pid, monkeypatch)
    path = storage.poster_dir(pid) / "versions" / "v1.feed_4x5_native.acceptance.paddle.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    report["native_image_sha256"] = "0" * 64
    path.write_text(json.dumps(report), encoding="utf-8")
    summary, rows = audit_trial(REVIEWER)
    assert summary["can_score"] is False
    assert rows[0]["status"] == "OCR_STALE"
    with pytest.raises(ValueError, match="未完成"):
        run_trial(REVIEWER, tmp_path / "out", score=True)


def test_failed_ocr_and_missing_price_check_get_distinct_actions(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _poster(tmp_path, monkeypatch, product_bytes)
    _review(pid)
    from app.ocr_provider import OCRUnavailable
    monkeypatch.setattr(
        "app.native_acceptance.recognize",
        lambda _: (_ for _ in ()).throw(OCRUnavailable("no engine")),
    )
    url = f"/api/posters/{pid}/versions/1/exports/feed_4x5_native/acceptance"
    assert client.post(url, json={"ocr_mode": "paddle"}).status_code == 200
    summary, rows = audit_trial(REVIEWER)
    assert rows[0]["status"] == "OCR_NOT_OK"
    assert not summary["can_score"]

    _run_ocr(pid, monkeypatch)
    path = storage.poster_dir(pid) / "versions" / "v1.feed_4x5_native.acceptance.paddle.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    report["checks"] = [x for x in report["checks"] if x["name"] != "ocr_price_position"]
    path.write_text(json.dumps(report), encoding="utf-8")
    _, rows = audit_trial(REVIEWER)
    assert rows[0]["status"] == "OCR_CHECKS_INVALID"


def test_all_unsure_does_not_create_denominator_or_score(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _poster(tmp_path, monkeypatch, product_bytes)
    all_fields = [
        "sku_integrity", "text_legibility", "composition",
        "title", "price", "cta", "subtitle", "original_price",
    ]
    _review(pid, unsure=all_fields)
    _run_ocr(pid, monkeypatch)
    summary, rows = audit_trial(REVIEWER)
    assert summary["ready_versions"] == 0
    assert rows[0]["status"] == "REVIEW_UNSURE"
    assert summary["can_score"] is False


def test_invalid_reviewer_rejected_without_data_access(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "posters"))
    with pytest.raises(ValueError, match="审核员"):
        audit_trial("../")


def test_new_audit_archives_prior_success_instead_of_reusing_stale_metrics(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _poster(tmp_path, monkeypatch, product_bytes)
    _review(pid)
    _run_ocr(pid, monkeypatch)
    output = tmp_path / "trial"
    scored = run_trial(REVIEWER, output, score=True)
    assert scored["ocr_text_labels"] == 5
    saved = (output / "native_real_summary.json").read_bytes()
    state = json.loads((output / "native_trial_readiness.json").read_text())
    assert state["latest_score_status"] == "SCORED"

    # Any future audit is a new run. Old metrics are history, not latest.
    fresh = run_trial(REVIEWER, output)
    assert fresh["latest_score_status"] == "AUDIT_ONLY"
    assert fresh["trial_run_id"] != state["trial_run_id"]
    assert not (output / "native_real_summary.json").exists()
    assert not (output / "native_real_cases.csv").exists()
    assert not (output / "native_trial_labels.csv").exists()
    assert (output / fresh["previous_score_archive"] / "native_real_summary.json").read_bytes() == saved


def test_new_blocked_score_does_not_leave_old_metrics_current(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _poster(tmp_path, monkeypatch, product_bytes)
    _review(pid)
    _run_ocr(pid, monkeypatch)
    output = tmp_path / "trial"
    run_trial(REVIEWER, output, score=True)
    assert (output / "native_real_summary.json").is_file()

    # Version v2 has an operator label but NO corresponding PaddleOCR result.
    r = client.post(f"/api/posters/{pid}/revisions", json={"price": "189"})
    assert r.status_code == 201
    _review(pid, 2)
    with pytest.raises(ValueError, match="未完成"):
        run_trial(REVIEWER, output, score=True)

    status = json.loads((output / "native_trial_readiness.json").read_text())
    assert status["latest_score_status"] == "BLOCKED"
    assert status["status_counts"]["OCR_MISSING"] == 1
    assert not (output / "native_real_summary.json").exists()
    assert (output / status["previous_score_archive"] / "native_real_summary.json").is_file()


def test_failed_scorer_does_not_publish_partial_files(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _poster(tmp_path, monkeypatch, product_bytes)
    _review(pid)
    _run_ocr(pid, monkeypatch)
    output = tmp_path / "trial"
    def corrupt_score(*args, **kwargs):
        from pathlib import Path
        stage = Path(args[1])
        (stage / "native_real_summary.json").write_text('{"fake": true}')
        raise OSError("simulated disk or report error")
    monkeypatch.setattr("evals.native_real_reports.evaluate_native_reports", corrupt_score)
    with pytest.raises(OSError, match="simulated"):
        run_trial(REVIEWER, output, score=True)
    state = json.loads((output / "native_trial_readiness.json").read_text())
    assert state["latest_score_status"] == "FAILED"
    assert not (output / "native_real_summary.json").exists()
    assert not (output / "native_real_cases.csv").exists()


def test_mocked_plain_list_ocr_is_explicitly_unrecorded(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _poster(tmp_path, monkeypatch, product_bytes)
    _review(pid)
    _run_ocr(pid, monkeypatch)
    report = client.get(
        f"/api/posters/{pid}/versions/1/exports/feed_4x5_native/acceptance"
        "?ocr_mode=paddle"
    ).json()
    assert report["ocr_runtime"] is None
    assert report["ocr_runtime_evidence"] == "UNRECORDED"
    state, _ = audit_trial(REVIEWER)
    assert state["ready_versions"] == 1
    assert state["ocr_adapter_runtime_unrecorded_versions"] == 1
    assert state["ocr_runtime_attested"] is False
    out = tmp_path / "score"
    scored = run_trial(REVIEWER, out, score=True)
    assert scored["ocr_runtime_unrecorded_versions"] == 1
    assert scored["ocr_runtime_attested"] is False


def test_focus_trial_can_score_own_evidence_without_classic_reuse(
    tmp_path, monkeypatch, product_bytes,
):
    from app.native_acceptance import PRODUCT_FOCUS_VARIANT

    pid = _poster(tmp_path, monkeypatch, product_bytes)
    focus = PRODUCT_FOCUS_VARIANT
    review_url = f"/api/posters/{pid}/versions/1/exports/{focus}/human-review"
    info = client.get(review_url).json()
    data = {
        "reviewer": REVIEWER,
        "image_sha256": info["image_sha256"],
        "source_image_sha256": info["source_image_sha256"],
        "spec_sha256": info["spec_sha256"],
        "asset_sha256": info["asset_sha256"],
        "fields": {
            field: {"judgment": "correct", "reason": ""}
            for field in info["fields_to_review"]
        },
    }
    assert client.post(review_url, json=data).status_code == 201
    monkeypatch.setattr("app.native_acceptance.recognize", _ocr)
    report = client.post(
        f"/api/posters/{pid}/versions/1/exports/{focus}/acceptance",
        json={"ocr_mode": "paddle"},
    )
    assert report.status_code == 200, report.text

    default_state, _ = audit_trial(REVIEWER)
    assert default_state["can_score"] is False
    focus_state, rows = audit_trial(REVIEWER, variant=focus)
    assert focus_state["variant_id"] == focus
    assert rows[0]["status"] == "READY"
    assert focus_state["can_score"] is True
    assert focus_state["ocr_runtime_attested"] is False

    destination = tmp_path / "focus-trial"
    result = run_trial(
        REVIEWER, destination, score=True, variant=focus
    )
    assert result["variant_id"] == focus
    assert result["poster_versions"] == 1
    assert result["ocr_engine_execution"] == "saved_report_only"
    readiness = json.loads(
        (destination / "native_trial_readiness.json").read_text()
    )
    assert readiness["variant_id"] == focus
    assert readiness["latest_score_status"] == "SCORED"
    assert (destination / "native_real_cases.csv").is_file()
    assert (destination / "native_trial_labels.csv").is_file()
    assert not (
        storage.poster_dir(pid) / "versions"
        / "v1.feed_4x5_native.acceptance.paddle.json"
    ).exists()


def test_focus_audit_does_not_score_if_evidence_missing(
    tmp_path, monkeypatch, product_bytes,
):
    from app.native_acceptance import PRODUCT_FOCUS_VARIANT

    _poster(tmp_path, monkeypatch, product_bytes)
    destination = tmp_path / "focus-missing"
    with pytest.raises(ValueError, match="未完成"):
        run_trial(
            REVIEWER, destination, score=True,
            variant=PRODUCT_FOCUS_VARIANT,
        )
    readiness = json.loads(
        (destination / "native_trial_readiness.json").read_text()
    )
    assert readiness["variant_id"] == PRODUCT_FOCUS_VARIANT
    assert readiness["can_score"] is False
    assert readiness["latest_score_status"] == "BLOCKED"
    assert not (destination / "native_real_summary.json").exists()


def test_cli_variant_selection_preserves_classic_default_and_focus_metadata(
    tmp_path, monkeypatch, capsys,
):
    import sys
    from scripts import native_trial as cli

    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "empty-posters"))
    focus_dir = tmp_path / "focus-cli"
    monkeypatch.setattr(sys, "argv", [
        "native_trial", "--reviewer", REVIEWER,
        "--variant", "feed_4x5_native_product_focus",
        "--output-dir", str(focus_dir),
    ])
    assert cli.main() == 0
    focus = json.loads(
        (focus_dir / "native_trial_readiness.json").read_text()
    )
    assert focus["variant_id"] == "feed_4x5_native_product_focus"
    assert focus["latest_score_status"] == "AUDIT_ONLY"
    assert focus["model_invoked"] is False

    classic_dir = tmp_path / "classic-cli"
    monkeypatch.setattr(sys, "argv", [
        "native_trial", "--reviewer", REVIEWER,
        "--output-dir", str(classic_dir),
    ])
    assert cli.main() == 0
    classic = json.loads(
        (classic_dir / "native_trial_readiness.json").read_text()
    )
    assert classic["variant_id"] == "feed_4x5_native"
    assert not (focus_dir / "native_real_summary.json").exists()
    assert not (classic_dir / "native_real_summary.json").exists()



def _eval_files_snapshot(output_dir):
    """Include history and manifests: a rejected reuse must mutate nothing."""
    return {
        str(path.relative_to(output_dir)): path.read_bytes()
        for path in output_dir.rglob("*") if path.is_file()
    }


def test_reusing_classic_score_directory_for_focus_preserves_all_evidence(
    tmp_path, monkeypatch, product_bytes,
):
    from app.native_acceptance import PRODUCT_FOCUS_VARIANT

    pid = _poster(tmp_path, monkeypatch, product_bytes)
    _review(pid)
    _run_ocr(pid, monkeypatch)
    output = tmp_path / "trial"
    run_trial(REVIEWER, output, score=True)
    before = _eval_files_snapshot(output)
    assert (output / "native_real_summary.json").is_file()

    with pytest.raises(ValueError, match="版式.*独立目录"):
        run_trial(REVIEWER, output, variant=PRODUCT_FOCUS_VARIANT)
    assert _eval_files_snapshot(output) == before

    with pytest.raises(ValueError, match="版式.*独立目录"):
        run_trial(REVIEWER, output, score=True, variant=PRODUCT_FOCUS_VARIANT)
    assert _eval_files_snapshot(output) == before


def test_reusing_score_directory_for_another_reviewer_does_not_archive(
    tmp_path, monkeypatch, product_bytes,
):
    pid = _poster(tmp_path, monkeypatch, product_bytes)
    _review(pid)
    _run_ocr(pid, monkeypatch)
    output = tmp_path / "trial"
    run_trial(REVIEWER, output, score=True)
    before = _eval_files_snapshot(output)

    with pytest.raises(ValueError, match="审核员.*独立目录"):
        run_trial("another-reviewer", output)
    assert _eval_files_snapshot(output) == before


def test_unclear_eval_directory_identity_fails_closed_before_any_write(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "empty-posters"))
    output = tmp_path / "trial"
    output.mkdir()
    readiness = output / "native_trial_readiness.json"
    readiness.write_text("{broken", encoding="utf-8")
    before = _eval_files_snapshot(output)
    with pytest.raises(ValueError, match="元数据.*独立目录"):
        run_trial(REVIEWER, output)
    assert _eval_files_snapshot(output) == before

    readiness.unlink()
    (output / "native_real_summary.json").write_text('{"old":true}')
    before = _eval_files_snapshot(output)
    with pytest.raises(ValueError, match="来源不明.*独立目录"):
        run_trial(REVIEWER, output)
    assert _eval_files_snapshot(output) == before


def test_distinct_variant_directories_remain_independent(
    tmp_path, monkeypatch,
):
    from app.native_acceptance import PRODUCT_FOCUS_VARIANT, VARIANT

    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "empty-posters"))
    classic = tmp_path / "classic"
    focus = tmp_path / "focus"
    classic_result = run_trial(REVIEWER, classic)
    focus_result = run_trial(REVIEWER, focus, variant=PRODUCT_FOCUS_VARIANT)
    assert classic_result["variant_id"] == VARIANT
    assert focus_result["variant_id"] == PRODUCT_FOCUS_VARIANT
    assert json.loads((classic / "native_trial_readiness.json").read_text())["variant_id"] == VARIANT
    assert json.loads((focus / "native_trial_readiness.json").read_text())["variant_id"] == PRODUCT_FOCUS_VARIANT
    assert not (classic / "native_real_summary.json").exists()
    assert not (focus / "native_real_summary.json").exists()


def _synthetic_batch_scope(journal_digest: str = "a", manifest_digest: str = "b"):
    """A small validated-shape cohort identity; no real SKUs or OCR involved."""
    return {
        "scope_mode": "batch_latest_per_sku",
        "sku_by_id": {"a" * 32: "sku-01"},
        "source_batch_journal_sha256": journal_digest * 64,
        "source_manifest_sha256": manifest_digest * 64,
        "batch_sku_count": 1,
        "batch_unique_raw_product_sha256_count": 1,
    }


def test_same_variant_reviewer_new_batch_cannot_reuse_trial_directory(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "missing-posters"))
    output = tmp_path / "cohort-a"
    first = _synthetic_batch_scope("a", "b")
    second = _synthetic_batch_scope("c", "d")
    audit = run_trial(REVIEWER, output, batch_scope=first)
    assert audit["source_batch_journal_sha256"] == "a" * 64
    before = _eval_files_snapshot(output)

    with pytest.raises(ValueError, match="批次.*独立目录"):
        run_trial(REVIEWER, output, batch_scope=second)
    assert _eval_files_snapshot(output) == before

    with pytest.raises(ValueError, match="批次.*独立目录"):
        run_trial(REVIEWER, output, batch_scope=second, score=True)
    assert _eval_files_snapshot(output) == before

    # Same immutable cohort remains resumable/re-auditable by the same reviewer.
    repeat = run_trial(REVIEWER, output, batch_scope=first)
    assert repeat["source_batch_journal_sha256"] == "a" * 64
    assert repeat["trial_run_id"] != audit["trial_run_id"]


def test_unscoped_and_scoped_audits_cannot_share_output_directory(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "missing-posters"))
    scope = _synthetic_batch_scope()
    all_dir = tmp_path / "all-posters"
    batch_dir = tmp_path / "batch"
    run_trial(REVIEWER, all_dir)
    run_trial(REVIEWER, batch_dir, batch_scope=scope)
    before_all = _eval_files_snapshot(all_dir)
    before_batch = _eval_files_snapshot(batch_dir)

    with pytest.raises(ValueError, match="批次范围.*独立目录"):
        run_trial(REVIEWER, all_dir, batch_scope=scope)
    assert _eval_files_snapshot(all_dir) == before_all

    with pytest.raises(ValueError, match="批次范围.*独立目录"):
        run_trial(REVIEWER, batch_dir)
    assert _eval_files_snapshot(batch_dir) == before_batch


def test_invalid_prior_batch_provenance_fails_closed_without_mutating_files(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path / "missing-posters"))
    output = tmp_path / "batch"
    scope = _synthetic_batch_scope()
    run_trial(REVIEWER, output, batch_scope=scope)
    readiness_file = output / "native_trial_readiness.json"
    baseline = json.loads(readiness_file.read_text(encoding="utf-8"))

    for change in (
        {"source_batch_journal_sha256": "NOT_A_DIGEST"},
        {"source_batch_journal_sha256": None},
        {"source_manifest_sha256": "x" * 64},
        {"scope_mode": "unrecognized_scope"},
    ):
        corrupted = {**baseline, **change}
        readiness_file.write_text(
            json.dumps(corrupted, ensure_ascii=False), encoding="utf-8"
        )
        before = _eval_files_snapshot(output)
        with pytest.raises(ValueError, match="批次.*独立目录"):
            run_trial(REVIEWER, output, batch_scope=scope)
        assert _eval_files_snapshot(output) == before
