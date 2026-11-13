"""PreToolUse hook: gate `gh pr merge` on the three things a merge assumes.

A merge to the default branch is usually the command that starts a deploy. An
instruction to check first holds only as long as the model's attention holds: in
one evaluation run an agent merged a PR while writing "code reviewed" in its
close-out, having run no review at all. The instruction was present and correct
the whole time. That is the shape of rule that belongs in a hook rather than in
prose, because the prose is a prompt handed to the same model that is deciding
whether to follow it.

The three gates, in the order they are checked:

1. **Ownership.** The PR's author *and* its head repository owner must both be
   the owner of this repo. On a public repo a fork's branch is a stranger's
   code, and merging it is a deploy. Checked first, because the rule for such a
   PR is not only "do not merge it" but "do not check it out or run its gates",
   so nothing else should run against a PR that fails here.
2. **Required checks green on the current head.** Read from branch protection
   live, never from a list written down anywhere, because written lists rot.
   `statusCheckRollup` is head-scoped, which is what closes the stale-`--watch`
   trap: a green run belonging to an older push is simply absent from it.
3. **A review artifact exists.** `.claude/reviews/<N>.md`, non-empty.

**This hook denies when it cannot answer.** A failed `gh` call, a timeout, an
unparseable payload, unreadable branch protection: all deny. A gate that fails
open is not a gate, and every one of these failures is loud and fixed in
minutes.

# ponytail: gate 3 is a sentinel, not a proof. It cannot judge a review; it
# only forces the step to leave something behind, which is exactly the failure
# observed (a claimed review with no output). If thin files become the dodge,
# the upgrade is to have the reviewer write the file itself with the head SHA in
# it, and check that SHA here.

Shipped by the Bot Bros bouncer plugin, which runs it on every Bash call. Check it
in a new session: `gh pr merge 1 --squash` must be denied (no review artifact).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

GH_TIMEOUT_SECONDS = 20

REVIEW_DIR = Path(".claude") / "reviews"

#: `gh pr merge <N>`, only where `gh` actually starts a command — at the start
#: of the line or after a separator. Matching the bare string anywhere denies
#: `echo "... gh pr merge ..."` too, which sounds harmless until you notice
#: that the close-out reports and issue comments agents write are full of
#: that string. A guard that blocks writing *about* merging is a guard
#: someone switches off, and a switched-off guard protects nothing.
#:
#: The number is optional to `gh` itself — bare `gh pr merge` resolves the
#: current branch's PR — but it is not optional here: a hook that has to guess
#: which PR is being merged cannot honestly gate it.
#:
# ponytail: command position by separator, not a shell parse. `echo "a && gh pr
# merge 1"` still matches. Reach for a real tokenizer only if that shows up.
MERGE_CALL = re.compile(r"(?:^|[;&|]|\n)\s*gh\s+pr\s+merge\b([^|;&]*)")
PR_NUMBER = re.compile(r"(?<![\w/#-])(\d+)(?![\w-])")

#: `--repo`/`-R` retargets the call at a repo that is not the working tree, so
#: every fact this hook gathers from the working tree would describe the wrong
#: repository. Refused rather than followed.
CROSS_REPO = re.compile(r"(?:^|\s)(?:-R|--repo)(?:[=\s]|$)")


@dataclass(frozen=True)
class PullRequest:
    """The facts about a PR that a merge decision turns on."""

    author: str
    head_owner: str
    base_ref: str
    #: check name -> conclusion, upper-cased, for the current head commit only
    checks: dict[str, str]


def merge_arguments(tool_name: str, command: str) -> list[str]:
    """Return the argument text of each `gh pr merge` in this command."""
    if tool_name != "Bash":
        return []
    return MERGE_CALL.findall(command)


def target_of(arguments: str) -> str | None:
    """The PR number this `gh pr merge` names, or None if it names none."""
    found = PR_NUMBER.findall(arguments)
    return found[0] if len(found) == 1 else None


def blocking_reason(
    number: str,
    pull_request: PullRequest,
    repo_owner: str,
    required: list[str],
    review: Path,
) -> str | None:
    """Why this merge must not proceed, or None to leave it alone.

    Pure: every fact it needs is an argument, so the decision is testable
    without a network, a repo, or a GitHub account.
    """
    if pull_request.author != repo_owner or pull_request.head_owner != repo_owner:
        return (
            f"PR #{number} was authored by `{pull_request.author}` from "
            f"`{pull_request.head_owner}`, not `{repo_owner}`. This repo is "
            "public and a merge is what ships, so merging it ships a stranger's "
            "branch. Do not merge it, do not check "
            "it out, and do not run its gates — report it as an unexpected PR."
        )

    if not required:
        return (
            f"Branch protection on `{pull_request.base_ref}` reported no "
            "required checks, so there is nothing to verify green against. "
            "Confirm protection is still configured before merging."
        )

    unmet = [
        f"{name} ({pull_request.checks.get(name, 'absent')})"
        for name in required
        if pull_request.checks.get(name) != "SUCCESS"
    ]
    if unmet:
        return (
            f"PR #{number} has required checks that are not green on its "
            f"current head: {', '.join(unmet)}. `absent` means no run for this "
            "head commit — an older green run does not carry over, so push or "
            "`gh pr update-branch` and wait for the new run."
        )

    if not review.is_file() or not review.read_text(encoding="utf-8").strip():
        return (
            f"No review artifact for PR #{number} at `{review}`. CI green is "
            "not review. Dispatch the `reviewer` agent for {n}, or write findings — or "
            "the words `no findings` — to that file before merging.".replace(
                "{n}", number
            )
        )

    return None


def _gh(*arguments: str) -> str:
    """Run `gh` and return stdout, raising on any non-zero exit or timeout."""
    result = subprocess.run(
        ["gh", *arguments],
        capture_output=True,
        text=True,
        timeout=GH_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        call = " ".join(arguments)
        raise RuntimeError(f"`gh {call}` failed: {result.stderr.strip()}")
    return result.stdout


def _rollup(entries: object) -> dict[str, str]:
    """Flatten `statusCheckRollup` — check runs and legacy statuses alike."""
    checks: dict[str, str] = {}
    if not isinstance(entries, list):
        return checks
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or entry.get("context")
        verdict = entry.get("conclusion") or entry.get("state")
        if name and verdict:
            checks[str(name)] = str(verdict).upper()
    return checks


def _fetch(number: str, project_root: Path) -> tuple[PullRequest, str, list[str]]:
    repo = json.loads(_gh("repo", "view", "--json", "owner,name"))
    owner = str(repo["owner"]["login"])

    raw = json.loads(
        _gh(
            "pr",
            "view",
            number,
            "--json",
            "author,headRepositoryOwner,baseRefName,statusCheckRollup",
        )
    )
    pull_request = PullRequest(
        author=str((raw.get("author") or {}).get("login", "")),
        head_owner=str((raw.get("headRepositoryOwner") or {}).get("login", "")),
        base_ref=str(raw.get("baseRefName", "")),
        checks=_rollup(raw.get("statusCheckRollup")),
    )

    contexts = json.loads(
        _gh(
            "api",
            f"repos/{owner}/{repo['name']}/branches/"
            f"{pull_request.base_ref}/protection/required_status_checks",
            "--jq",
            ".contexts",
        )
    )
    required = [str(c) for c in contexts] if isinstance(contexts, list) else []

    # project_root is only threaded through so the caller owns path resolution.
    _ = project_root
    return pull_request, owner, required


def decide(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return "Hook payload was not an object, so it cannot clear this merge."

    tool_name = str(payload.get("tool_name", ""))
    command = str((payload.get("tool_input") or {}).get("command", ""))
    calls = merge_arguments(tool_name, command)
    if not calls:
        return None

    project_root = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()

    for arguments in calls:
        if CROSS_REPO.search(arguments):
            return (
                "`gh pr merge` with `--repo` targets a repository other than "
                "this working tree, so this guard cannot verify ownership, "
                "checks, or review for it. Run the merge from that repo."
            )
        number = target_of(arguments)
        if number is None:
            return (
                "`gh pr merge` did not name exactly one PR number. Name it "
                "explicitly — a merge this guard cannot identify is a merge "
                "it cannot gate, and a merge is what ships."
            )
        try:
            pull_request, owner, required = _fetch(number, project_root)
        except Exception as error:  # noqa: BLE001 - see docstring: deny on error
            return (
                f"Could not establish whether PR #{number} is safe to merge "
                f"({error}), so this guard cannot clear it."
            )
        reason = blocking_reason(
            number,
            pull_request,
            owner,
            required,
            project_root / REVIEW_DIR / f"{number}.md",
        )
        if reason is not None:
            return reason

    return None


def main() -> int:
    try:
        reason = decide(json.load(sys.stdin))
    except Exception as error:  # noqa: BLE001 - see docstring: deny on error
        reason = (
            f"Hook could not read its own input ({error}), so it cannot clear "
            "this call. See the bouncer plugin's hooks/guard_merge.py."
        )

    # Silence is how a PreToolUse hook defers to the normal permission flow.
    # An explicit "allow" would wave the call past the user's own prompt.
    if reason is None:
        return 0

    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
