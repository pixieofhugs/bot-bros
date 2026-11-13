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


@pytest.mark.parametrize("text", [
    "by Some Name", "class SomeName:", "x = 'somename'", "SOME_NAME = 1", "some-name",
    "somenames", "SomeNamePlayground", "SOMENAMEs", "the Some Name's",
])
def test_scrub_refuses_hashed_words_without_naming_them(repo, text):
    from bros.scrub import digest
    assert digest("Some Name") == digest("somename") == digest("SOME_NAME")
    (repo / ".bros.toml").write_text(
        (repo / ".bros.toml").read_text() + f'\n[scrub]\ndeny_sha256 = ["{digest("Some Name")}"]\n')
    commit(repo, "api/app.py", "ok = 1\n" + text + "\n")
    result = bros(repo, "scrub")
    assert result.returncode == 1 and "line 2: a denied word" in result.stdout
    assert "some" not in result.stdout.lower()


def test_scrub_passes_near_misses_and_rejects_bad_hashes(repo):
    from bros.scrub import digest
    (repo / ".bros.toml").write_text(
        (repo / ".bros.toml").read_text() + f'\n[scrub]\ndeny_sha256 = ["{digest("somename")}"]\n')
    commit(repo, "api/app.py", "some other name\nsome\nname\n")
    assert bros(repo, "scrub").returncode == 0
    (repo / ".bros.toml").write_text(
        (repo / ".bros.toml").read_text().replace(digest("somename"), "not-a-hash"))
    assert "not SHA-256" in bros(repo, "scrub").stderr
    assert bros(repo, "scrub", "--hash").returncode == 2
