"""M1 本地版存储，供单机开发使用；生产环境需要事务和权限。"""
from __future__ import annotations
import io
import json
import os
import threading
from pathlib import Path
from uuid import uuid4
from PIL import Image
from .schemas import PosterSpec

LOCK = threading.RLock()


def root() -> Path:
    return Path(os.getenv("POSTER_DATA_DIR", "data/posters")).resolve()


def poster_dir(poster_id: str) -> Path:
    if len(poster_id) != 32 or any(c not in "0123456789abcdef" for c in poster_id):
        raise FileNotFoundError("海报不存在")
    path = root() / poster_id
    if not path.is_dir():
        raise FileNotFoundError("海报不存在")
    return path


def save_png(image: Image.Image, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG")


def create(spec: PosterSpec, product: Image.Image, logo: Image.Image | None,
           background: Image.Image, render_bytes: bytes) -> dict:
    with LOCK:
        poster_id = uuid4().hex
        path = root() / poster_id
        assets = path / "assets"
        assets.mkdir(parents=True, exist_ok=False)
        save_png(product, assets / "product.png")
        if logo:
            save_png(logo, assets / "logo.png")
        save_png(background, assets / "background.png")
        _save_version(path, 1, spec, render_bytes)
        _atomic_json(path / "meta.json", {"poster_id": poster_id, "latest_version": 1,
                                           "background_mode": spec.background_mode})
        return view(poster_id)


def _atomic_json(path: Path, value: dict) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _save_version(path: Path, version: int, spec: PosterSpec, render_bytes: bytes) -> None:
    versions = path / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    (versions / f"v{version}.png").write_bytes(render_bytes)
    _atomic_json(versions / f"v{version}.json", spec.model_dump(mode="json"))


def view(poster_id: str) -> dict:
    path = poster_dir(poster_id)
    meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
    latest = meta["latest_version"]
    spec = json.loads((path / "versions" / f"v{latest}.json").read_text(encoding="utf-8"))
    return {"poster_id": poster_id, "version": latest, "spec": spec,
            "image_url": f"/api/posters/{poster_id}/versions/{latest}/image",
            "versions": list(range(1, latest + 1)), "background_mode": meta["background_mode"]}


def read_assets(poster_id: str) -> tuple[Image.Image, Image.Image | None, Image.Image]:
    assets = poster_dir(poster_id) / "assets"
    def loaded(p):
        with Image.open(p) as img:
            img.load()
            return img.convert("RGBA")
    return (loaded(assets / "product.png"),
            loaded(assets / "logo.png") if (assets / "logo.png").exists() else None,
            loaded(assets / "background.png"))


def new_version(poster_id: str, spec: PosterSpec, render_bytes: bytes) -> dict:
    with LOCK:
        path = poster_dir(poster_id)
        meta_path = path / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        version = int(meta["latest_version"]) + 1
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
