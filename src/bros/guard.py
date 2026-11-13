"""bros guard <name>: run a bouncer hook outside Claude Code (another agent, a git hook).

Same file the plugin runs, so every harness shares one implementation. Reads the
hook payload on stdin and prints the hook's JSON decision, exactly like the plugin.
"""

import runpy
import sys
from pathlib import Path

NAMES = {"paths": "guard_paths.py", "issue-comments": "guard_issue_comments.py",
         "merge": "guard_merge.py"}


def hooks_dir() -> Path:
    packaged = Path(__file__).parent / "_hooks"
    return packaged if packaged.is_dir() else Path(__file__).parents[2] / "plugins/bouncer/hooks"


def main(args: list[str]) -> int:
    if not args or args[0] not in NAMES:
        print(f"usage: bros guard {{{','.join(NAMES)}}} < payload.json", file=sys.stderr)
        return 2
    path = hooks_dir() / NAMES[args[0]]
    if not path.exists():
        print(f"bros guard: {path} is missing; reinstall bros", file=sys.stderr)
        return 2
    sys.argv = [str(path)]
    try:
        runpy.run_path(str(path), run_name="__main__")
    except SystemExit as done:
        return int(done.code or 0)
    return 0
