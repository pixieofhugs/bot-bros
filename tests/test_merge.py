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


def test_a_pr_goes_after_what_its_handoff_names_and_only_then(monkeypatch, tmp_path):
    reviews = tmp_path / ".claude" / "reviews"
    reviews.mkdir(parents=True)
    (reviews / "7.md").write_text(CLEAN_REVIEW)
    (reviews / "HANDOFF-wave.md").write_text(
        "| #7 | Closes #1 | fix | wt |\n"
        "PR #7 after: #5, #6\n"
        "PR #8 after: #9\n"
        "PR #70 after: #9\n"
    )

    state = {"5": "OPEN", "6": "MERGED"}
    ran = _wire(monkeypatch, tmp_path, decide=lambda p: None)
    view = merge_pr._view
    monkeypatch.setattr(
        merge_pr, "_view", lambda n: {**view(n), "state": state.get(n, "OPEN")}
    )
    code, message = merge_pr.land("7")
    assert code == merge_pr.BLOCKED and "#5" in message and "#6" not in message
    assert not any("merge" in command for command in ran)

    state["5"] = "MERGED"
    assert merge_pr.land("7")[0] == merge_pr.MERGED


def _after(monkeypatch, tmp_path, handoff, states):
    reviews = tmp_path / ".claude" / "reviews"
    reviews.mkdir(parents=True)
    (reviews / "7.md").write_text(CLEAN_REVIEW)
    (reviews / "HANDOFF-wave.md").write_text(handoff)
    _wire(monkeypatch, tmp_path, decide=lambda p: None)
    view = merge_pr._view
    monkeypatch.setattr(merge_pr, "_view", lambda n: {**view(n), "state": states.get(n, "OPEN")})
    return merge_pr.land("7")


def test_a_closed_predecessor_blocks_and_says_it_will_never_land(monkeypatch, tmp_path):
    code, message = _after(monkeypatch, tmp_path, "PR #7 after: #5\n", {"5": "CLOSED"})
    assert code == merge_pr.BLOCKED and "closed unmerged" in message and "remove #5" in message


def test_two_unmerged_predecessors_read_in_the_plural(monkeypatch, tmp_path):
    _, message = _after(monkeypatch, tmp_path, "PR #7 after: #5, #6\n", {})
    assert "#5, #6" in message and "are not merged" in message


def test_an_after_line_that_does_not_parse_blocks_loudly(monkeypatch, tmp_path):
    for line in ("PR #7 lands first; PR #9 after: #7", "PR #7 after: #7",
                 "PR #7 after: #5 (shares a migration)"):  # fmt: skip
        sub = tmp_path / str(abs(hash(line)))
        sub.mkdir()
        code, message = _after(monkeypatch, sub, line + "\n", {})
        assert code == merge_pr.BLOCKED and line in message


def test_after_lines_about_other_prs_or_prose_never_block(monkeypatch, tmp_path):
    handoff = (
        "Merge order: land the schema after: the API\n"
        "PR #9 after: #9\n"
        "PR #<N> after: #<M>[, #<K>]\n"
        "PR #70 after: nonsense\n"
    )
    assert _after(monkeypatch, tmp_path, handoff, {})[0] == merge_pr.MERGED
