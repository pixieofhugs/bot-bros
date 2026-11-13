"""bros ratchet <name> [--shrink|--init]: lint findings may only shrink.

Each `[[ratchet]]` in `.bros.toml` runs a linter that prints JSON and compares its
findings, keyed `path::code` (no line numbers: they churn), to an allowlist file.
It fails both ways: a new or grown key is a regression, and a key that shrank is a
stale allowlist (`--shrink` rewrites it, and can only lower it). The allowlist itself
may not grow against `base`, so raising it by hand fails too. `--init` writes the
first allowlist from today's findings, only when there is none here or at `base`.
It also refuses the cheap ways to fake progress: suppressions with no rule named,
and linter config that switches rules or files off.

    [[ratchet]]
    name = "ruff"
    format = "ruff"                      # ruff | eslint
    dir = "backend"                      # run here; keys are relative to it
    run = "{python} -m ruff check --no-cache --output-format json ."
    requires = "ruff==0.16.4"            # optional: counts depend on the version
    select = ["E", "F", "I"]             # ruff: narrowing the rules is not progress
    line_length = 88                     # ruff: required if the config sets one
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

# Ruff reads a file-wide exemption (ruff or flake8 prefix, then noqa) only at the start of
# a comment, but a code-less line noqa anywhere in one. Both silence rules wholesale.
RUFF_SUPPRESS = re.compile(r"^#\s*(?:ruff|flake8)\s*:\s*noqa|#\s*noqa(?!\s*:)", re.I)
# A disable with no rule listed (a `-- why` description is not a rule), or an inline
# `/* eslint rule: off */` that switches a rule off for the file.
ESLINT_SUPPRESS = re.compile(
    r"eslint-disable(?:-next-line|-line)?(?=\s*(?:--|\*/|$))"
    r"|/\*\s*eslint\s+[^*]*?:\s*[\"']?(?:off|0)\b"
)
SOURCES = {
    "ruff": ((".py", ".pyi"), RUFF_SUPPRESS, "name the code: `# noqa: F401 - why`"),
    "eslint": ((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"),
               ESLINT_SUPPRESS, "name the rule: `// eslint-disable-next-line no-console -- why`"),
}
RUFF_OFF = ("ignore", "extend-ignore", "per-file-ignores", "extend-per-file-ignores",
            "exclude", "extend-exclude", "include", "extend")
RUFF_CONFIGS = ("pyproject.toml", "ruff.toml", ".ruff.toml")
# ponytail: select and line length are pinned; per-rule limits (mccabe, pylint max-*)
# are not. Pin them the same way when a ratchet selects rules that read them.


class RatchetError(SystemExit):
    pass


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)


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
        return Counter(f"{rel(f['filename'])}::{f.get('code') or 'syntax-error'}"
                       for f in findings)
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


def grown(allowed: dict, at_base: dict | None) -> list[str]:
    """The allowlist may only shrink against base: raising it by hand is not a fix."""
    if at_base is None:
        return []
    return [f"  {k}: {n} (base allows {at_base.get(k, 0)})"
            for k, n in sorted(allowed.items()) if n > at_base.get(k, 0)]


def tracked(root: Path, base: Path) -> list[str]:
    out = git(base, "ls-files", "-z")
    if out.returncode:
        raise RatchetError(f"bros ratchet: git ls-files failed in {base}: {out.stderr.strip()}")
    return [name for name in out.stdout.split("\0") if name]


def comment_lines(path: Path, fmt: str) -> list[tuple[int, str]]:
    """Python comments come from the tokenizer, so a `# noqa` inside a string is not one."""
    if fmt != "ruff":
        return list(enumerate(path.read_text(errors="replace").splitlines(), 1))
    with path.open("rb") as source:
        return [(tok.start[0], tok.string) for tok in tokenize.tokenize(source.readline)
                if tok.type == tokenize.COMMENT]


def ruff_tables(root: Path, base: Path, files: list[str]) -> list[tuple[str, dict]]:
    """Every ruff config that can apply: nested under `dir`, and its parents up to root."""
    paths = [base / f for f in files if Path(f).name in RUFF_CONFIGS]
    for parent in [base, *base.parents]:
        paths += [parent / n for n in RUFF_CONFIGS if parent != base and (parent / n).exists()]
        if parent == root:
            break
    tables = []
    for path in paths:
        data = tomllib.loads(path.read_text())
        ruff = data.get("tool", {}).get("ruff") if path.name == "pyproject.toml" else data
        if ruff is not None:
            tables.append((path.relative_to(root).as_posix(), ruff))
    return tables


def laundering(spec: dict, root: Path, base: Path, files: list[str]) -> list[str]:
    """Ways to make the number drop without fixing anything."""
    fmt = spec["format"]
    suffixes, pattern, fix = SOURCES[fmt]
    found = []
    for name in files:
        if not name.endswith(suffixes):
            continue
        try:
            hits = comment_lines(base / name, fmt)
        except (OSError, SyntaxError, tokenize.TokenError) as err:
            found.append(f"  {name}: could not scan for suppressions ({err})")
            continue
        found += [f"  {name}:{i}: suppression with no rule named; {fix}"
                  for i, text in hits if pattern.search(text)]
    if fmt != "ruff":
        return found
    tables = ruff_tables(root, base, files)
    if spec.get("select") is not None and not tables:
        found.append(f"  no ruff config found, so ruff runs its defaults, not {spec['select']}")
    for name, ruff in tables:
        lint = ruff.get("lint", {})
        for table in (ruff, lint):
            found += [f"  {name}: `{k}` changes what ruff checks wholesale; grandfather "
                      "in the allowlist instead" for k in RUFF_OFF if k in table]
        have = lint.get("select", ruff.get("select"))
        if spec.get("select") is not None and have != spec["select"]:
            found.append(f"  {name}: ruff selects {have}, .bros.toml pins {spec['select']}; "
                         "change both in one commit so the reviewer sees the rules move")
        length = ruff.get("line-length", lint.get("pycodestyle", {}).get("max-line-length"))
        if length is not None and length != spec.get("line_length"):
            found.append(f"  {name}: line length {length} is not pinned in .bros.toml "
                         f"(line_length = {spec.get('line_length')}); raising it hides E501s")
    return found


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
    # Linters exit 0 (clean) or 1 (findings). Anything else is the linter failing.
    if done.returncode not in (0, 1):
        raise RatchetError(f"bros ratchet: `{spec['run']}` exited {done.returncode}; a linter "
                           f"that crashed must not read as clean.\n{done.stderr.strip()[:400]}")
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError:
        raise RatchetError(
            f"bros ratchet: `{spec['run']}` printed no JSON (exit {done.returncode}); a linter "
            f"that didn't run must not read as clean.\n{done.stderr.strip()[:400]}")


def at_base(root: Path, ref: str, path: str) -> dict | None:
    if git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode:
        raise RatchetError(f"bros ratchet: base `{ref}` is not in this clone, so the "
                           "allowlist can't be checked against it. Fetch it (CI: "
                           "actions/checkout with fetch-depth: 0).")
    shown = git(root, "show", f"{ref}:{path}")
    return json.loads(shown.stdout) if shown.returncode == 0 else None


def main(args: list[str]) -> int:
    conf = cfg.load()
    root = cfg.repo_root()
    ref = conf.get("base", "origin/main")
    specs = {s.get("name"): s for s in cfg.require(conf, "ratchet")}
    names = [a for a in args if not a.startswith("-")] or list(specs)
    failed = False
    for name in names:
        if name not in specs:
            raise RatchetError(f"bros ratchet: no [[ratchet]] named `{name}` in .bros.toml")
        spec = specs[name]
        for key in ("format", "run", "allowlist"):
            cfg.require(spec, key)
        if spec["format"] not in SOURCES:
            raise RatchetError(f"bros ratchet: unknown format `{spec['format']}` (ruff | eslint)")
        base = root / spec.get("dir", ".")
        allowlist = root / spec["allowlist"]
        before = at_base(root, ref, spec["allowlist"])
        actual = keys(run_linter(spec, base), spec["format"], base)
        if not allowlist.exists():
            if before is not None:
                raise RatchetError(f"bros ratchet: {spec['allowlist']} exists at {ref} but was "
                                   "deleted here; restore it. Re-baselining is not a fix.")
            if "--init" not in args:
                raise RatchetError(f"bros ratchet: no allowlist at {spec['allowlist']}. Baseline "
                                   f"today's findings with `bros ratchet {name} --init`.")
            allowlist.write_text(json.dumps(dict(sorted(actual.items())), indent=2) + "\n")
            print(f"{name}: baselined {sum(actual.values())} findings in {spec['allowlist']}")
        allowed = json.loads(allowlist.read_text())
        if not actual and allowed:
            raise RatchetError(f"bros ratchet: `{spec['run']}` found nothing, but the allowlist "
                               "has findings. Did it lint any files? Check `dir` and `run`.")
        regressed, stale = compare(actual, allowed)
        cheats = laundering(spec, root, base, tracked(root, base))
        if stale and "--shrink" in args:
            shrunk = {k: min(n, actual[k]) for k, n in sorted(allowed.items()) if actual.get(k)}
            allowlist.write_text(json.dumps(shrunk, indent=2) + "\n")
            print(f"{name}: allowlist shrunk by {sum(allowed.values()) - sum(shrunk.values())}")
            allowed, stale = shrunk, []
        bad = False
        for title, lines in (
            ("new findings: fix them; the allowlist only shrinks", regressed),
            (f"fixed but still allowed: run `bros ratchet {name} --shrink`", stale),
            (f"allowlist raised against {ref}: fix the code instead", grown(allowed, before)),
            ("suppressed instead of fixed", cheats),
        ):
            if lines:
                bad = True
                print(f"{name}: {title}\n" + "\n".join(lines))
        failed |= bad
        print(f"bros ratchet {name}: {'FAIL' if bad else 'ok'} "
              f"({sum(actual.values())} findings, {sum(allowed.values())} allowed)")
    return 1 if failed else 0
