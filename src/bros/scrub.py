"""Fail if any tracked file leaks an email, a home-directory path, or a `[scrub] deny` word."""

import re
import subprocess

from bros import config as cfg

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}\b")
EMAIL_OK = re.compile(r"noreply@|@example\.|@users\.noreply\.github\.com|^git@github\.com")
HOME = re.compile(r"/(home|Users)/[a-z][\w-]*/")


def leaks(text: str, deny: list[str]) -> list[str]:
    found = [m for m in EMAIL.findall(text) if not EMAIL_OK.search(m)]
    found += [m.group() for m in HOME.finditer(text)]
    found += [w for w in deny if w.lower() in text.lower()]
    return found


def main(args: list[str]) -> int:
    conf = cfg.load()
    root = cfg.repo_root()
    deny = conf.get("scrub", {}).get("deny", [])
    files = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, cwd=root, check=True
    ).stdout.split()
    bad = 0
    for name in files:
        if name == ".bros.toml":
            continue
        try:
            text = (root / name).read_text()
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for leak in leaks(text, deny):
            print(f"  {name}: {leak}")
            bad += 1
    print(f"bros scrub: {'FAIL' if bad else 'ok'}")
    return 1 if bad else 0
