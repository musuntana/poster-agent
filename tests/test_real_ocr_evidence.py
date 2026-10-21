"""Tests of evidence validation, never proof of genuine PaddleOCR inference.

Mocked rows and environments intentionally exercise the failure-closed contract.
Only the separate real-ocr GitHub Actions run can verify a real model process.
"""
from __future__ import annotations

import json

import pytest
from PIL import Image

from evals import ocr_runtime
from evals.image_fixtures import generate_fixture_set
from evals.image_runner import evaluate_image_manifest
from scripts.verify_real_ocr_evidence import EvidenceInvalid, verify_probe, verify_batch


def _fake_env():
    return {
        "platform": "Linux",
        "machine": "x86_64",
        "python": "3.12.5",
        "paddlepaddle_distribution": "3.2.0",
        "paddleocr_distribution": "3.3.0",
        "paddle_module_detected": True,
        "paddleocr_module_detected": True,
    }


def _make_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr_runtime, "environment", _fake_env)
    image_file = tmp_path / "sample.png"
    Image.new("RGB", (100, 100), "white").save(image_file)
    result = ocr_runtime.probe(
        image_file, infer=True, include_text=True,
        runner=lambda _: [{"text": "¥199", "score": 0.98, "bbox": [2, 3, 30, 25]}],
    )
    path = tmp_path / "probe.json"
    ocr_runtime.save_report(result, path)
    return path, image_file


def test_valid_probe_records_image_hash_and_recognition_boxes(tmp_path, monkeypatch):
    report, image = _make_probe(tmp_path, monkeypatch)
    proof = verify_probe(report, image, "Linux")
    assert proof["ocr_lines"] == 1
    assert proof["valid_bboxes"] == 1
    assert proof["paddleocr"] == "3.3.0"
    assert len(proof["probe_sha256"]) == 64


def test_not_run_and_bad_platform_rejected(tmp_path, monkeypatch):
    report, image = _make_probe(tmp_path, monkeypatch)
    with pytest.raises(EvidenceInvalid, match="平台"):
        verify_probe(report, image, "Darwin")
    data = json.loads(report.read_text())
    data["status"] = "NOT_RUN"
    report.write_text(json.dumps(data))
    with pytest.raises(EvidenceInvalid, match="成功的真实推理"):
        verify_probe(report, image, "Linux")


def test_cannot_accept_missing_original_ocr_lines_or_no_boxes(tmp_path, monkeypatch):
    report, image = _make_probe(tmp_path, monkeypatch)
    data = json.loads(report.read_text())
    data.pop("ocr_lines")
    report.write_text(json.dumps(data))
    with pytest.raises(EvidenceInvalid, match="原始识别行"):
        verify_probe(report, image, "Linux")
    data["ocr_lines"] = [{"text": "¥199", "score": 0.99, "bbox": None}]
    report.write_text(json.dumps(data))
    with pytest.raises(EvidenceInvalid, match="坐标框"):
        verify_probe(report, image, "Linux")


def test_tampered_input_and_missing_installed_versions_rejected(tmp_path, monkeypatch):
    report, image = _make_probe(tmp_path, monkeypatch)
    image.write_bytes(image.read_bytes() + b"tampered")
    with pytest.raises(EvidenceInvalid, match="SHA-256"):
        verify_probe(report, image, "Linux")
    _, image = _make_probe(tmp_path, monkeypatch)
    data = json.loads(report.read_text())
    data["environment"]["paddleocr_distribution"] = None
    report.write_text(json.dumps(data))
    with pytest.raises(EvidenceInvalid, match="版本"):
        verify_probe(report, image, "Linux")


def test_batch_manifest_observations_and_hashes_are_checked(tmp_path, monkeypatch):
    manifest = generate_fixture_set(tmp_path / "images")
    monkeypatch.setattr("evals.image_runner.recognize", lambda path: [
        {"text": "¥199", "score": 0.90, "bbox": [10, 10, 90, 30]}
    ])
    base = tmp_path / "bench"
    evaluate_image_manifest(manifest, base, "paddle")
    # The replay runner expects manifest under batch_dir/images, so copy the
    # programmatic fixtures into this standard layout for the test.
    import shutil
    shutil.copytree(manifest.parent, base / "images")
    outcome = verify_batch(base)
    assert outcome["fixture_count"] == 20
    assert outcome["observation_records"] == 20

    file = base / "images" / "F01.png"
    file.write_bytes(file.read_bytes() + b"changed")
    with pytest.raises(EvidenceInvalid, match="SHA-256"):
        verify_batch(base)


def test_batch_rejects_fake_not_run_summary(tmp_path, monkeypatch):
    manifest = generate_fixture_set(tmp_path / "images")
    base = tmp_path / "bench"
    evaluate_image_manifest(manifest, base, "none")
    import shutil
    shutil.copytree(manifest.parent, base / "images")
    with pytest.raises(EvidenceInvalid, match="PaddleOCR"):
        verify_batch(base)
