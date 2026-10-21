"""Verify PaddleOCR 3.x parsing without loading its large model weights."""
from pathlib import Path

from app import ocr_provider


def test_paddle_3x_result_conversion(monkeypatch, tmp_path):
    class FakeOCR:
        def predict(self, path):
            assert Path(path).exists()
            return [{"res": {
                "rec_texts": ["¥199", "立即抢购"],
                "rec_scores": [0.99, 0.86],
                "rec_boxes": [[60, 660, 280, 770], [74, 866, 410, 935]],
            }}]
    monkeypatch.setattr(ocr_provider, "_engine", lambda: FakeOCR())
    path = tmp_path / "placeholder.png"
    path.write_bytes(b"unused fake bytes")
    out = ocr_provider.recognize(path)
    assert out[0] == {"text": "¥199", "score": 0.99, "bbox": [60, 660, 280, 770]}
    assert out[1]["text"] == "立即抢购"
