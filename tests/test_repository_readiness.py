"""M5k: offline release gate distinguishes a private working repo from OSS-ready.

All fixtures are made-up paths and generated tokens; no real secrets or
user/customer images are included in these tests.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.check_repository_readiness import (
    ESSENTIAL, _sensitive_path, inspect_repository, tracked_files,
)


def _fixture_repo(folder: Path, *, license_present=False) -> list[str]:
    names = list(ESSENTIAL) + ["tests/test_minimal.py", "data/examples/brand_rules.sample.json"]
    if license_present:
        names.append("LICENSE")
    for name in names:
        file = folder / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("safe fixture text\n", encoding="utf-8")
    return names


def test_essential_community_templates_use_canonical_paths():
    expected = {
        ".github/pull_request_template.md",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
    }
    legacy = {
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/ISSUE_TEMPLATE/bug_report.md",
        ".github/ISSUE_TEMPLATE/feature_request.md",
    }
    assert expected.issubset(set(ESSENTIAL))
    assert not (set(ESSENTIAL) & legacy)


def test_no_license_is_explicit_public_release_blocker(tmp_path):
    names = _fixture_repo(tmp_path)
    report = inspect_repository(tmp_path, names)
    assert report["public_release_ready"] is False
    assert any("缺少 LICENSE" in error for error in report["blockers"])
    assert report["network_accessed"] is False
    assert report["repository_modified"] is False
    assert report["tracked_files"] == len(names)


def test_authorized_license_and_safe_examples_pass_structural_checks(tmp_path):
    names = _fixture_repo(tmp_path, license_present=True)
    report = inspect_repository(tmp_path, names)
    assert report["blockers"] == []
    assert report["public_release_ready"] is True
    # This only says files exist and the small scan found no obvious secret.
    assert any("并非完整" in warning for warning in report["warnings"])


@pytest.mark.parametrize("name", [
    ".env", ".env.production", "private.pem", "id_ed25519",
    "data/posters/0123456789abcdef.png", "data/eval_runs/labels.csv",
    "data/real_trial/customer.png", "data/examples/customer.jpg",
    "local.sqlite3", ".venv/pyvenv.cfg", "data/real_skus/batch.json",
])
def test_private_material_is_always_a_release_blocker(tmp_path, name):
    names = _fixture_repo(tmp_path, license_present=True)
    f = tmp_path / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("not-an-actual-secret\n", encoding="utf-8")
    report = inspect_repository(tmp_path, names + [name])
    assert report["public_release_ready"] is False
    assert any(name in b for b in report["blockers"])


def test_safe_public_env_template_is_explicitly_allowed():
    assert _sensitive_path(".env.example") is False
    assert _sensitive_path("data/examples/brand_rules.sample.json") is False
    assert _sensitive_path("data/real_trial/my_sku.png") is True


def test_high_confidence_key_pattern_is_flagged_without_real_key(tmp_path):
    names = _fixture_repo(tmp_path, license_present=True)
    leaked = "ghp_" + ("X" * 36)  # synthetic only; not a functional token
    file = tmp_path / "docs" / "unsafe.md"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("example token: " + leaked + "\n", encoding="utf-8")
    report = inspect_repository(tmp_path, names + ["docs/unsafe.md"])
    assert report["public_release_ready"] is False
    assert any("密钥材料" in b for b in report["blockers"])
    assert leaked not in str(report)


def test_external_symlink_is_rejected(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    names = _fixture_repo(root, license_present=True)
    outside = tmp_path / "external.txt"
    outside.write_text("safe fixture\n")
    (root / "linked.txt").symlink_to(outside)
    report = inspect_repository(root, names + ["linked.txt"])
    assert any("符号链接" in b for b in report["blockers"])


def test_git_index_includes_tracked_but_not_untracked_private_file(tmp_path):
    root = tmp_path / "mini-repo"
    root.mkdir()
    try:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / "README.md").write_text("hello\n")
        (root / ".env").write_text("placeholder-not-a-key\n")
        subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    except FileNotFoundError:
        pytest.skip("git is unavailable in this environment")
    found = tracked_files(root)
    assert "README.md" in found
    assert ".env" not in found
