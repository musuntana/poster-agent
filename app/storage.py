"""Crash-resistant local poster storage for the single-process Demo.

Files are atomically replaced so readers never observe partially written PNG/JSON
files. New posters are assembled in a hidden staging directory and published with
a same-filesystem directory rename only after every required file is complete.
This is not a multi-process or production object-store implementation.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
from pathlib import Path
from typing import Any, BinaryIO, Callable
from uuid import uuid4

from PIL import Image

from .schemas import PosterSpec

LOCK = threading.RLock()
_STAGING_NAME = re.compile(r"^\.staging-[0-9a-f]{32}$")


def root() -> Path:
    return Path(os.getenv("POSTER_DATA_DIR", "data/posters")).resolve()


def poster_dir(poster_id: str) -> Path:
    if len(poster_id) != 32 or any(c not in "0123456789abcdef" for c in poster_id):
        raise FileNotFoundError("海报不存在")
    path = root() / poster_id
    if not path.is_dir():
        raise FileNotFoundError("海报不存在")
    return path


def _cleanup_stale_temp_files(directory: Path, target_name: str) -> None:
    """Remove abandoned temporary files for one target left by a hard process exit."""
    for candidate in directory.glob(f".{target_name}.*.tmp"):
        if candidate.is_file():
            candidate.unlink(missing_ok=True)


def _atomic_write(path: Path, writer: Callable[[BinaryIO], Any]) -> None:
    """Write and fsync a sibling temporary file, then atomically publish it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _cleanup_stale_temp_files(path.parent, path.name)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temp.open("xb") as stream:
            writer(stream)
            stream.flush()
            os.fsync(stream.fileno())
        # Temp and destination share a parent/filesystem. Existing readers see
        # either the old complete file or the new complete file, never a prefix.
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def save_png(image: Image.Image, target: Path) -> None:
    _atomic_write(target, lambda stream: image.save(stream, format="PNG"))


def _atomic_json(path: Path, value: dict) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    _atomic_write(path, lambda stream: stream.write(payload))


def _cleanup_stale_staging_dirs(storage_root: Path) -> None:
    """Remove only this module's abandoned staging directories.

    Callers hold LOCK, and the supported deployment has one application process.
    Final poster directories are never matched by this name/pattern.
    """
    for candidate in storage_root.iterdir():
        if candidate.is_dir() and _STAGING_NAME.fullmatch(candidate.name):
            shutil.rmtree(candidate)


def _save_version(path: Path, version: int, spec: PosterSpec, render_bytes: bytes) -> None:
    versions = path / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    _atomic_write(versions / f"v{version}.png", lambda stream: stream.write(render_bytes))
    _atomic_json(versions / f"v{version}.json", spec.model_dump(mode="json"))


def create(
    spec: PosterSpec,
    product: Image.Image,
    logo: Image.Image | None,
    background: Image.Image,
    render_bytes: bytes,
) -> dict:
    with LOCK:
        storage_root = root()
        storage_root.mkdir(parents=True, exist_ok=True)
        _cleanup_stale_staging_dirs(storage_root)

        poster_id = uuid4().hex
        final_path = storage_root / poster_id
        staging_path = storage_root / f".staging-{poster_id}"
        staging_path.mkdir(parents=False, exist_ok=False)
        try:
            assets = staging_path / "assets"
            assets.mkdir(parents=True, exist_ok=False)
            save_png(product, assets / "product.png")
            if logo is not None:
                save_png(logo, assets / "logo.png")
            save_png(background, assets / "background.png")
            _save_version(staging_path, 1, spec, render_bytes)
            _atomic_json(
                staging_path / "meta.json",
                {
                    "poster_id": poster_id,
                    "latest_version": 1,
                    "background_mode": spec.background_mode,
                },
            )
            # Publish only after assets, version PNG/spec and metadata are complete.
            # Staging and final directories share the same parent/filesystem.
            os.replace(staging_path, final_path)
        except BaseException:
            shutil.rmtree(staging_path, ignore_errors=True)
            raise

        return view(poster_id)


def view(poster_id: str) -> dict:
    path = poster_dir(poster_id)
    meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
    latest = meta["latest_version"]
    spec = json.loads((path / "versions" / f"v{latest}.json").read_text(encoding="utf-8"))
    return {
        "poster_id": poster_id,
        "version": latest,
        "spec": spec,
        "image_url": f"/api/posters/{poster_id}/versions/{latest}/image",
        "versions": list(range(1, latest + 1)),
        "background_mode": meta["background_mode"],
    }


def read_assets(poster_id: str) -> tuple[Image.Image, Image.Image | None, Image.Image]:
    assets = poster_dir(poster_id) / "assets"

    def loaded(path: Path) -> Image.Image:
        with Image.open(path) as image:
            image.load()
            return image.convert("RGBA")

    return (
        loaded(assets / "product.png"),
        loaded(assets / "logo.png") if (assets / "logo.png").exists() else None,
        loaded(assets / "background.png"),
    )


def new_version(poster_id: str, spec: PosterSpec, render_bytes: bytes) -> dict:
    with LOCK:
        path = poster_dir(poster_id)
        meta_path = path / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        version = int(meta["latest_version"]) + 1
        # The metadata pointer is committed last. If a write fails, view() keeps
        # returning the previous complete version; a retry safely replaces vN.
        _save_version(path, version, spec, render_bytes)
        meta["latest_version"] = version
        _atomic_json(meta_path, meta)
        return view(poster_id)


def version_path(poster_id: str, version: int) -> Path:
    if version < 1:
        raise FileNotFoundError("版本不存在")
    target = poster_dir(poster_id) / "versions" / f"v{version}.png"
    if not target.is_file():
        raise FileNotFoundError("版本不存在")
    return target
