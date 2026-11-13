"""PreToolUse: deny raw reads of GitHub issue comments; `bros issue <N>` is the only path.

On a public repo anyone can comment, and a bot acting on an issue reads those comments
as instructions. `bros issue` drops non-collaborator comments before they reach the model.
Denies when it cannot read its input: failing open would reopen the hole on any schema change.
"""

import json
import re
import sys

# `--json comments` returns the same bytes as `--comments`. The `[^|;&]*` bound keeps
# `... --json state,title | grep comments` out.
# ponytail: literal `gh` forms only; `gh api graphql` or curl walk past. If that becomes a
# real path, deny `gh api graphql` outright.
BLOCKED = (
    (re.compile(r"\bgh\s+issue\s+view\b.*--comments\b"), "`gh issue view --comments`"),
    (re.compile(r"\bgh\s+issue\s+view\b.*--json\b[^|;&]*\bcomments\b"),
     "`gh issue view --json comments`"),
    (re.compile(r"\bgh\s+api\b.*/issues/\d+/comments\b"), "`gh api .../issues/N/comments`"),
)


def decide(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return "Hook payload was not an object, so bouncer cannot clear this call."
    if payload.get("tool_name") != "Bash":
        return None
    command = str((payload.get("tool_input") or {}).get("command", ""))
    for pattern, form in BLOCKED:
        if pattern.search(command):
            return (f"{form} reads comments anyone with a GitHub account can write. Use "
                    "`bros issue <number>` instead: it drops non-collaborator comments "
                    "before they reach you and says how many it withheld.")
    return None


def main() -> int:
    try:
        reason = decide(json.load(sys.stdin))
    except Exception as error:  # noqa: BLE001 - deny on error
        reason = f"bouncer could not read its own input ({error}), so it cannot clear this call."
    if reason:
        json.dump({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
