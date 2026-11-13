"""bros ratchet <name> [--shrink]: lint findings may only shrink.

Each `[[ratchet]]` in `.bros.toml` runs a linter that prints JSON and compares its
findings, keyed `path::code` (no line numbers: they churn), to an allowlist file.
It fails both ways: a new or grown key is a regression, and a key that shrank is a
stale allowlist (`--shrink` rewrites it, and can only lower it). `--init` writes the
first allowlist from today's findings, and only when there is none. It also refuses
the cheap ways to fake progress: bare suppressions and rules switched off in config.

    [[ratchet]]
    name = "ruff"
    format = "ruff"                      # ruff | eslint
    dir = "backend"                      # run here; keys are relative to it
    run = "{python} -m ruff check --no-cache --output-format json ."
    requires = "ruff==0.16.4"            # optional: counts depend on the version
    allowlist = "backend/ruff-allowlist.json"
"""

import json
import re
import shlex
import subprocess
import sys
import tokenize
import tomllib
from collections import Counter
from importlib import metadata
from pathlib import Path

from bros import config as cfg

BARE = {
    "ruff": (re.compile(r"#\s*noqa(?!\s*:)", re.I), "*.py", "name the code: `# noqa: F401 - why`"),
    "eslint": (re.compile(r"eslint-disable(?:-next-line|-line)?\s*(?:\*/|$)"),
               "*.[jt]s*", "name the rule: `// eslint-disable-next-line no-console -- why`"),
}
RUFF_OFF = ("ignore", "extend-ignore", "per-file-ignores", "extend-per-file-ignores",
            "exclude", "extend-exclude")


class RatchetError(SystemExit):
    pass


def keys(findings, fmt: str, base: Path) -> Counter:
    def rel(name: str) -> str:
        path = Path(name)
        path = path if path.is_absolute() else base / path
        try:
            return path.resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            raise RatchetError(f"bros ratchet: {name} is outside {base}; set `dir` to "
                               "where the linter's paths are relative to")
    if not isinstance(findings, list):
        raise RatchetError(f"bros ratchet: {fmt} JSON was not a list")
    if fmt == "ruff":
        return Counter(f"{rel(f['filename'])}::{f.get('code') or 'syntax-error'}" for f in findings)
    if fmt == "eslint":
        return Counter(f"{rel(file['filePath'])}::{m.get('ruleId') or 'fatal'}"
                       for file in findings for m in file.get("messages", []))
    raise RatchetError(f"bros ratchet: unknown format `{fmt}` (ruff | eslint)")


def compare(actual: Counter, allowed: dict) -> tuple[list[str], list[str]]:
    regressed = [f"  {k}: {n} (allowed {allowed.get(k, 0)})"
                 for k, n in sorted(actual.items()) if n > allowed.get(k, 0)]
    stale = [f"  {k}: {actual.get(k, 0)} (allowlist says {n})"
             for k, n in sorted(allowed.items()) if actual.get(k, 0) < n]
    return regressed, stale


def laundering(fmt: str, base: Path, files: list[str]) -> list[str]:
    """Ways to make the number drop without fixing anything."""
    pattern, glob, fix = BARE[fmt]
    found = []
    for name in files:
        path = Path(name)
        if not path.match(glob):
            continue
        try:
            hits = comment_lines(base / path, fmt)
        except (OSError, SyntaxError, tokenize.TokenError):
            continue
        found += [f"  {name}:{i}: bare suppression; {fix}"
                  for i, text in hits if pattern.search(text)]
    if fmt == "ruff":
        for name in ("pyproject.toml", "ruff.toml", ".ruff.toml"):
            conf = base / name
            if not conf.exists():
                continue
            data = tomllib.loads(conf.read_text())
            ruff = data.get("tool", {}).get("ruff", {}) if name == "pyproject.toml" else data
            for table in (ruff, ruff.get("lint", {})):
                found += [f"  {name}: `{k}` switches rules or files off wholesale; "
                          "grandfather in the allowlist instead" for k in RUFF_OFF if k in table]
    return found


def comment_lines(path: Path, fmt: str) -> list[tuple[int, str]]:
    """Python comments come from the tokenizer, so a `# noqa` inside a string is not one."""
    if fmt != "ruff":
        return list(enumerate(path.read_text(errors="replace").splitlines(), 1))
    with path.open("rb") as source:
        return [(tok.start[0], tok.string) for tok in tokenize.tokenize(source.readline)
                if tok.type == tokenize.COMMENT]


def tracked(base: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=base, capture_output=True, text=True, check=True)
    return out.stdout.split()


def run_linter(spec: dict, base: Path) -> list:
    if spec.get("requires"):
        name, _, want = spec["requires"].partition("==")
        try:
            have = metadata.version(name)
        except metadata.PackageNotFoundError:
            have = None
        if have != want:
            raise RatchetError(
                f"bros ratchet: needs {spec['requires']} in bros's own Python, found "
                f"{have or 'nothing'}. Install it: uv tool install --force "
                f"git+https://github.com/pixieofhugs/bot-bros --with {spec['requires']}")
    command = spec["run"].replace("{python}", shlex.quote(sys.executable))
    done = subprocess.run(command, shell=True, cwd=base, capture_output=True, text=True)
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError:
        raise RatchetError(
            f"bros ratchet: `{spec['run']}` printed no JSON (exit {done.returncode}); a linter "
            f"that didn't run must not read as clean.\n{done.stderr.strip()[:400]}")


def main(args: list[str]) -> int:
    conf = cfg.load()
    root = cfg.repo_root()
    specs = {s.get("name"): s for s in cfg.require(conf, "ratchet")}
    names = [a for a in args if not a.startswith("-")] or list(specs)
    failed = False
    for name in names:
        if name not in specs:
            raise RatchetError(f"bros ratchet: no [[ratchet]] named `{name}` in .bros.toml")
        spec = specs[name]
        for key in ("format", "run", "allowlist"):
            cfg.require(spec, key)
        base = root / spec.get("dir", ".")
        allowlist = root / spec["allowlist"]
        actual = keys(run_linter(spec, base), spec["format"], base)
        if not allowlist.exists():
            if "--init" not in args:
                raise RatchetError(f"bros ratchet: no allowlist at {spec['allowlist']}. Baseline "
                                   f"today's findings with `bros ratchet {name} --init`.")
            allowlist.write_text(json.dumps(dict(sorted(actual.items())), indent=2) + "\n")
            print(f"{name}: baselined {sum(actual.values())} findings in {spec['allowlist']}")
        allowed = json.loads(allowlist.read_text())
        regressed, stale = compare(actual, allowed)
        cheats = laundering(spec["format"], base, tracked(base))
        if stale and "--shrink" in args:
            shrunk = {k: min(n, actual[k]) for k, n in sorted(allowed.items()) if actual.get(k)}
            allowlist.write_text(json.dumps(shrunk, indent=2) + "\n")
            print(f"{name}: allowlist shrunk by {sum(allowed.values()) - sum(shrunk.values())}")
            stale = []
        for title, lines in (
            ("new findings: fix them; the allowlist only shrinks", regressed),
            (f"fixed but still allowed: run `bros ratchet {name} --shrink`", stale),
            ("suppressed instead of fixed", cheats),
        ):
            if lines:
                print(f"{name}: {title}\n" + "\n".join(lines))
        bad = bool(regressed or stale or cheats)
        failed |= bad
        print(f"bros ratchet {name}: {'FAIL' if bad else 'ok'} "
              f"({sum(actual.values())} findings, {sum(allowed.values())} allowed)")
    return 1 if failed else 0
