"""M3b evaluator regression and metric consistency; fixtures are NOT real OCR data."""
from pathlib import Path
import json
import pytest
from pydantic import ValidationError

from evals.benchmark import BenchmarkDataset, evaluate_file, metrics, run_dataset

SOURCE = Path(__file__).resolve().parents[1] / "evals/datasets/m3b_synthetic_ocr_v1.json"


def test_dataset_has_20_unique_labeled_synthetic_cases():
    dataset = BenchmarkDataset.model_validate_json(SOURCE.read_bytes())
    assert dataset.kind == "synthetic_contract_tests"
    assert len(dataset.cases) == 20
    assert len({case.id for case in dataset.cases}) == 20
    assert set(dataset.target_fields) == {"title", "price", "cta"}
    assert {c.category for c in dataset.cases} >= {
        "clean", "price_error", "false_alarm", "missed_defect",
        "abstention", "confidence_threshold", "fragmented_text",
    }


def test_benchmark_reports_fp_fn_and_abstentions_from_actual_evaluator():
    dataset = BenchmarkDataset.model_validate_json(SOURCE.read_bytes())
    report, rows = run_dataset(dataset)
    assert len(rows) == 80  # 20 cases * (3 field judgments + 1 case judgment)
    assert report["case_metrics"]["total"] == 20
    assert report["field_metrics"]["total"] == 60
    outcomes = {r["case_id"]: r["outcome"] for r in rows if r["field"] == "__case__"}
    assert outcomes["S04"] == "TP"  # wrong price
    assert outcomes["S05"] == "TP"  # price 1990 must not match 199
    assert outcomes["S06"] == "TP"  # price 199.90 must not match 199
    assert outcomes["S12"] == "FP"  # OCR misses correct visible price
    assert outcomes["S16"] == "FN"  # OCR hallucinated correct number
    assert outcomes["S18"] == "ABSTAIN_GOOD"
    assert outcomes["S19"] == "ABSTAIN_DEFECT"
    assert outcomes["S20"] == "FP"  # split title
    assert report["case_metrics"]["confusion"] == {
        "TP": 8, "FP": 5, "FN": 2, "TN": 3,
        "ABSTAIN_GOOD": 1, "ABSTAIN_DEFECT": 1,
    }
    assert report["case_metrics"]["coverage"] == 0.9
    assert report["case_metrics"]["recall_including_abstentions"] < report["case_metrics"]["recall_on_assessed"]
    assert report["warning"].startswith("SYNTHETIC")


def test_reports_are_deterministic_and_machine_readable(tmp_path):
    first = evaluate_file(SOURCE, tmp_path)
    second = evaluate_file(SOURCE, tmp_path)
    assert first == second
    summary = json.loads((tmp_path / "benchmark_summary.json").read_text(encoding="utf-8"))
    csv_text = (tmp_path / "benchmark_cases.csv").read_text(encoding="utf-8")
    assert summary["case_count"] == 20
    assert "S06" in csv_text
    assert summary["input_sha256"] == first["input_sha256"]


def test_metric_denominator_not_divide_by_zero():
    empty = metrics([])
    assert empty["total"] == 0
    assert empty["coverage"] is None
    assert empty["precision_on_assessed"] is None
    assert empty["f1_on_assessed"] is None
    assert metrics(["ABSTAIN_DEFECT"])["recall_including_abstentions"] == 0


def test_real_image_claims_are_rejected_by_synthetic_loader():
    d = json.loads(SOURCE.read_text(encoding="utf-8"))
    d["kind"] = "human_annotated_real_images"
    with pytest.raises(ValidationError, match="Real-image benchmark"):
        BenchmarkDataset.model_validate(d)


def test_duplicate_case_ids_rejected():
    d = json.loads(SOURCE.read_text(encoding="utf-8"))
    d["cases"][1]["id"] = d["cases"][0]["id"]
    with pytest.raises(ValidationError, match="case ids duplicated"):
        BenchmarkDataset.model_validate(d)


def test_bad_ground_truth_field_rejected():
    d = json.loads(SOURCE.read_text(encoding="utf-8"))
    d["cases"][0]["truth_defects"] = ["layout"]
    with pytest.raises(ValidationError):
        BenchmarkDataset.model_validate(d)
