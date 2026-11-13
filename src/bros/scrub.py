"""Fail on a tracked file that leaks an email, a home path, or a denied word.
`deny_sha256` hashes words a public repo must refuse without naming (`--hash <word>`)."""

import hashlib
import re
import subprocess

from bros import config as cfg

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}\b")
EMAIL_OK = re.compile(r"noreply@|@example\.|@users\.noreply\.github\.com|^git@github\.com")
HOME = re.compile(r"/(home|Users)/[a-z][\w-]*/")
TOKEN = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")


def digest(word: str) -> str:
    return hashlib.sha256(word.lower().encode()).hexdigest()


def hashed_hits(text: str, hashes: set[str]) -> list[str]:
    tokens = [t.lower() for t in TOKEN.findall(text)]
    candidates = set(tokens) | {a + b for a, b in zip(tokens, tokens[1:])}
    return sorted(f"a denied word ({c[:2]}…)" for c in candidates if digest(c) in hashes)


def leaks(text: str, deny: list[str], hashes: set[str] = frozenset()) -> list[str]:
    found = [m for m in EMAIL.findall(text) if not EMAIL_OK.search(m)]
    found += [m.group() for m in HOME.finditer(text)]
    found += [w for w in deny if w.lower() in text.lower()]
    found += hashed_hits(text, hashes) if hashes else []
    return found


def main(args: list[str]) -> int:
    if args[:1] == ["--hash"]:
        for word in args[1:]:
            print(digest(word))
        return 0
    conf = cfg.load()
    root = cfg.repo_root()
    scrub = conf.get("scrub", {})
    deny, hashes = scrub.get("deny", []), set(scrub.get("deny_sha256", []))
    files = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, text=True, cwd=root, check=True
    ).stdout.split("\0")
    bad = 0
    for name in filter(None, files):
        try:
            text = (root / name).read_text()
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for leak in leaks(text, [] if name == ".bros.toml" else deny, hashes):
            print(f"  {name}: {leak}")
            bad += 1
    print(f"bros scrub: {'FAIL' if bad else 'ok'}")
    return 1 if bad else 0
