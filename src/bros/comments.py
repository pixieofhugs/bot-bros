"""Comment budget: a file may add at most max(floor, ratio * code) comment lines.

Docstrings count as comments; `ponytail:` comments and lint pragmas are free.
Tunables live under `[comments]` in `.bros.toml`: floor, ratio, skip.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

from bros import config as cfg

PY = {".py"}
C_LIKE = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css"}
FREE = re.compile(
    r"ponytail:|noqa|type: ?ignore|pragma|eslint-|@ts-|prettier-ignore"
    r"|istanbul|c8 |stylelint-|^#!"
)
ADVICE = (
    "Keep a comment only for a why or a hazard the code cannot say. Delete "
    "comments that narrate what the code does or what changed (the commit says that)."
)


class Budget:
    def __init__(self, settings: dict | None = None):
        settings = settings or {}
        self.floor = settings.get("floor", 4)
        self.ratio = settings.get("ratio", 0.2)
        self.skip = ("/node_modules/", *settings.get("skip", ()))

    def allowed(self, code: int) -> int:
        return max(self.floor, int(self.ratio * code))

    def checked(self, path: str) -> bool:
        return Path(path).suffix in PY | C_LIKE and not any(
            s in f"/{path}" for s in self.skip
        )


def classify(text: str, suffix: str) -> list[str]:
    """Per line: 'c' comment, 'x' code, '' blank or free."""
    kinds, block, block_kind = [], None, "c"
    for raw in text.splitlines():
        line = raw.strip()
        if block:
            free = not line or (block_kind == "c" and FREE.search(line))
            kinds.append("" if free else block_kind)
            if block in line:
                block = None
            continue
        if not line:
            kinds.append("")
            continue
        kind = "x"
        if suffix in PY:
            docstring = re.match(r'[rRbBuU]?("""|\'\'\')', line)
            if line.startswith("#"):
                kind = "c"
            elif docstring:
                kind = "c"
                if line.count(docstring.group(1)) == 1:
                    block, block_kind = docstring.group(1), "c"
            else:
                for quote in ('"""', "'''"):
                    if line.count(quote) % 2:
                        block, block_kind = quote, "x"
        elif suffix in C_LIKE:
            if line.startswith(("//", "*")):
                kind = "c"
            elif line.startswith(("/*", "{/*")):
                kind = "c"
                if "*/" not in line:
                    block, block_kind = "*/", "c"
        kinds.append("" if kind == "c" and FREE.search(line) else kind)
    return kinds


def tally(kinds: list[str]) -> tuple[int, int]:
    return kinds.count("c"), kinds.count("x")


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout


def changed_lines(diff: str) -> tuple[set[int], set[int]]:
    removed, added = set(), set()
    hunk = r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@"
    for match in re.finditer(hunk, diff, re.M):
        old_start, old_len, new_start, new_len = match.groups()
        removed.update(range(int(old_start), int(old_start) + int(old_len or 1)))
        added.update(range(int(new_start), int(new_start) + int(new_len or 1)))
    return removed, added


def lint(base: str, budget: Budget) -> int:
    merge_base = git("merge-base", base, "HEAD").strip()
    failures = []
    for path in git("diff", "--name-only", "--diff-filter=AM", merge_base).split():
        if not budget.checked(path) or not Path(path).exists():
            continue
        suffix = Path(path).suffix
        removed, added = changed_lines(git("diff", "-U0", merge_base, "--", path))
        new = classify(Path(path).read_text(errors="replace"), suffix)
        old = classify(git("show", f"{merge_base}:{path}"), suffix) if removed else []
        comments, code = tally([k for i, k in enumerate(new, 1) if i in added])
        comments -= tally([k for i, k in enumerate(old, 1) if i in removed])[0]
        if comments > budget.allowed(code):
            failures.append(
                f"  {path}: +{comments} comment lines for +{code} code "
                f"(budget {budget.allowed(code)})"
            )
    if failures:
        print("comment budget exceeded:\n" + "\n".join(failures) + "\n" + ADVICE)
        return 1
    print("comment budget: ok")
    return 0


def hook(budget: Budget) -> int:
    try:
        tool_input = json.load(sys.stdin)["tool_input"]
        path = tool_input["file_path"]
    except Exception:
        return 0  # a nudge, not a security control: unreadable input passes
    if not budget.checked(path):
        return 0
    suffix = Path(path).suffix
    comments = code = 0
    for edit in tool_input.get("edits") or [tool_input]:
        new = tally(classify(edit.get("new_string", edit.get("content", "")), suffix))
        old = tally(classify(edit.get("old_string", ""), suffix))
        comments += new[0] - old[0]
        code += max(new[1] - old[1], 0)
    if comments > budget.allowed(code):
        print(
            f"This edit adds {comments} comment lines for {code} code lines "
            f"(budget {budget.allowed(code)}). {ADVICE}",
            file=sys.stderr,
        )
        return 2
    return 0


def main(args: list[str]) -> int:
    if "--hook" in args:
        try:
            settings = cfg.load().get("comments")
        except SystemExit:
            settings = None
        return hook(Budget(settings))
    conf = cfg.load()
    base = args[0] if args else conf.get("base", "origin/main")
    return lint(base, Budget(conf.get("comments")))
