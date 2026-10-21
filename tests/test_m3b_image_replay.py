"""Actual rendered PNG fixture tests. Real OCR is optional and NOT run by CI."""
from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError
from app.ocr_provider import OCRUnavailable

from evals.image_fixtures import ImageManifest, generate_fixture_set
from evals.image_runner import evaluate_image_manifest


def test_20_actual_png_files_and_label_integrity(tmp_path):
    manifest_path = generate_fixture_set(tmp_path)
    manifest = ImageManifest.model_validate_json(manifest_path.read_bytes())
    assert len(manifest.fixtures) == 20
    assert len({case.case_id for case in manifest.fixtures}) == 20
    assert sum(bool(case.truth_defects) for case in manifest.fixtures) == 16
    for case in manifest.fixtures:
        image = (tmp_path / case.image_file).read_bytes()
        assert image.startswith(b"\x89PNG\r\n\x1a\n")
        assert hashlib.sha256(image).hexdigest() == case.image_sha256
        assert case.truth_defects == [
            name for name in manifest.target_fields
            if getattr(case.rendered_spec, name) != getattr(manifest.expected_spec, name)
        ]


def test_no_ocr_reports_all_abstentions_not_automatic_pass(tmp_path):
    manifest = generate_fixture_set(tmp_path / "images")
    report = evaluate_image_manifest(manifest, tmp_path, "none")
    assert report["model_executed"] is False
    assert report["fixture_count"] == 20
    assert report["field_metrics"]["total"] == 60
    assert report["field_metrics"]["abstained"] == 60
    assert report["field_metrics"]["coverage"] == 0
    assert report["field_metrics"]["precision_on_assessed"] is None
    assert report["case_metrics"]["confusion"]["ABSTAIN_GOOD"] == 4
    assert report["case_metrics"]["confusion"]["ABSTAIN_DEFECT"] == 16
    assert (tmp_path / "image_benchmark_cases.csv").is_file()
    assert (tmp_path / "image_benchmark_summary.json").is_file()


def test_paddle_mode_uses_image_file_and_evaluator_with_mocked_ocr(tmp_path, monkeypatch):
    manifest_file = generate_fixture_set(tmp_path / "images")
    manifest = ImageManifest.model_validate_json(manifest_file.read_bytes())
    called = []
    def fake_recognize(path):
        assert path.is_file()
        assert path.suffix == ".png"
        called.append(path.name)
        fixture = next(f for f in manifest.fixtures if f.image_file == path.name)
        spec = fixture.rendered_spec
        return [{"text": s, "score": 0.99} for s in
                (spec.title, "¥" + str(spec.price).rstrip("0").rstrip(".") if "." in str(spec.price) else "¥"+str(spec.price), spec.cta)]
    monkeypatch.setattr("evals.image_runner.recognize", fake_recognize)
    report = evaluate_image_manifest(manifest_file, tmp_path / "paddle_run", "paddle")
    assert len(called) == 20
    assert report["model_executed"] is True  # mocked provider invocation only
    assert report["case_metrics"]["confusion"]["TP"] >= 10
    assert report["case_metrics"]["confusion"]["TN"] == 4
    assert report["case_metrics"]["abstained"] == 0


def test_paddle_absent_raises_instead_of_fabricating_accuracy(tmp_path, monkeypatch):
    manifest = generate_fixture_set(tmp_path / "images")
    def absent(path):
        raise OCRUnavailable("not installed")
    monkeypatch.setattr("evals.image_runner.recognize", absent)
    with pytest.raises(RuntimeError, match="未生成任何真实 OCR 指标"):
        evaluate_image_manifest(manifest, tmp_path / "missing_ocr", "paddle")
    assert not (tmp_path / "missing_ocr/image_benchmark_summary.json").exists()


def test_modified_png_blocks_scoring(tmp_path):
    manifest = generate_fixture_set(tmp_path / "images")
    bad = tmp_path / "images" / "F07.png"
    bad.write_bytes(bad.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="SHA-256"):
        evaluate_image_manifest(manifest, tmp_path / "score", "none")


def test_misleading_labels_are_rejected(tmp_path):
    manifest = generate_fixture_set(tmp_path / "images")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["fixtures"][0]["truth_defects"] = ["price"]
    with pytest.raises(ValidationError, match="programmatic ground truth"):
        ImageManifest.model_validate(data)
