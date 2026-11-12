"""bros <command> [args]"""

import sys

from bros import check, comments, prbody, scrub

COMMANDS = {
    "check": check.main,
    "comments": comments.main,
    "pr-body": prbody.main,
    "scrub": scrub.main,
}


def main() -> None:
    name, *args = sys.argv[1:] or ["--help"]
    if name not in COMMANDS:
        print(f"usage: bros {{{','.join(COMMANDS)}}} [args]", file=sys.stderr)
        sys.exit(0 if name in ("-h", "--help") else 2)
    sys.exit(COMMANDS[name](args))
