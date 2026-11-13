"""PreToolUse: protected paths need the human. `[bouncer] ask` prompts, `[bouncer] deny` refuses.

Globs are fnmatch over repo-relative paths (`*` crosses `/`). Defaults: ask on `.github/*`.
Denies when it cannot read its input: a guard that fails open is not a guard.
"""

import json
import os
import re
import shlex
import sys
import tomllib
from fnmatch import fnmatch
from pathlib import Path

DEFAULT_ASK = [".github/*"]
FILE_TOOLS = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path",
              "NotebookEdit": "notebook_path"}
# ponytail: a write-verb regex, not a shell parse. `python -c "open(...)"` or a script that
# writes the file walks past; deny Bash on protected paths outright if that shows up.
WRITES = re.compile(
    r"\bsed\s+(-\w*i|--in-place)|\btee\b|\brm\b|\bmv\b|\bcp\b|\bln\b|\btruncate\b"
    r"|\bchmod\b|\bgit\s+(rm|mv|checkout|restore)\b|\bpatch\b|\binstall\b"
)
REDIRECT = re.compile(r"(?<![0-9&])>>?\s*([^\s;&|]+)|[12]>>?\s*([^\s;&|]+)")


def rules(root: Path) -> tuple[list[str], list[str], str]:
    path = root / ".bros.toml"
    if not path.exists():
        return DEFAULT_ASK, [], " (defaults: no .bros.toml; run /bros:setup)"
    conf = tomllib.loads(path.read_text()).get("bouncer", {})
    return conf.get("ask", DEFAULT_ASK), conf.get("deny", []), ""


def relative(raw: str, root: Path) -> str | None:
    if not raw:
        return None
    path = Path(raw)
    path = (path if path.is_absolute() else root / path).resolve()
    try:
        return path.relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def targets(payload: dict, root: Path) -> list[str]:
    tool, args = payload.get("tool_name", ""), payload.get("tool_input") or {}
    if tool in FILE_TOOLS:
        return [p for p in [relative(str(args.get(FILE_TOOLS[tool], "")), root)] if p]
    if tool == "Bash":
        command = str(args.get("command", ""))
        words = [a or b for a, b in REDIRECT.findall(command)]
        if WRITES.search(command):
            try:
                words += shlex.split(command)
            except ValueError:
                words += command.split()
        return [p for w in words if (p := relative(w.strip("'\";&|<>"), root))]
    return []


def decide(payload: object, root: Path) -> tuple[str, str] | None:
    if not isinstance(payload, dict):
        return "deny", "Hook payload was not an object, so bouncer cannot clear this call."
    ask, deny, note = rules(root)
    for path in targets(payload, root):
        if any(fnmatch(path, g) for g in deny):
            return "deny", (f"`{path}` is protected (`[bouncer] deny` in .bros.toml){note}: "
                            "only a human edits it. Stop and tell the user what change you "
                            "wanted and why.")
        if any(fnmatch(path, g) for g in ask):
            return "ask", (f"`{path}` is protected (`[bouncer] ask`){note}: the user "
                           "approves this change themselves.")
    return None


def main() -> int:
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()
    try:
        verdict = decide(json.load(sys.stdin), root)
    except Exception as error:  # noqa: BLE001 - deny on error
        verdict = "deny", f"bouncer could not read its input or .bros.toml ({error})."
    if verdict:
        json.dump({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": verdict[0],
            "permissionDecisionReason": verdict[1],
        }}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
