"""bros board: the open `ready-for-agent` issues as one table.

One GraphQL query (REST lacks `blockedBy` / `closedByPullRequestsReferences`). Comments
pass `bros issue`'s collaborator filter before any planner stamp is read, and the
collaborator lookup runs first, so a failed lookup prints nothing.

    bros board [--repo OWNER/NAME]
"""

import argparse
import json
import re
import sys

from bros import issue

# ponytail: first 100 issues, last 20 comments each; page if the board outgrows it.
QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    issues(first: 100, states: OPEN, labels: ["ready-for-agent"]) {
      nodes {
        number title body
        blockedBy(first: 20) { nodes { number state } }
        closedByPullRequestsReferences(first: 5, includeClosedPrs: false) {
          nodes { number isDraft }
        }
        comments(last: 20) { nodes { author { login } createdAt body } }
      }
    }
  }
}
"""

STAMP = re.compile(r"\*\*Verified-at:\*\*\s*`?([0-9a-f]{7,40})")
PREMISE = re.compile(r"\*\*Premise:\*\*\s*(.+)")


def latest_stamp(comments: list[dict]) -> str:
    """`sha — premise` from the newest comment carrying a Verified-at, else `-`."""
    for comment in reversed(comments):
        body = comment.get("body") or ""
        if sha := STAMP.search(body):
            premise = PREMISE.search(body)
            return f"{sha[1][:9]} — {premise[1][:70]}" if premise else sha[1][:9]
    return "-"


def build_rows(issues: list[dict], collaborators: frozenset[str]) -> list[dict]:
    rows = []
    for node in issues:
        kept, _ = issue.filter_comments(node["comments"]["nodes"], collaborators)
        blockers = [b["number"] for b in node["blockedBy"]["nodes"] if b["state"] == "OPEN"]
        prs = node["closedByPullRequestsReferences"]["nodes"]
        rows.append({
            "number": node["number"],
            "title": node["title"],
            "blocked_by": ",".join(f"#{n}" for n in blockers) or "-",
            "pr": ",".join(f"#{p['number']}{' (draft)' if p['isDraft'] else ''}" for p in prs)
            or "-",
            "size": len(node.get("body") or ""),
            "stamp": latest_stamp(kept),
        })
    return rows


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def render(rows: list[dict]) -> str:
    head = "| issue | blocked_by | in flight | size | planner stamp |\n|---|---|---|---|---|"
    lines = [
        f"| #{r['number']} {_cell(r['title'][:60])} | {r['blocked_by']} | {r['pr']} | {r['size']} "
        f"| {_cell(r['stamp'])} |"
        for r in rows
    ]
    return "\n".join([head, *lines]) if lines else "No open ready-for-agent issues."


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bros board", description=__doc__.splitlines()[0])
    parser.add_argument("--repo", help="OWNER/NAME (default: this checkout's remote)")
    args = parser.parse_args(argv)
    if args.repo and args.repo.count("/") != 1:
        parser.error(f"--repo wants OWNER/NAME, got {args.repo!r}")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        repo = args.repo or issue._gh(
            ["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"]
        ).strip()
        collaborators = issue.collaborators_from(
            issue._gh(["api", "--paginate", f"repos/{repo}/collaborators", "--jq", ".[].login"])
        )
        owner, name = repo.split("/")
        raw = issue._gh(
            ["api", "graphql", "-f", f"query={QUERY}", "-f", f"owner={owner}", "-f", f"name={name}"]
        )
    except issue.LookupError_ as error:
        print(f"REFUSING TO PRINT THE BOARD: {error}", file=sys.stderr)
        return 1
    nodes = json.loads(raw)["data"]["repository"]["issues"]["nodes"]
    print(render(build_rows(nodes, collaborators)))
    return 0
