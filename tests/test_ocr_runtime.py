"""M3d: report never claims model success from package presence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image

from evals.ocr_runtime import probe, save_report
from scripts.ocr_probe import main
from app.ocr_provider import OCRUnavailable, OCRExecutionError


def _image(tmp_path):
    path = tmp_path / "demo.png"
    Image.new("RGB", (200, 200), "white").save(path)
    return path


def test_diagnosis_without_image_does_not_import_or_execute_model(monkeypatch):
    def not_called(_):
        raise AssertionError("the inference function must not run")
    output = probe(infer=False, runner=not_called)
    assert output["status"] == "NOT_RUN"
    assert output["model_invocation_completed"] is False
    assert output["elapsed_ms"] is None
    assert output["line_count"] is None


def test_real_png_evidence_but_not_run_without_infer(tmp_path):
    path = _image(tmp_path)
    report = probe(path)
    assert report["status"] == "NOT_RUN"
    assert report["image"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert report["image"]["pixel_size"] == [200, 200]


def test_infer_success_is_explicit_and_raw_text_opt_in(tmp_path):
    path = _image(tmp_path)
    calls = []
    def fake_runner(image):
        calls.append(image)
        return [{"text": "¥199", "score": 0.97, "bbox": [1, 2, 3, 4]}]
    minimal = probe(path, infer=True, runner=fake_runner)
    assert minimal["model_invocation_completed"] is True
    assert minimal["status"] == "OK"
    assert minimal["line_count"] == 1
    assert minimal["mean_recognition_score"] == 0.97
    assert "ocr_lines" not in minimal
    detailed = probe(path, infer=True, include_text=True, runner=fake_runner)
    assert detailed["ocr_lines"][0]["text"] == "¥199"
    assert len(calls) == 2
    dest = tmp_path / "out" / "evidence.json"
    save_report(detailed, dest)
    assert json.loads(dest.read_text())["image"]["sha256"] == minimal["image"]["sha256"]


def test_unavailable_model_not_counted_as_success(tmp_path):
    def missing(_):
        raise OCRUnavailable("dependency not available")
    result = probe(_image(tmp_path), infer=True, runner=missing)
    assert result["status"] == "UNAVAILABLE"
    assert result["model_invocation_completed"] is False
    assert result["line_count"] is None


def test_runtime_error_not_counted_as_success(tmp_path):
    def failed(_):
        raise OCRExecutionError("model failed")
    result = probe(_image(tmp_path), infer=True, runner=failed)
    assert result["status"] == "ERROR"
    assert result["model_invocation_completed"] is False


def test_invalid_image_or_missing_file_is_rejected(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        probe(tmp_path / "missing.png")
    broken = tmp_path / "not-an-image.png"
    broken.write_bytes(b"not a PNG")
    with pytest.raises(Exception):
        probe(broken)


def test_cli_diagnosis_exit_zero_and_writes_non_inference_record(tmp_path, monkeypatch):
    import sys
    target = tmp_path / "result.json"
    monkeypatch.setattr(sys, "argv", ["ocr_probe", "--output", str(target)])
    assert main() == 0
    data = json.loads(target.read_text())
    assert data["status"] == "NOT_RUN"
    assert data["model_invocation_completed"] is False
