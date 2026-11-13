"""PostToolUse hook (Bash): after `gh pr create|edit`, warn on an over-budget PR body.

Warns (exit 2, stderr goes to the agent); never blocks. Counts words, not lines,
because one bullet can be a paragraph. Attribution footers are free.
"""

import json
import re
import subprocess
import sys

from bros import config as cfg

FREE = re.compile(
    r"Generated with \[Claude Code\]|^https://claude\.ai/code/|^Co-Authored-By:", re.I
)


def words(body: str) -> int:
    return sum(
        len(line.split()) for line in body.splitlines() if not FREE.search(line.strip())
    )


def pr_number(command: str, response: str) -> str | None:
    if not re.search(r"\bgh\s+pr\s+(create|edit)\b", command):
        return None
    match = re.search(r"/pull/(\d+)", response) or re.search(
        r"\bgh\s+pr\s+edit\s+#?(\d+)", command
    )
    return match.group(1) if match else None


def main(args: list[str]) -> int:
    conf, note = cfg.for_hook()
    limit = conf.get("pr_body", {}).get("words", 250)
    try:
        payload = json.load(sys.stdin)
        number = pr_number(
            payload["tool_input"]["command"],
            json.dumps(payload.get("tool_response", "")),
        )
    except Exception as err:
        print(f"hush: could not read the hook payload ({err!r}); PR body not checked.",
              file=sys.stderr)
        return 2
    if not number:
        return 0
    if note:
        print(f"hush: {note}", file=sys.stderr)
    try:
        body = subprocess.run(
            ["gh", "pr", "view", number, "--json", "body", "-q", ".body"],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        ).stdout
    except Exception as err:
        print(f"hush: could not read PR #{number}'s body with gh ({err!r}); "
              "PR body not checked. Is gh installed and logged in?", file=sys.stderr)
        return 2
    count = words(body)
    if count <= limit:
        return 2 if note else 0
    print(
        f"PR #{number} body is {count} words; the budget is {limit}. Trim it with "
        f"`gh pr edit {number} --body-file -`: no file list, one line per deviation, "
        "no restating the diff.",
        file=sys.stderr,
    )
    return 2
