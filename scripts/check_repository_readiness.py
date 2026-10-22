"""M5k: small, offline Git-tracked-file guard for release preparation.

This is a conservative hygiene gate, NOT a full security/secret scanner and
NOT evidence that a private repository has already been released publicly.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Iterable

ESSENTIAL = (
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    ".gitignore",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/ISSUE_TEMPLATE/bug_report.md",
    ".github/ISSUE_TEMPLATE/feature_request.md",
)

# Only high-confidence patterns; do NOT claim to find all possible secrets.
SENSITIVE_TOKEN = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    r"|gh[pousr]_[A-Za-z0-9]{36,}"
    r"|github_pat_[A-Za-z0-9_]{40,}"
    r"|sk-[A-Za-z0-9]{32,}"
)
TEXT_SUFFIXES = frozenset({
    ".py", ".md", ".toml", ".txt", ".yaml", ".yml", ".json", ".csv", ".sh"
})
MAX_INSPECT_BYTES = 1_000_000


def tracked_files(repo: Path) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "-z", "--cached"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise ValueError("无法从 Git 索引读取受版本控制的文件") from exc
    return [p.decode("utf-8", errors="surrogateescape")
            for p in proc.stdout.split(b"\x00") if p]


def _sensitive_path(filename: str) -> bool:
    p = PurePosixPath(filename)
    if p.is_absolute() or ".." in p.parts:
        return True
    lower = filename.lower()
    basename = p.name.lower()
    if basename == ".env.example":
        return False
    if basename == ".env" or basename.startswith(".env."):
        return True
    if basename in ("id_rsa", "id_ed25519", "credentials.json", "service-account.json"):
        return True
    if p.suffix.lower() in {".pem", ".p12", ".pfx", ".key", ".sqlite",
                            ".sqlite3", ".db", ".db-journal"}:
        return True
    if any(part in {".venv", ".venv-ocr", ".pytest_cache", ".ruff_cache",
                    "__pycache__", ".git"} for part in p.parts):
        return True
    if lower.startswith("data/") and not lower.startswith("data/examples/"):
        return True
    if lower.startswith("data/examples/") and p.suffix.lower() in {
        ".png", ".jpg", ".jpeg", ".webp", ".pdf", ".csv", ".sqlite3"
    }:
        # Runtime-created images are not distributable fixture assets.
        return True
    return False


def inspect_repository(root: Path, files: Iterable[str]) -> dict:
    """Report publishing blockers, with no repo mutation or network access."""
    root = root.resolve()
    names = sorted(set(files))
    blockers: list[str] = []
    warnings: list[str] = []
    for filename in ESSENTIAL:
        if filename not in names or not (root / filename).is_file():
            blockers.append(f"缺少开源维护文件：{filename}")

    # The owner, not this script, must explicitly choose a legal license.
    if not any(name in names and (root / name).is_file()
               for name in ("LICENSE", "LICENSE.md", "LICENSE.txt")):
        blockers.append("缺少 LICENSE：必须由仓库所有者确认授权和许可证")
    if not any(name.startswith("tests/") and name.endswith(".py") for name in names):
        blockers.append("未跟踪任何自动化测试")

    inspected = 0
    for name in names:
        if _sensitive_path(name):
            blockers.append(f"存在疑似敏感文件或运行时数据：{name}")
            continue
        path = root / name
        if path.is_symlink():
            if not path.resolve().is_relative_to(root):
                blockers.append(f"跟踪的符号链接指向仓库外部：{name}")
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if path.stat().st_size > MAX_INSPECT_BYTES:
            warnings.append(f"跳过超出文本扫描上限的文件：{name}")
            continue
        inspected += 1
        try:
            payload = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            warnings.append(f"无法按 UTF-8 扫描：{name}")
            continue
        if SENSITIVE_TOKEN.search(payload):
            blockers.append(f"疑似提交高可信度密钥材料：{name}（请人工确认）")

    warnings.extend([
        "发布前仍需仓库所有者核实代码/文档/样本知识产权及依赖许可证。",
        "高可信度模式扫描并非完整的凭据检测；仍需人工审查和平台密钥扫描。",
        "此脚本不会检测 Git 历史、GitHub 仓库可见性或远程安全设置。",
    ])
    return {
        "schema_version": "m5k-1",
        "public_release_ready": not blockers,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "tracked_files": len(names),
        "text_files_inspected": inspected,
        "network_accessed": False,
        "repository_modified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline release readiness check")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--strict-public", action="store_true",
                        help="Fail unless ALL public-release blockers are resolved")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        report = inspect_repository(root, tracked_files(root))
    except (OSError, ValueError) as exc:
        parser.exit(2, f"无法扫描仓库：{exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # Always fail CI for tracked sensitive paths or key-like material; only the
    # missing LICENSE/documentation blockers are advisory before release.
    critical = any(
        msg.startswith("存在疑似敏感文件") or
        msg.startswith("疑似提交高可信度密钥材料") or
        msg.startswith("跟踪的符号链接")
        for msg in report["blockers"]
    )
    if critical or (args.strict_public and not report["public_release_ready"]):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
