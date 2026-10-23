from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from app import storage
from app.schemas import PosterSpec


def _spec(title: str = "测试商品") -> PosterSpec:
    return PosterSpec(title=title, price="199", background_mode="local")


def _png_bytes(color: str = "#183050") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (128, 128), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _image(color: str = "#183050") -> Image.Image:
    return Image.new("RGB", (128, 128), color)


def test_atomic_write_keeps_previous_file_when_publish_fails(tmp_path, monkeypatch):
    target = tmp_path / "meta.json"
    target.write_bytes(b'{"version": 1}')
    original_replace = storage.os.replace

    def fail_publish(source, destination):
        if Path(destination) == target:
            raise OSError("simulated disk publish failure")
        return original_replace(source, destination)

    monkeypatch.setattr(storage.os, "replace", fail_publish)

    with pytest.raises(OSError, match="simulated disk publish failure"):
        storage._atomic_write(target, lambda stream: stream.write(b'{"version": 2}'))

    assert target.read_bytes() == b'{"version": 1}'
    assert list(tmp_path.glob(".meta.json.*.tmp")) == []


def test_create_does_not_publish_partial_poster_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path))
    original_save_png = storage.save_png

    def fail_on_background(image, target):
        if target.name == "background.png":
            raise OSError("simulated asset write failure")
        original_save_png(image, target)

    monkeypatch.setattr(storage, "save_png", fail_on_background)

    with pytest.raises(OSError, match="simulated asset write failure"):
        storage.create(_spec(), _image(), None, _image("#456789"), _png_bytes())

    # A failed create leaves neither a visible final poster nor an abandoned
    # staging directory in the ordinary exception path.
    assert list(tmp_path.iterdir()) == []


def test_stale_staging_directory_is_cleaned_before_next_create(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path))
    stale = tmp_path / (".staging-" + "a" * 32)
    stale.mkdir()
    (stale / "partial.tmp").write_bytes(b"interrupted create")

    result = storage.create(_spec(), _image(), None, _image("#456789"), _png_bytes())

    assert result["version"] == 1
    assert (tmp_path / result["poster_id"] / "meta.json").is_file()
    assert not stale.exists()
    assert not list(tmp_path.glob(".staging-*"))


def test_failed_version_write_keeps_previous_version_readable(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTER_DATA_DIR", str(tmp_path))
    original = storage._atomic_json
    poster = storage.create(_spec(), _image(), None, _image(), _png_bytes())
    poster_id = poster["poster_id"]

    def fail_version_spec(path, value):
        if path.name == "v2.json":
            raise OSError("simulated version metadata failure")
        return original(path, value)

    with monkeypatch.context() as patch:
        patch.setattr(storage, "_atomic_json", fail_version_spec)
        with pytest.raises(OSError, match="simulated version metadata failure"):
            storage.new_version(poster_id, _spec("新标题"), _png_bytes("#456789"))

    # The latest-version pointer is committed only after both version files.
    # A failed attempt can leave an unpublished v2 PNG, but v1 stays readable.
    assert storage.view(poster_id)["version"] == 1
    assert storage.version_path(poster_id, 1).read_bytes() == _png_bytes()

    # Retrying safely replaces the orphaned v2 PNG and publishes a complete v2.
    updated = storage.new_version(poster_id, _spec("新标题"), _png_bytes("#456789"))
    assert updated["version"] == 2
    assert updated["spec"]["title"] == "新标题"
    assert storage.version_path(poster_id, 2).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
