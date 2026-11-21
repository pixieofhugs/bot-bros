"""Guards on `gh pr merge` (it deploys): ours, required checks green, reviewed.

``blocking_reason`` is pure, so no network is needed; protocol tests only use
inputs refused before `gh` is reached.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK_SCRIPT = Path(__file__).resolve().parent.parent / "plugins/bouncer/hooks/guard_merge.py"
_spec = importlib.util.spec_from_file_location("guard_pr_merge", HOOK_SCRIPT)
guard_pr_merge = importlib.util.module_from_spec(_spec)
sys.modules["guard_pr_merge"] = guard_pr_merge
_spec.loader.exec_module(guard_pr_merge)

OWNER = "repo-owner"
REQUIRED = ["test", "api-schema", "frontend"]
ALL_GREEN = {"test": "SUCCESS", "api-schema": "SUCCESS", "frontend": "SUCCESS"}


def a_pull_request(
    author: str = OWNER,
    head_owner: str = OWNER,
    checks: dict[str, str] | None = None,
    author_admin: bool = False,
) -> guard_pr_merge.PullRequest:
    return guard_pr_merge.PullRequest(
        author=author,
        head_owner=head_owner,
        base_ref="main",
        checks=dict(ALL_GREEN if checks is None else checks),
        author_admin=author_admin,
    )


@pytest.fixture
def review(tmp_path: Path) -> Path:
    path = tmp_path / "3110.md"
    path.write_text("no findings", encoding="utf-8")
    return path


def reason_for(pull_request, review_path, required=None) -> str | None:
    return guard_pr_merge.blocking_reason(
        "3110", pull_request, OWNER, REQUIRED if required is None else required,
        review_path,
    )


# Gate 1: ownership


def test_a_clean_own_pr_with_review_and_green_checks_is_allowed(review):
    assert reason_for(a_pull_request(), review) is None


@pytest.mark.parametrize(
    ("author", "head_owner"),
    [
        ("driveby-dev", "driveby-dev"),  # outside contributor, their fork
        (OWNER, "driveby-dev"),  # our account, a fork's branch
        ("driveby-dev", OWNER),  # someone else's commit on our branch
    ],
)
def test_a_pr_that_is_not_ours_is_denied(author, head_owner, review):
    reason = reason_for(a_pull_request(author=author, head_owner=head_owner), review)
    assert reason is not None
    assert "stranger's branch" in reason


def test_an_org_admins_branch_is_ours(review):
    """On an org-owned repo the author is a person, never the org."""
    pr = a_pull_request(author="an-admin", head_owner=OWNER, author_admin=True)
    assert reason_for(pr, review) is None


def test_an_org_admin_on_a_fork_is_still_a_stranger(review):
    pr = a_pull_request(author="an-admin", head_owner="an-admin", author_admin=True)
    assert "stranger's branch" in (reason_for(pr, review) or "")


def test_ownership_is_checked_before_anything_else(tmp_path):
    """A stranger's PR: don't even run the other gates."""
    reason = reason_for(
        a_pull_request(author="driveby-dev", head_owner="driveby-dev", checks={}),
        tmp_path / "absent.md",
    )
    assert reason is not None
    assert "stranger's branch" in reason
    assert "review" not in reason.lower().split("report")[0]


# Gate 2: required checks green on this head


def test_a_failing_required_check_is_denied(review):
    checks = dict(ALL_GREEN, frontend="FAILURE")
    reason = reason_for(a_pull_request(checks=checks), review)
    assert reason is not None
    assert "frontend (FAILURE)" in reason


def test_a_check_missing_from_this_head_is_denied_as_absent(review):
    """Stale `--watch` trap: an older green run is not in the rollup."""
    checks = {"test": "SUCCESS", "api-schema": "SUCCESS"}
    reason = reason_for(a_pull_request(checks=checks), review)
    assert reason is not None
    assert "frontend (absent)" in reason
    assert "does not carry over" in reason


def test_a_green_check_that_is_not_required_does_not_substitute(review):
    checks = {"test": "SUCCESS", "api-schema": "SUCCESS", "e2e": "SUCCESS"}
    reason = reason_for(a_pull_request(checks=checks), review)
    assert reason is not None
    assert "frontend (absent)" in reason


def test_unreadable_protection_denies_rather_than_waving_through(review):
    reason = reason_for(a_pull_request(), review, required=[])
    assert reason is not None
    assert "no required checks" in reason


# Gate 3: a non-empty review artifact


def test_a_missing_review_artifact_is_denied(tmp_path):
    reason = reason_for(a_pull_request(), tmp_path / "3110.md")
    assert reason is not None
    assert "CI green is not review" in reason
    assert "reviewed" in reason


def test_an_empty_review_artifact_does_not_count(tmp_path):
    path = tmp_path / "3110.md"
    path.write_text("   \n\n  ", encoding="utf-8")
    reason = reason_for(a_pull_request(), path)
    assert reason is not None
    assert "CI green is not review" in reason


# Parsing


@pytest.mark.parametrize(
    "command",
    [
        "gh pr merge 3110 --squash --delete-branch",
        "git fetch origin main && gh pr merge 3110 --squash",
        "gh  pr   merge 3110",
    ],
)
def test_merge_calls_are_recognised(command):
    assert guard_pr_merge.merge_arguments("Bash", command)


@pytest.mark.parametrize(
    "command",
    [
        "gh pr list --state open",
        "gh pr checks 3110 --watch",
        "gh pr ready 3110",
    ],
)
def test_non_merge_calls_are_left_alone(command):
    assert guard_pr_merge.merge_arguments("Bash", command) == []


@pytest.mark.parametrize(
    "command",
    [
        "echo 'run gh pr merge 3110 next' >> notes.md",
        'gh issue comment 7 --body "then gh pr merge 3110 --squash"',
    ],
)
def test_writing_about_a_merge_is_not_a_merge(command):
    """Close-outs name the command; denying them would cry wolf."""
    assert guard_pr_merge.merge_arguments("Bash", command) == []


def test_a_non_bash_tool_is_left_alone():
    assert guard_pr_merge.merge_arguments("Read", "gh pr merge 3110") == []


def test_every_merge_in_a_chained_command_is_seen():
    calls = guard_pr_merge.merge_arguments(
        "Bash", "gh pr merge 3110 --squash && gh pr merge 3112 --squash"
    )
    assert len(calls) == 2
    assert [guard_pr_merge.target_of(c) for c in calls] == ["3110", "3112"]


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (" 3110 --squash --delete-branch", "3110"),
        (" --squash", None),  # bare merge: resolves a PR this guard cannot name
        (" 3110 3112", None),  # ambiguous
    ],
)
def test_the_named_pr_is_extracted_or_refused(arguments, expected):
    assert guard_pr_merge.target_of(arguments) == expected


def test_rollup_flattens_check_runs_and_legacy_statuses():
    flattened = guard_pr_merge._rollup(
        [
            {"__typename": "CheckRun", "name": "test", "conclusion": "success"},
            {"__typename": "StatusContext", "context": "frontend", "state": "failure"},
            {"noise": True},
        ]
    )
    assert flattened == {"test": "SUCCESS", "frontend": "FAILURE"}


# Protocol: stdin JSON in, permission decision out


def run_hook(payload: str) -> dict | None:
    result = subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=payload,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout) if result.stdout.strip() else None


def decision_for(command: str, tool_name: str = "Bash") -> str | None:
    emitted = run_hook(
        json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": tool_name,
                "tool_input": {"command": command},
            }
        )
    )
    if emitted is None:
        return None
    output = emitted["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    return output["permissionDecision"]


def test_an_unrelated_command_is_met_with_silence():
    assert decision_for("gh pr list --state open") is None


def test_a_bare_merge_is_denied_without_reaching_the_network():
    assert decision_for("gh pr merge --squash") == "deny"


def test_a_cross_repo_merge_is_denied():
    assert decision_for("gh pr merge 3110 -R other/repo --squash") == "deny"


def test_an_unparseable_payload_denies():
    emitted = run_hook("not json at all")
    assert emitted is not None
    assert emitted["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_the_hook_never_emits_allow():
    """An allow would bypass the user's own permission prompt."""
    assert run_hook(
        json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "ls -la"},
            }
        )
    ) is None


def test_bros_guard_merge_runs_this_file() -> None:
    src = Path(__file__).resolve().parent.parent / "src"
    out = subprocess.run(
        [sys.executable, "-c", "from bros.cli import main; main()", "guard", "merge"],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "gh pr merge --squash"}}),
        capture_output=True, text=True, env={"PYTHONPATH": str(src)}, check=False)
    decision = json.loads(out.stdout)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "exactly one PR number" in decision["permissionDecisionReason"]


# _fetch: the admin lookup


def _stub_gh(monkeypatch, author, head_owner, permission):
    calls = []

    def fake(*arguments):
        calls.append(arguments)
        if arguments[:2] == ("repo", "view"):
            return json.dumps({"owner": {"login": OWNER}, "name": "r"})
        if arguments[:2] == ("pr", "view"):
            return json.dumps(
                {
                    "author": {"login": author},
                    "headRepositoryOwner": {"login": head_owner},
                    "baseRefName": "main",
                    "statusCheckRollup": [],
                }
            )
        if "permission" in arguments[1]:
            if isinstance(permission, Exception):
                raise permission
            return permission + "\n"
        return json.dumps(["test"])

    monkeypatch.setattr(guard_pr_merge, "_gh", fake)
    return calls


@pytest.mark.parametrize(
    ("permission", "admin"), [("admin", True), ("maintain", False), ("write", False)]
)
def test_only_admin_permission_makes_an_author_ours(monkeypatch, tmp_path, permission, admin):
    _stub_gh(monkeypatch, "a-person", OWNER, permission)
    pull_request, _, _ = guard_pr_merge._fetch("1", tmp_path)
    assert pull_request.author_admin is admin


@pytest.mark.parametrize(("author", "head_owner"), [(OWNER, OWNER), ("a-person", "a-person")])
def test_no_permission_lookup_for_the_owner_or_a_fork(monkeypatch, tmp_path, author, head_owner):
    calls = _stub_gh(monkeypatch, author, head_owner, RuntimeError("must not be called"))
    pull_request, _, _ = guard_pr_merge._fetch("1", tmp_path)
    assert not pull_request.author_admin
    assert not any("permission" in str(c) for c in calls)


def test_a_failed_permission_lookup_denies(monkeypatch):
    _stub_gh(monkeypatch, "a-person", OWNER, RuntimeError("gh api failed"))
    reason = guard_pr_merge.decide(
        {"tool_name": "Bash", "tool_input": {"command": "gh pr merge 1 --squash"}}
    )
    assert reason is not None and "Could not establish" in reason
