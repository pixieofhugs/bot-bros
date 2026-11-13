"""Fail on a tracked file that leaks an email, a home path, or a denied word.
`deny_sha256` keeps denied words out of plain sight (not secret: a guess can confirm one)."""

import hashlib
import re
import subprocess
import sys

from bros import config as cfg

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}\b")
EMAIL_OK = re.compile(r"noreply@|@example\.|@users\.noreply\.github\.com|^git@github\.com")
HOME = re.compile(r"/(home|Users)/[a-z][\w-]*/")
TOKEN = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")
RUN, LONGEST = 3, 40  # names up to three words; prefixes catch plurals and longer forms


def normal(word: str) -> str:
    return "".join(TOKEN.findall(word)).lower()


def digest(word: str) -> str:
    return hashlib.sha256(normal(word).encode()).hexdigest()


def hashed_hits(text: str, hashes: set[str]) -> list[str]:
    hits = set()
    for number, line in enumerate(text.splitlines(), 1):
        tokens = [t.lower() for t in TOKEN.findall(line)]
        for i in range(len(tokens)):
            joined = "".join(tokens[i:i + RUN])[:LONGEST]
            if any(hashlib.sha256(joined[:end].encode()).hexdigest() in hashes
                   for end in range(3, len(joined) + 1)):
                hits.add(number)
    return [f"line {n}: a denied word" for n in sorted(hits)]


def leaks(text: str, deny: list[str], hashes: frozenset[str] = frozenset()) -> list[str]:
    found = [m for m in EMAIL.findall(text) if not EMAIL_OK.search(m)]
    found += [m.group() for m in HOME.finditer(text)]
    found += [w for w in deny if w.lower() in text.lower()]
    found += hashed_hits(text, hashes) if hashes else []
    return found


def main(args: list[str]) -> int:
    if args[:1] == ["--hash"]:
        if not args[1:]:
            print("usage: bros scrub --hash <word or name> ...", file=sys.stderr)
            return 2
        for word in args[1:]:
            print(digest(word))
        return 0
    conf = cfg.load()
    root = cfg.repo_root()
    scrub = conf.get("scrub", {})
    deny, hashes = scrub.get("deny", []), frozenset(h.lower() for h in scrub.get("deny_sha256", []))
    malformed = [h for h in hashes if not re.fullmatch(r"[0-9a-f]{64}", h)]
    if malformed:
        raise cfg.ConfigError(f"bros scrub: not SHA-256 hex in deny_sha256: {malformed}")
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
