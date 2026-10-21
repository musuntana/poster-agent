"""Unit tests for fail-closed branch eligibility; no network / deletions."""
from scripts.prune_merged_branches import ALLOWLIST, candidates


def branch(name, sha="a"*40, protected=False):
    return {"name": name, "commit": {"sha": sha}, "protected": protected}


def pull(name, sha="a"*40, merged=True, state="closed", number=1, base="main", repo="musuntana/poster-agent"):
    return {
        "number": number, "state": state, "merged_at": "2026-10-21T10:00:00Z" if merged else None,
        "head": {"ref": name, "sha": sha, "repo": {"full_name": repo}},
        "base": {"ref": base},
    }


def test_exact_merged_sha_is_eligible():
    name = "feature/m2b-durable-approval"
    selected, skipped = candidates([branch(name), branch("main")], [pull(name)], "musuntana/poster-agent")
    assert len(selected) == 1
    assert selected[0] == {"branch": name, "sha": "a"*40, "merged_pr": 1}
    assert not skipped


def test_never_delete_branch_updated_after_merge():
    name = "feature/m3g-real-paddleocr-ci"
    selected, skipped = candidates([branch(name, sha="b"*40)], [pull(name)], "musuntana/poster-agent")
    assert not selected
    assert "exact branch sha" in skipped[0]["reason"]


def test_protected_and_unknown_branches_not_selected():
    items = [branch("feature/m2-brief-to-spec", protected=True), branch("hotfix/my-work")]
    selected, skipped = candidates(items, [pull("feature/m2-brief-to-spec"), pull("hotfix/my-work")], "musuntana/poster-agent")
    assert not selected and len(skipped) == 2


def test_open_pr_or_unmerged_pr_blocks_deletion():
    name = "feature/m3c-human-labeling"
    selected, skipped = candidates([branch(name)], [pull(name), pull(name, merged=False, state="open", number=200)], "musuntana/poster-agent")
    assert not selected and "open pull request" in skipped[0]["reason"]
    selected2, _ = candidates([branch(name)], [pull(name, merged=False)], "musuntana/poster-agent")
    assert not selected2


def test_different_repository_or_target_branch_cannot_prove_merge():
    name = "chore/actions-budget-triggers"
    selected, _ = candidates([branch(name)], [pull(name, repo="some-other/repo")], "musuntana/poster-agent")
    assert not selected
    selected2, _ = candidates([branch(name)], [pull(name, base="release")], "musuntana/poster-agent")
    assert not selected2


def test_allowlist_is_explicit_and_never_covers_main():
    assert "main" not in ALLOWLIST
    assert "feature/m3f-ocr-evidence-overlay" in ALLOWLIST
    assert "chore/repo-hygiene-20261021" in ALLOWLIST
