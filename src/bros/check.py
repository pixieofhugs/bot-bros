"""Run the `[[check]]` commands whose `paths` globs match files changed since base.

The last line is a one-line verdict an agent can quote in its hand-back.
"""

import subprocess
from fnmatch import fnmatch

from bros import config as cfg


def changed(base: str) -> list[str]:
    merge_base = subprocess.run(
        ["git", "merge-base", base, "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    out = subprocess.run(
        ["git", "diff", "--name-only", merge_base],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.split()


def main(args: list[str]) -> int:
    conf = cfg.load()
    root = cfg.repo_root()
    checks = cfg.require(conf, "check")
    files = None if "--all" in args else changed(conf.get("base", "origin/main"))
    passed, failed = [], []
    for check in checks:
        name, run = cfg.require(check, "name"), cfg.require(check, "run")
        globs = check.get("paths", ["*"])
        if files is not None and not any(fnmatch(f, g) for f in files for g in globs):
            continue
        print(f"--- {name}: {run}", flush=True)
        ok = subprocess.run(run, shell=True, cwd=root).returncode == 0
        (passed if ok else failed).append(name)
    if failed:
        print(f"bros check: FAIL -- {' '.join(failed)}; passed: {' '.join(passed) or 'none'}")
        return 1
    print(f"bros check: PASS -- {' '.join(passed) or 'nothing matched the changes'}")
    return 0
