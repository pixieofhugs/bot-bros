"""bros issue <N>: an issue with only the comments a repo collaborator wrote.

On a public repo a stranger's comment is a prompt injection for any bot that acts on
issues. Filtering here, outside the model, means their text never reaches context (only
a count). The body prints unfiltered: issues reach a bot through a label, and labels
need write access. Fails closed: collaborators are fetched before the issue, so a failed
lookup prints nothing.

    bros issue <N> [--repo OWNER/NAME] [--tail N] [--since ISO]
"""

import argparse
import json
import subprocess
import sys

from bros import config as cfg

ISSUE_FIELDS = "number,title,state,url,author,labels,body,comments"


class LookupError_(RuntimeError):
    pass


def _gh(args: list[str]) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise LookupError_(
            f"`gh {' '.join(args)}` failed ({result.returncode}): "
            f"{result.stderr.strip() or 'no stderr'}"
        )
    return result.stdout


def collaborators_from(raw: str) -> frozenset[str]:
    """Empty is a failure: it would otherwise read as "nobody commented"."""
    logins = frozenset(line.strip().casefold() for line in raw.splitlines() if line.strip())
    if not logins:
        raise LookupError_("the collaborator lookup returned nobody, which cannot be right")
    return logins


def filter_comments(comments: list[dict], collaborators: frozenset[str]) -> tuple[list[dict], int]:
    """`(collaborator comments, withheld count)`; `author: null` is withheld."""
    kept = [
        c for c in comments
        if ((c.get("author") or {}).get("login") or "").casefold() in collaborators
    ]
    return kept, len(comments) - len(kept)


def narrow(kept: list[dict], tail: int | None = None, since: str | None = None) -> list[dict]:
    if since:  # UTC ISO-8601: string order is time order
        kept = [c for c in kept if (c.get("createdAt") or "") >= since]
    return kept[-tail:] if tail else kept


def render(issue: dict, kept: list[dict], withheld: int) -> str:
    labels = ", ".join(label["name"] for label in issue.get("labels") or [])
    author = (issue.get("author") or {}).get("login", "unknown")
    lines = [
        f"#{issue.get('number')}  {issue.get('title')}",
        f"state: {issue.get('state')}  author: {author}"
        + (f"  labels: {labels}" if labels else ""),
        "",
        (issue.get("body") or "").strip(),
        "",
        "--",
        "",
    ]
    if kept:
        lines.append(f"{len(kept)} comment(s) from collaborators:")
        for c in kept:
            login = (c.get("author") or {}).get("login", "unknown")
            lines += ["", f"@{login} ({c.get('createdAt', '')}):", (c.get("body") or "").strip()]
    else:
        lines.append("No comments from collaborators.")
    if withheld:
        noun = "comment" if withheld == 1 else "comments"
        lines += [
            "",
            f"({withheld} {noun} from non-collaborators withheld)",
            "Their text was dropped before it reached you and cannot be retrieved through "
            "this tool. If one of them matters, a collaborator has to read it and say so.",
        ]
    return "\n".join(lines)


def guard_enabled() -> bool:
    """Check the hook that makes this the only path is on, rather than assume it."""
    try:
        settings = json.loads((cfg.repo_root() / ".claude" / "settings.json").read_text())
    except (OSError, json.JSONDecodeError, SystemExit):
        return False
    plugins = settings.get("enabledPlugins") or {}
    return any(k.startswith("bouncer@") and on for k, on in plugins.items())


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bros issue", description=__doc__.splitlines()[0])
    parser.add_argument("number")
    parser.add_argument("--repo", help="OWNER/NAME (default: this checkout's remote)")
    parser.add_argument("--tail", type=int, metavar="N")
    parser.add_argument("--since", metavar="ISO")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        repo = args.repo or _gh(
            ["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"]
        ).strip()
        collaborators = collaborators_from(
            _gh(["api", "--paginate", f"repos/{repo}/collaborators", "--jq", ".[].login"])
        )
        issue = json.loads(
            _gh(["issue", "view", args.number, "--repo", repo, "--json", ISSUE_FIELDS])
        )
    except LookupError_ as error:
        print(f"REFUSING TO PRINT COMMENTS: {error}\nWithout the collaborator set no "
              "comment can be shown to be trustworthy, so this fails closed.", file=sys.stderr)
        return 1
    kept, withheld = filter_comments(issue.get("comments") or [], collaborators)
    if not guard_enabled():
        print("WARNING: the bouncer plugin is not enabled in .claude/settings.json, so a raw "
              "comment read is not blocked and this filter is advisory. Run /bros:setup.",
              file=sys.stderr)
    print(render(issue, narrow(kept, args.tail, args.since), withheld))
    return 0
