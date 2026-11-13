"""`scripts/merge_pr.py` bypasses the PreToolUse hook, so it asks the hook's gate."""

from __future__ import annotations

from bros import merge as merge_pr
from bros.merge import guard

OWNER = "repo-owner"
REQUIRED = ["test", "api-schema", "frontend"]
GREEN = dict.fromkeys(REQUIRED, "SUCCESS")
SHA = "a" * 40
VERDICT_0 = f"PR #7 @ {SHA} — VERDICT: blocking=0 nits=0"
CLEAN_REVIEW = f"{VERDICT_0}\n\nno findings"


def test_checks_state_reads_absent_as_pending_and_failure_as_failed():
    assert merge_pr.checks_state(GREEN, REQUIRED) == ("green", [])
    assert merge_pr.checks_state({"test": "SUCCESS"}, REQUIRED) == (
        "pending",
        ["api-schema", "frontend"],
    )
    assert merge_pr.checks_state({**GREEN, "frontend": "FAILURE"}, REQUIRED) == (
        "failed",
        ["frontend"],
    )


def _wire(monkeypatch, tmp_path, author=OWNER, decide=None, behind=()):
    """Stub every `gh` call; return the commands shelled out."""
    ran: list[str] = []
    behind = iter(behind)
    pull_request = guard.PullRequest(
        author=author, head_owner=author, base_ref="main", checks=GREEN
    )
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr(merge_pr, "project_root", lambda: tmp_path)
    fetched = (pull_request, OWNER, REQUIRED)
    monkeypatch.setattr(guard, "_fetch", lambda n, root: fetched)
    monkeypatch.setattr(guard, "_gh", lambda *a: ran.append(" ".join(a)) or "")
    monkeypatch.setattr(
        merge_pr,
        "_view",
        lambda n: {
            "state": "OPEN",
            "isDraft": False,
            "headRefOid": "abc",
            "baseRefName": "main",
        },
    )
    monkeypatch.setattr(merge_pr, "_update_branch", lambda n, view: None)
    monkeypatch.setattr(merge_pr, "_behind", lambda view: next(behind, False))
    monkeypatch.setattr(merge_pr, "_unreviewed", lambda reviewed, head, base: [])
    monkeypatch.setattr(merge_pr, "_resolve_commit", lambda sha: sha)
    if decide is not None:
        monkeypatch.setattr(guard, "decide", decide)

    def run(command, **_):
        ran.append(" ".join(command))
        return type("Done", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(merge_pr.subprocess, "run", run)
    return ran


def test_a_strangers_pr_is_refused_before_anything_touches_it(monkeypatch, tmp_path):
    ran = _wire(monkeypatch, tmp_path, author="stranger")
    code, message = merge_pr.land("7")
    assert code == merge_pr.BLOCKED and "stranger" in message
    assert ran == []


def test_green_without_a_review_stops_short_of_the_merge(monkeypatch, tmp_path):
    ran = _wire(monkeypatch, tmp_path)
    code, _ = merge_pr.land("7")
    assert code == merge_pr.NEEDS_REVIEW
    assert not any("merge" in command for command in ran)


def test_the_hooks_gate_has_the_last_word(monkeypatch, tmp_path):
    (tmp_path / ".claude" / "reviews").mkdir(parents=True)
    (tmp_path / ".claude" / "reviews" / "7.md").write_text(CLEAN_REVIEW)

    ran = _wire(monkeypatch, tmp_path, decide=lambda payload: "the hook says no")
    assert merge_pr.land("7") == (merge_pr.BLOCKED, "the hook says no")
    assert not any("merge" in command for command in ran)

    ran = _wire(monkeypatch, tmp_path, decide=lambda payload: None)
    code, _ = merge_pr.land("7")
    assert code == merge_pr.MERGED
    assert "gh pr merge 7 --squash --delete-branch --match-head-commit abc" in ran


def test_a_review_only_clears_the_head_it_read_and_never_past_a_blocking_line():
    refusal = lambda text, unreviewed=(): merge_pr.review_refusal(  # noqa: E731
        "7", text, lambda sha: list(unreviewed), resolve=lambda sha: sha
    )
    assert refusal(CLEAN_REVIEW) is None
    assert refusal("")[0] == merge_pr.NEEDS_REVIEW
    assert refusal("no findings")[0] == merge_pr.NEEDS_REVIEW
    assert refusal(CLEAN_REVIEW, unreviewed=["b" * 40])[0] == merge_pr.NEEDS_REVIEW
    blocked = f"PR #7 @ {SHA} — VERDICT: blocking=1 nits=1\n\nBLOCKING  a.py:1 — breaks"
    assert refusal(blocked)[0] == merge_pr.BLOCKED
    assert refusal(f"{VERDICT_0}\n\nNIT  b.py:2 — meh") is None


def test_the_hook_recognises_the_command_this_script_asks_it_about():
    """If MERGE_CALL stopped matching it, `decide` would wave the merge through."""
    command = "gh pr merge 7 --squash --delete-branch"
    (arguments,) = guard.merge_arguments("Bash", command)
    assert guard.target_of(arguments) == "7"


def test_green_does_not_count_while_the_base_keeps_moving(monkeypatch, tmp_path):
    (tmp_path / ".claude" / "reviews").mkdir(parents=True)
    (tmp_path / ".claude" / "reviews" / "7.md").write_text(CLEAN_REVIEW)

    ran = _wire(monkeypatch, tmp_path, decide=lambda p: None, behind=[True, False])
    assert merge_pr.land("7")[0] == merge_pr.MERGED

    ran = _wire(monkeypatch, tmp_path, decide=lambda p: None, behind=[True] * 9)
    code, message = merge_pr.land("7")
    assert code == merge_pr.BLOCKED and "moved under it" in message
    assert not any("merge" in command for command in ran)
