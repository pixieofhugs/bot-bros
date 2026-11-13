"""bros audit: is this GitHub repo hardened the way the crew assumes?

Prints one line per setting: ✓ set, ✗ missing (with the exact command that fixes it),
? could not tell (with why). Exit 1 when anything is ✗. Changes nothing itself:
/bros:setup shows the list and runs the fixes the user approves.
"""

import json
import re
import shlex
import subprocess
from pathlib import Path

from bros import config as cfg

LABELS = {
    "needs-triage": ("ededed", "Not yet looked at"),
    "needs-info": ("d4c5f9", "Waiting on the reporter"),
    "ready-for-agent": ("0e8a16", "Researched; an agent can build it"),
    "ready-for-human": ("fbca04", "Needs a human decision or hands"),
    "wontfix": ("ffffff", "Will not be worked on"),
}
SHA = re.compile(r"@[0-9a-f]{40}\b")


class Unknown(Exception):
    pass


def gh(*args: str) -> str:
    out = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=30)
    if out.returncode:
        raise Unknown(out.stderr.strip().splitlines()[-1] if out.stderr.strip() else "gh failed")
    return out.stdout


def ghj(*args: str):
    return json.loads(gh(*args))


def pipe(payload: dict, *args: str) -> str:
    return f"echo {shlex.quote(json.dumps(payload))} | gh api {' '.join(args)} --input -"


def checks(root: Path, conf: dict) -> list[tuple[str, callable]]:
    meta = ghj("repo", "view", "--json", "nameWithOwner,defaultBranchRef")
    repo, branch = meta["nameWithOwner"], meta["defaultBranchRef"]["name"]
    info = ghj("api", f"repos/{repo}")

    def merge_settings():
        bad = {k: v for k, v in {
            "allow_squash_merge": True, "allow_merge_commit": False,
            "allow_rebase_merge": False, "delete_branch_on_merge": True,
            "allow_auto_merge": False,
        }.items() if info.get(k) != v}
        flags = " ".join(f"-F {k}={str(v).lower()}" for k, v in bad.items())
        return not bad, f"wrong: {', '.join(bad)}", f"gh api -X PATCH repos/{repo} {flags}"

    # Classic branch protection, not a ruleset: the merge guard reads
    # branches/<b>/protection/required_status_checks, which a ruleset does not fill.
    def protection():
        try:
            p = ghj("api", f"repos/{repo}/branches/{branch}/protection")
        except Unknown as err:
            if "Not Found" not in str(err) and "not protected" not in str(err):
                raise
            p = {}
        gaps = [name for name, ok in (
            ("required checks", (p.get("required_status_checks") or {}).get("contexts")),
            ("PRs only", "required_pull_request_reviews" in p),
            ("no force-push", p and not (p.get("allow_force_pushes") or {}).get("enabled")),
            ("no delete", p and not (p.get("allow_deletions") or {}).get("enabled")),
        ) if not ok]
        if not gaps:
            return True, "", ""
        names = sorted(set(gh("api", f"repos/{repo}/commits/{branch}/check-runs",
                              "--jq", ".check_runs[].name").split()))
        if not names:
            raise Unknown(f"missing {', '.join(gaps)}, and {branch} has no check runs yet to "
                          "require; push a CI workflow first")
        payload = {
            "required_status_checks": {"strict": True, "contexts": names},
            "enforce_admins": False,
            "required_pull_request_reviews": {"required_approving_review_count": 0},
            "restrictions": None, "allow_force_pushes": False, "allow_deletions": False,
        }
        return False, f"missing: {', '.join(gaps)}", \
            pipe(payload, "-X", "PUT", f"repos/{repo}/branches/{branch}/protection")

    def secret_scanning():
        sa = info.get("security_and_analysis")
        if not sa:
            raise Unknown("GitHub did not report secret scanning (not available on this plan?)")
        off = [k for k in ("secret_scanning", "secret_scanning_push_protection")
               if (sa.get(k) or {}).get("status") != "enabled"]
        payload = {"security_and_analysis": {k: {"status": "enabled"} for k in off}}
        return not off, f"off: {', '.join(off)}", pipe(payload, "-X", "PATCH", f"repos/{repo}")

    def token():
        perms = ghj("api", f"repos/{repo}/actions/permissions/workflow")
        return (perms.get("default_workflow_permissions") == "read",
                "default GITHUB_TOKEN can write",
                f"gh api -X PUT repos/{repo}/actions/permissions/workflow "
                "-f default_workflow_permissions=read")

    def codeowners():
        found = any((root / p).exists() for p in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"))
        return found, "no CODEOWNERS", "/bros:setup writes .github/CODEOWNERS for the protected paths"

    def dependabot():
        path = root / ".github/dependabot.yml"
        ok = path.exists() and bool(re.search(
            r"^\s*-?\s*package-ecosystem:\s*[\"']?github-actions", path.read_text(), re.M))
        return ok, "Dependabot does not bump Actions, so new Bot Bros rules never arrive", \
            "/bros:setup adds a github-actions entry to .github/dependabot.yml"

    def pinned():
        loose = []
        for wf in sorted((root / ".github/workflows").glob("*.y*ml")):
            for n, line in enumerate(wf.read_text().splitlines(), 1):
                m = re.match(r"\s*(?:-\s*)?uses:\s*[\"']?([^\s#\"']+)", line)
                if m and not m.group(1).startswith(("./", "docker://")) and not SHA.search(m.group(1)):
                    loose.append(f"{wf.name}:{n} {m.group(1)}")
        return not loose, "not pinned by SHA: " + "; ".join(loose[:5]) + (" …" if len(loose) > 5 else ""), \
            "/bros:setup rewrites each to `owner/action@<sha> # <tag>` (Dependabot keeps them current)"

    def labels():
        want = conf.get("labels", LABELS)
        have = set(gh("label", "list", "-R", repo, "--limit", "200", "--json", "name",
                      "--jq", ".[].name").split("\n"))
        missing = [n for n in want if n not in have]
        fix = " && ".join(
            f"gh label create {shlex.quote(n)} -R {repo} --color {LABELS.get(n, ('ededed',))[0]}"
            for n in missing)
        return not missing, f"missing: {', '.join(missing)}", fix

    return [
        ("squash-only merges, branches auto-delete, auto-merge off", merge_settings),
        (f"{branch} protected: PRs only, required checks, no force-push or delete", protection),
        ("secret scanning + push protection", secret_scanning),
        ("workflow token read-only by default", token),
        ("CODEOWNERS", codeowners),
        ("Dependabot bumps Actions", dependabot),
        ("Actions pinned by commit SHA", pinned),
        ("triage labels", labels),
    ]


def main(args: list[str]) -> int:
    root = cfg.repo_root()
    try:
        conf = cfg.load(root).get("audit", {})
    except SystemExit as err:
        print(f"? {err} (auditing with defaults)")
        conf = {}
    try:
        items = checks(root, conf)
    except Unknown as err:
        print(f"bros audit: cannot reach this repo on GitHub ({err}). Is gh logged in?")
        return 1
    failed = 0
    for name, check in items:
        try:
            ok, why, fix = check()
        except Unknown as err:
            print(f"? {name} — {err}")
            continue
        if ok:
            print(f"✓ {name}")
        else:
            failed += 1
            print(f"✗ {name} — {why}\n    fix: {fix}")
    print(f"bros audit: {'ok' if not failed else f'{failed} to fix'}")
    return 1 if failed else 0
