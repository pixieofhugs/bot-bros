"""Land one PR: ready it, update it, wait for green, gate it, merge it.

A merge's mechanics in one process instead of five model turns. This command's
`gh pr merge` is a subprocess the PreToolUse hook never sees, so it calls the
bouncer merge guard's own `decide` and merges only on silence. The repo is the
one the command runs in (git's top level), so the guard checks that repo.
Ownership is checked first: a stranger's branch is not readied, updated or merged.

Exit codes:
    0  merged
    1  blocked — a check failed, the PR is not ours, or `gh` could not answer
    3  green, but not reviewed at this head — get the review and re-run

The review file is the whole merge decision here, so its first line must name
the reviewed commit, only `update-branch` merges may have landed since, and it
must say `blocking=0`. Steps are idempotent; re-run after a 3 (or fixed 1).

A queue (`<n> <n> ...` or `--queue FILE`, one number per line, `#` comments) lands
each PR in order under a file lock, stopping at the first that does not merge
(printing its CI `FAILED` / `^E ` lines if checks failed).

Usage:
    bros merge <pr-number> [<pr-number> ...]
    bros merge --queue FILE
"""

from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from bros import config as cfg
from bros.guard import hooks_dir

_spec = importlib.util.spec_from_file_location("guard_merge", hooks_dir() / "guard_merge.py")
guard = sys.modules.setdefault("guard_merge", importlib.util.module_from_spec(_spec))
_spec.loader.exec_module(guard)

POLL_SECONDS = 20
CHECKS_TIMEOUT_SECONDS = 45 * 60
HEAD_MOVE_TIMEOUT_SECONDS = 90
UPDATE_ROUNDS = 3

#: Conclusions waiting will not turn green.
FAILED = {
    "FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR",
    "SKIPPED", "NEUTRAL", "STALE",
}  # fmt: skip

#: Consecutive `gh` failures tolerated while polling.
POLL_RETRIES = 3

#: Review line 1, written by foreman's `reviewer` agent; extra `k=v` fields ignored.
VERDICT = re.compile(r"^PR #(\d+) @ ([0-9a-f]{7,40}) — VERDICT:((?: [a-z][a-z-]*=\d+)+)\s*$")

#: Hand-off line `PR #N after: #M[, #K]`: N waits for M and K to merge.
AFTER = re.compile(r"^\W*PR #(\d+)\s+after:\s*((?:[ ,]*#\d+)+)\s*(?:#.*)?$")

MERGED, BLOCKED, NEEDS_REVIEW = 0, 1, 3


def checks_state(checks: dict[str, str], required: list[str]) -> tuple[str, list[str]]:
    """`("green" | "failed" | "pending", names)`; absent = not reported yet."""
    failed = [name for name in required if checks.get(name) in FAILED]
    if failed:
        return "failed", failed
    pending = [name for name in required if checks.get(name) != "SUCCESS"]
    return ("pending", pending) if pending else ("green", [])


def _view(number: str) -> dict:
    return json.loads(
        guard._gh("pr", "view", number, "--json", "state,isDraft,headRefOid,baseRefName")
    )


def _behind(view: dict) -> bool:
    repo = guard._gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner").strip()
    behind = guard._gh(
        "api",
        f"repos/{repo}/compare/{view['baseRefName']}...{view['headRefOid']}",
        "--jq",
        ".behind_by",
    ).strip()
    return behind != "0"


def _update_branch(number: str, view: dict) -> None:
    if not _behind(view):
        return
    guard._gh("pr", "update-branch", number)
    # Async: wait for the head to move, or we'd read the old commit's green run.
    deadline = time.monotonic() + HEAD_MOVE_TIMEOUT_SECONDS
    while _view(number)["headRefOid"] == view["headRefOid"]:
        if time.monotonic() > deadline:
            raise RuntimeError("update-branch was accepted but the head never moved")
        time.sleep(3)


def _wait_for_green(number: str, root: Path) -> str | None:
    """Block until checks settle; why not green, or None."""
    deadline = time.monotonic() + CHECKS_TIMEOUT_SECONDS
    blips = 0
    while True:
        try:
            pull_request, _, required = guard._fetch(number, root)
            blips = 0
        except Exception:  # noqa: BLE001 - retried, then re-raised
            blips += 1
            if blips > POLL_RETRIES:
                raise
            time.sleep(POLL_SECONDS)
            continue
        if not required:
            return "branch protection reported no required checks"
        state, names = checks_state(pull_request.checks, required)
        if state == "green":
            return None
        if state == "failed":
            return (
                f"required checks failed: {', '.join(names)}. "
                "Read `gh run view <run-id> --log-failed` before reacting."
            )
        if time.monotonic() > deadline:
            return f"timed out waiting for: {', '.join(names)}"
        time.sleep(POLL_SECONDS)


def _unreviewed(reviewed: str, head: str, base: str) -> list[str]:
    subprocess.run(["git", "fetch", "-q", "origin", head, base], check=True, timeout=120)
    listed = subprocess.run(
        ["git", "rev-list", "--no-merges", f"{reviewed}..{head}", f"^origin/{base}"],
        capture_output=True, text=True, check=True, timeout=60,
    )  # fmt: skip
    return listed.stdout.split()


def _resolve_commit(sha: str) -> str | None:
    done = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"],
        capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    return done.stdout.strip() if done.returncode == 0 else None


def review_refusal(
    number: str, text: str, unreviewed_since, resolve=None
) -> tuple[int, str] | None:
    """Why this review does not clear the merge, or None. Git is injected for tests."""
    again = f"dispatch the `reviewer` agent for #{number}, then re-run this."
    if not text.strip():
        return NEEDS_REVIEW, f"PR #{number} is green on its current head. No review yet — {again}"
    first = text.strip().splitlines()[0]
    verdict = VERDICT.match(first)
    if not verdict or verdict[1] != number:
        return NEEDS_REVIEW, (
            f"The review for PR #{number} does not open with its verdict line "
            f"(`PR #{number} @ <sha> — VERDICT: blocking=<k> nits=<m>`) — {again}"
        )
    pairs = [field.split("=") for field in verdict[3].split()]
    fields = dict(pairs)
    if len(fields) != len(pairs):
        return NEEDS_REVIEW, f"The verdict line for PR #{number} repeats a field — {again}"
    if "blocking" not in fields:
        return NEEDS_REVIEW, f"The verdict line for PR #{number} has no `blocking=` field — {again}"
    if int(fields["blocking"]) != 0:
        return BLOCKED, (
            f"The review for PR #{number} has {fields['blocking']} blocking finding(s). Fix "
            "them and have it reviewed again; a finding you believe is wrong goes to the "
            "user, not past this."
        )
    reviewed = (resolve or _resolve_commit)(verdict[2])
    if not reviewed:
        return NEEDS_REVIEW, (
            f"The review for PR #{number} names `{verdict[2]}`, which is not exactly one "
            f"commit here, so it cannot be matched to this head — {again}"
        )
    # shortcut: a hand-made merge commit smuggling edits reads as reviewed;
    # compare trees if writers ever merge by hand.
    unreviewed = unreviewed_since(reviewed)
    if unreviewed:
        return NEEDS_REVIEW, (
            f"PR #{number} has {len(unreviewed)} commit(s) the review never saw "
            f"({unreviewed[0][:9]}…) — {again}"
        )
    return None


def project_root() -> Path:
    """The repo this runs in. The guard reads CLAUDE_PROJECT_DIR, so point it here too."""
    root = cfg.repo_root()
    os.environ["CLAUDE_PROJECT_DIR"] = str(root)
    return root


def unmerged_predecessors(number: str, root: Path) -> list[tuple[str, str]]:
    """`(pr, state)` for each PR a hand-off says `number` goes `after:` that is not merged.

    Raises on any hand-off line that mentions `after:` but is not that form.
    """
    waits: list[str] = []
    for handoff in sorted((root / guard.REVIEW_DIR).glob("HANDOFF-*.md")):
        for line in handoff.read_text(encoding="utf-8").splitlines():
            if "after:" not in line:
                continue
            found = AFTER.match(line)
            named = re.findall(r"\d+", found[2]) if found else []
            if not found or found[1] in named:
                raise RuntimeError(
                    f"{handoff.name} has an `after:` line that is not `PR #N after: #M`: {line!r}"
                )
            if found[1] == number:
                waits += named
    states = {m: _view(m)["state"] for m in dict.fromkeys(waits)}
    return [(m, state) for m, state in states.items() if state != "MERGED"]


def land(number: str, say=lambda _: None) -> tuple[int, str]:
    root = project_root()
    review = root / guard.REVIEW_DIR / f"{number}.md"

    pull_request, owner, _ = guard._fetch(number, root)
    if pull_request.author != owner or pull_request.head_owner != owner:
        return BLOCKED, guard.blocking_reason(number, pull_request, owner, [], review) or ""

    view = _view(number)
    try:
        waiting = unmerged_predecessors(number, root) if view["state"] == "OPEN" else []
    except RuntimeError as error:
        return BLOCKED, f"PR #{number}: {error}"
    for m, state in waiting:
        if state == "CLOSED":
            return BLOCKED, (
                f"PR #{number} goes after #{m}, which is closed unmerged and will never land; "
                f"if #{number} no longer needs it, remove #{m} from the hand-off's `after:` line."
            )
    if waiting:
        names = ", ".join(f"#{m}" for m, _ in waiting)
        verb = "are" if len(waiting) > 1 else "is"
        return BLOCKED, (
            f"PR #{number} goes after {names} (the hand-off's `after:`), which {verb} not "
            "merged. Land that first."
        )
    if view["state"] == "MERGED":
        return MERGED, f"PR #{number} is already merged."
    if view["state"] != "OPEN":
        return BLOCKED, f"PR #{number} is {view['state']}, not open."
    if view["isDraft"]:
        say("marking ready")
        guard._gh("pr", "ready", number)

    # The base moves under CI (parallel merges) and protection refuses a behind
    # head, so green counts only if still level with the base afterwards.
    for _ in range(UPDATE_ROUNDS):
        say("waiting for checks")
        _update_branch(number, _view(number))
        reason = _wait_for_green(number, root)
        if reason:
            return BLOCKED, f"PR #{number}: {reason}"
        if not _behind(_view(number)):
            break
    else:
        return BLOCKED, (
            f"PR #{number}: `{pull_request.base_ref}` moved under it {UPDATE_ROUNDS} times "
            "while CI ran. Re-run this when the other merges have settled."
        )

    head = _view(number)["headRefOid"]
    refusal = review_refusal(
        number,
        review.read_text(encoding="utf-8") if review.is_file() else "",
        lambda reviewed: _unreviewed(reviewed, head, pull_request.base_ref),
    )
    if refusal:
        return refusal

    say("merging")
    command = f"gh pr merge {number} --squash --delete-branch"
    reason = guard.decide({"tool_name": "Bash", "tool_input": {"command": command}})
    if reason:
        return BLOCKED, reason

    merged = subprocess.run(
        [*command.split(), "--match-head-commit", head],
        capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    # `gh` can fail after merging (local branch delete), so trust the PR state.
    if merged.returncode != 0 and _view(number)["state"] != "MERGED":
        return BLOCKED, f"`{command}` failed: {merged.stderr.strip()}"
    fetched = subprocess.run(["git", "fetch", "-q", "origin", pull_request.base_ref], timeout=120)
    tail = "re-fetched" if fetched.returncode == 0 else "NOT re-fetched — fetch it yourself"
    return MERGED, f"Merged PR #{number}; origin/{pull_request.base_ref} {tail}."


def failed_lines(number: str) -> list[str]:
    head = _view(number)["headRefOid"]
    runs = json.loads(
        guard._gh("run", "list", "--commit", head,
                  "--json", "databaseId,conclusion", "--limit", "20")
    )
    lines: list[str] = []
    for run in runs:
        if run["conclusion"] in FAILED:
            log = guard._gh("run", "view", str(run["databaseId"]), "--log-failed")
            lines += [x for x in log.splitlines() if "FAILED" in x or re.search(r"Z E ", x)]
    return lines


def run_queue(numbers: list[str], land_fn=land, root: Path | None = None) -> int:
    root = root or project_root()
    lock_path = root / ".claude" / "merge-queue.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for number in numbers:
            try:
                code, message = land_fn(
                    number, lambda state, n=number: print(f"#{n}: {state}", flush=True))
            except Exception as error:  # noqa: BLE001 - anything unanswered is a refusal
                code, message = BLOCKED, f"PR #{number}: {error}"
            if "checks failed" in message:
                try:
                    print("\n".join(failed_lines(number)))
                except Exception as error:  # noqa: BLE001 - the log is a courtesy
                    print(f"(could not read the failing log: {error})")
            print(f"#{number}: {message}", flush=True)
            if code != MERGED:
                return code
    return MERGED


def read_queue(path: str) -> list[str]:
    """`123` or `#123` per line; anything else is a comment."""
    found = (re.match(r"#?(\d+)\b", line.strip()) for line in Path(path).read_text().splitlines())
    return [m[1] for m in found if m]


def main(argv: list[str]) -> int:
    args = list(argv)
    queue = len(args) == 2 and args[0] == "--queue"
    if queue:
        args = read_queue(args[1])
    if not args or not all(a.isdigit() for a in args):
        print(__doc__.rsplit("Usage:", 1)[1].strip(), file=sys.stderr)
        return BLOCKED
    if queue or len(args) > 1:
        return run_queue(args)
    try:
        code, message = land(args[0])
    except Exception as error:  # noqa: BLE001 - anything unanswered is a refusal
        code, message = BLOCKED, f"PR #{args[0]}: {error}"
    print(message)
    return code

