"""End to end on tests/fixture: the second project that keeps the crew generic."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixture"
SRC = Path(__file__).parent.parent / "src"


def sh(repo, *cmd):
    return subprocess.run(cmd, cwd=repo, capture_output=True, text=True, check=True)


def bros(repo, *args):
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    return subprocess.run(
        [sys.executable, "-c", "from bros.cli import main; main()", *args],
        cwd=repo, capture_output=True, text=True, env=env,
    )


@pytest.fixture
def repo(tmp_path):
    shutil.copytree(FIXTURE, tmp_path, dirs_exist_ok=True)
    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "add", "."],
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "commit", "-qm", "init"],
        ["git", "checkout", "-qb", "feature"],
    ):
        sh(tmp_path, *cmd)
    return tmp_path


def commit(repo, path, text):
    (repo / path).write_text(text)
    sh(repo, "git", "add", ".")
    sh(repo, "git", "-c", "user.name=t", "-c", "user.email=t@example.com",
       "commit", "-qm", "change")


def test_narrated_change_fails_the_comment_budget(repo):
    commit(repo, "api/app.py", "# says what it does\n" * 6 + "x = 1\n")
    result = bros(repo, "comments")
    assert result.returncode == 1 and "api/app.py" in result.stdout


def test_lean_change_and_skipped_paths_pass(repo):
    (repo / "api/generated").mkdir()
    commit(repo, "api/generated/client.py", "# generated\n" * 40)
    commit(repo, "api/app.py", "def greet(n):\n    return n\n")
    assert bros(repo, "comments").returncode == 0


def test_check_runs_only_what_the_change_touches(repo):
    commit(repo, "web/src/app.ts", "export const a = 1;\n")
    result = bros(repo, "check")
    assert result.returncode == 0
    assert "--- web" in result.stdout and "--- api" not in result.stdout
    assert result.stdout.strip().endswith("PASS -- web")


def test_check_all_and_a_failing_check(repo):
    (repo / "web/src/app.ts").unlink()
    commit(repo, "api/app.py", "x = 1\n")
    result = bros(repo, "check", "--all")
    assert result.returncode == 1
    assert "FAIL -- web; passed: api" in result.stdout


def test_missing_config_fails_loudly(repo):
    (repo / ".bros.toml").unlink()
    result = bros(repo, "check")
    assert result.returncode != 0 and "run /bros:setup" in result.stderr


def test_scrub_catches_a_leak(repo):
    email, home = "someone" + "@gmail.com", "/" + "home/someone/"
    commit(repo, "api/app.py", f"OWNER = '{email}'\nP = '{home}x'\n")
    result = bros(repo, "scrub")
    assert result.returncode == 1
    assert email in result.stdout and home in result.stdout


def test_scrub_refuses_hashed_words_without_naming_them(repo):
    from bros.scrub import digest
    (repo / ".bros.toml").write_text(
        (repo / ".bros.toml").read_text() + f'\n[scrub]\ndeny_sha256 = ["{digest("somename")}"]\n')
    for text in ("by Some Name", "class SomeName:", "x = 'somename'"):
        commit(repo, "api/app.py", text + "\n")
        result = bros(repo, "scrub")
        assert result.returncode == 1 and "a denied word (so…)" in result.stdout
        assert "somename" not in result.stdout.lower()
    commit(repo, "api/app.py", "some other name\n")
    assert bros(repo, "scrub").returncode == 0
