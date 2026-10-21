"""Safely prune ONLY known, merged, unprotected feature/chore GitHub branches.

Default mode is dry-run. Mutations require --apply AND
ALLOW_BRANCH_PRUNE=1. Each branch must be the exact HEAD of a merged PR into
main. A branch modified after merge, referenced by an open PR, or outside the
allowlist is never deleted.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

# Audited 2026-10-21; add new names only in a reviewed maintenance change.
ALLOWLIST = frozenset({
    "feature/m2-brief-to-spec",
    "feature/m2b-durable-approval",
    "feature/m2c-execution-reliability",
    "feature/m3a-acceptance-gate",
    "feature/m3b-ocr-benchmark",
    "feature/m3b-image-replay",
    "feature/m3c-human-labeling",
    "feature/m3d-ocr-runtime-proof",
    "feature/m3e-ocr-price-zones",
    "feature/m3f-ocr-evidence-overlay",
    "feature/m3g-real-paddleocr-ci",
    "chore/actions-budget-triggers",
    "chore/repo-hygiene-20261021",
})


def candidates(branches: list[dict], pulls: list[dict], repo: str) -> tuple[list[dict], list[dict]]:
    """Pure eligibility check; never delete a branch with ambiguous ownership."""
    found, skipped = [], []
    for branch in branches:
        name = branch.get("name", "")
        if name == "main":
            continue
        if name not in ALLOWLIST or branch.get("protected") is not False:
            skipped.append({"branch": name, "reason": "not allowlisted or protected"})
            continue
        head_sha = (branch.get("commit") or {}).get("sha")
        if not head_sha:
            skipped.append({"branch": name, "reason": "missing branch head sha"})
            continue
        associated = [
            pr for pr in pulls
            if (pr.get("head") or {}).get("ref") == name
            and ((pr.get("head") or {}).get("repo") or {}).get("full_name") == repo
        ]
        if any(pr.get("state") == "open" for pr in associated):
            skipped.append({"branch": name, "reason": "open pull request"})
            continue
        merged = [
            pr for pr in associated
            if pr.get("merged_at")
            and (pr.get("base") or {}).get("ref") == "main"
            and (pr.get("head") or {}).get("sha") == head_sha
        ]
        if not merged:
            skipped.append({"branch": name, "reason": "no merged PR for the exact branch sha"})
            continue
        found.append({"branch": name, "sha": head_sha, "merged_pr": max(p["number"] for p in merged)})
    return sorted(found, key=lambda item: item["branch"]), skipped


def _api(*args: str) -> str:
    request = subprocess.run(
        ["gh", "api", *args], capture_output=True, text=True, check=False,
    )
    if request.returncode:
        raise RuntimeError(f"GitHub API request failed (exit={request.returncode}): "
                           f"{request.stderr[-300:]}")
    return request.stdout


def _get_pages(repo: str, endpoint: str) -> list[dict]:
    # This maintenance workflow deliberately fails closed instead of silently
    # evaluating an incomplete first page of a large repository.
    raw = json.loads(_api(f"repos/{repo}/{endpoint}?per_page=100"))
    if not isinstance(raw, list) or len(raw) >= 100:
        raise RuntimeError("Too many branch/PR records; pagination-safe audit required")
    return raw


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--apply", action="store_true", help="delete eligible branches")
    parser.add_argument("--output", type=Path, default=Path("data/eval_runs/branch-prune.json"))
    args = parser.parse_args()
    if args.repo != "musuntana/poster-agent":
        parser.error("This audited cleanup only supports musuntana/poster-agent")
    if args.apply and os.environ.get("ALLOW_BRANCH_PRUNE") != "1":
        parser.error("--apply requires ALLOW_BRANCH_PRUNE=1")
    branches = _get_pages(args.repo, "branches")
    pulls = _get_pages(args.repo, "pulls?state=all&dummy=1")
    eligible, skipped = candidates(branches, pulls, args.repo)
    report = {
        "repository": args.repo, "mode": "apply" if args.apply else "dry-run",
        "eligible": eligible, "skipped": skipped, "deleted": [], "failed": [],
    }
    if args.apply:
        for item in eligible:
            name = item["branch"]
            # Fetch current branch SHA again immediately before deleting.
            import urllib.parse
            escaped = urllib.parse.quote(name, safe="")
            try:
                live = json.loads(_api(f"repos/{args.repo}/branches/{escaped}"))
                if live.get("protected") or (live.get("commit") or {}).get("sha") != item["sha"]:
                    report["failed"].append({"branch": name, "reason": "head/protection changed"})
                    continue
                _api("--method", "DELETE", f"repos/{args.repo}/git/refs/heads/{name}")
                report["deleted"].append(name)
            except RuntimeError as exc:
                report["failed"].append({"branch": name, "reason": str(exc)[:300]})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
