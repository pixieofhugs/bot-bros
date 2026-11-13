import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
CONFIG = ('base = "HEAD"\n\n[[ratchet]]\nname = "lint"\nformat = "{fmt}"\ndir = "lint"\n'
          'run = "cat findings.json"\nallowlist = "allow.json"\n')


def bros(repo, *args):
    return subprocess.run(
        [sys.executable, "-c", "from bros.cli import main; main()", "ratchet", *args],
        cwd=repo, capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(SRC)})


def git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   cwd=repo, check=True, capture_output=True)


def commit(repo):
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base", "--allow-empty")


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "lint").mkdir()
    (tmp_path / "lint/a.py").write_text("x = 1\n")
    (tmp_path / ".bros.toml").write_text(CONFIG.format(fmt="ruff"))
    git(tmp_path, "init", "-q")
    commit(tmp_path)
    return tmp_path


def findings(repo, *codes):
    (repo / "lint/findings.json").write_text(
        json.dumps([{"filename": "a.py", "code": c} for c in codes]))


def allow(repo, mapping):
    (repo / "allow.json").write_text(json.dumps(mapping))


def add_to_config(repo, text):
    (repo / ".bros.toml").write_text((repo / ".bros.toml").read_text() + text)


def test_matching_allowlist_passes(repo):
    findings(repo, "E501", "E501")
    allow(repo, {"a.py::E501": 2})
    r = bros(repo)
    assert r.returncode == 0 and "ok (2 findings, 2 allowed)" in r.stdout


def test_a_new_finding_fails(repo):
    findings(repo, "E501", "F401")
    allow(repo, {"a.py::E501": 1})
    r = bros(repo, "lint")
    assert r.returncode == 1 and "a.py::F401: 1 (allowed 0)" in r.stdout


def test_a_stale_allowlist_fails_until_shrunk(repo):
    findings(repo, "E501")
    allow(repo, {"a.py::E501": 3, "gone.py::I001": 1})
    assert "--shrink" in bros(repo).stdout
    assert bros(repo, "--shrink").returncode == 0
    assert json.loads((repo / "allow.json").read_text()) == {"a.py::E501": 1}


def test_shrink_never_grows_the_allowlist(repo):
    findings(repo, "E501", "E501", "F401")
    allow(repo, {"a.py::E501": 1, "b.py::I001": 1})
    assert bros(repo, "--shrink").returncode == 1
    assert json.loads((repo / "allow.json").read_text()) == {"a.py::E501": 1}


def test_raising_the_allowlist_by_hand_fails_against_base(repo):
    findings(repo, "E501")
    allow(repo, {"a.py::E501": 1})
    commit(repo)
    findings(repo, "E501", "E501", "F401")
    allow(repo, {"a.py::E501": 2, "a.py::F401": 1})
    r = bros(repo)
    assert r.returncode == 1 and "allowlist raised against HEAD" in r.stdout
    assert "a.py::E501: 2 (base allows 1)" in r.stdout


def test_deleting_the_allowlist_to_rebaseline_fails(repo):
    findings(repo, "E501")
    allow(repo, {"a.py::E501": 1})
    commit(repo)
    (repo / "allow.json").unlink()
    r = bros(repo, "--init")
    assert r.returncode != 0 and "Re-baselining is not a fix" in r.stderr


def test_a_missing_base_is_loud(repo):
    (repo / ".bros.toml").write_text((repo / ".bros.toml").read_text().replace(
        'base = "HEAD"', 'base = "origin/nowhere"'))
    findings(repo)
    allow(repo, {})
    r = bros(repo)
    assert r.returncode != 0 and "fetch-depth: 0" in r.stderr


def test_a_missing_allowlist_is_an_error_until_baselined(repo):
    findings(repo, "E501", "F401")
    r = bros(repo)
    assert r.returncode != 0 and "--init" in r.stderr
    assert bros(repo, "--init").returncode == 0
    assert json.loads((repo / "allow.json").read_text()) == {"a.py::E501": 1, "a.py::F401": 1}
    assert "baselined" not in bros(repo, "--init").stdout


@pytest.mark.parametrize("line", [
    "import os  # noqa",
    "# ruff: noqa",
    "# ruff: noqa: E501",
    "# flake8: noqa",
])
def test_python_suppressions_without_a_rule_fail(repo, line):
    findings(repo)
    allow(repo, {})
    (repo / "lint/a.py").write_text(f"{line}\nimport re  # noqa: F401\nnote = '# noqa'\n")
    commit(repo)
    r = bros(repo)
    assert r.returncode == 1 and "a.py:1: suppression with no rule named" in r.stdout
    assert "a.py:2" not in r.stdout and "a.py:3" not in r.stdout


@pytest.mark.parametrize("line", [
    "foo(); // eslint-disable-line",
    "foo(); // eslint-disable-line -- because",
    "/* eslint-disable -- legacy */",
    "/* eslint no-console: off */",
    '/* eslint no-console: ["off"] */',
    "/* eslint no-console: [0] */",
])
def test_eslint_suppressions_without_a_rule_fail(repo, line):
    (repo / ".bros.toml").write_text(CONFIG.format(fmt="eslint"))
    (repo / "lint/findings.json").write_text("[]")
    allow(repo, {})
    (repo / "lint/a.ts").write_text(f"{line}\nbar(); // eslint-disable-line no-console -- ok\n")
    commit(repo)
    r = bros(repo)
    assert r.returncode == 1 and "a.ts:1:" in r.stdout and "a.ts:2" not in r.stdout


@pytest.mark.parametrize("path,text", [
    ("lint/pyproject.toml", '[tool.ruff.lint]\nignore = ["E501"]\n'),
    ("lint/sub/ruff.toml", 'exclude = ["legacy"]\n'),
    ("lint/ruff.toml", 'include = ["one.py"]\n'),
    ("pyproject.toml", '[tool.ruff]\nextend = "../elsewhere.toml"\n'),
])
def test_ruff_config_that_switches_things_off_fails_wherever_it_is(repo, path, text):
    findings(repo)
    allow(repo, {})
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text)
    commit(repo)
    r = bros(repo)
    assert r.returncode == 1 and "changes what ruff checks wholesale" in r.stdout


def test_select_and_line_length_are_pinned(repo):
    findings(repo)
    allow(repo, {})
    add_to_config(repo, 'select = ["E", "F"]\n')
    assert "runs its defaults" in bros(repo).stdout
    (repo / "lint/pyproject.toml").write_text(
        '[tool.ruff]\nline-length = 120\n[tool.ruff.lint]\nselect = ["E"]\n')
    commit(repo)
    r = bros(repo)
    assert r.returncode == 1 and "pins ['E', 'F']" in r.stdout and "line-length 120" in r.stdout


def test_a_crashing_or_empty_linter_is_not_clean(repo):
    allow(repo, {"a.py::E501": 1})
    (repo / ".bros.toml").write_text((repo / ".bros.toml").read_text().replace(
        "cat findings.json", "echo []; exit 2"))
    assert "exited 2" in bros(repo).stderr
    findings(repo)
    (repo / ".bros.toml").write_text(CONFIG.format(fmt="ruff"))
    r = bros(repo, "--shrink")
    assert r.returncode != 0 and "found nothing" in r.stderr
    assert json.loads((repo / "allow.json").read_text()) == {"a.py::E501": 1}


def test_a_linter_that_did_not_run_is_not_clean(repo):
    (repo / ".bros.toml").write_text((repo / ".bros.toml").read_text().replace(
        "cat findings.json", "true"))
    r = bros(repo)
    assert r.returncode != 0 and "printed no JSON" in r.stderr


def test_the_wrong_linter_version_fails_loudly(repo):
    findings(repo)
    allow(repo, {})
    add_to_config(repo, 'requires = "no-such-linter==1.0"\n')
    r = bros(repo)
    assert r.returncode != 0 and "needs no-such-linter==1.0" in r.stderr


def test_eslint_json_is_keyed_by_rule(repo):
    (repo / ".bros.toml").write_text(CONFIG.format(fmt="eslint"))
    (repo / "lint/findings.json").write_text(json.dumps(
        [{"filePath": str(repo / "lint/a.ts"), "messages": [{"ruleId": "no-console"}]}]))
    allow(repo, {"a.ts::no-console": 1})
    assert bros(repo).returncode == 0


def test_max_line_length_is_pinned_on_its_own(repo):
    findings(repo)
    allow(repo, {})
    add_to_config(repo, "line_length = 100\n")
    (repo / "lint/ruff.toml").write_text(
        "line-length = 100\n[lint.pycodestyle]\nmax-line-length = 1000\n")
    commit(repo)
    r = bros(repo)
    assert r.returncode == 1 and "max-line-length 1000" in r.stdout
    assert ": line-length" not in r.stdout


def test_moving_the_allowlist_does_not_dodge_base(repo):
    findings(repo, "E501")
    allow(repo, {"a.py::E501": 1})
    commit(repo)
    (repo / ".bros.toml").write_text((repo / ".bros.toml").read_text().replace(
        '"allow.json"', '"moved.json"'))
    (repo / "moved.json").write_text(json.dumps({"a.py::E501": 1, "a.py::F401": 2}))
    findings(repo, "E501", "F401", "F401")
    r = bros(repo)
    assert r.returncode == 1 and "a.py::F401: 2 (base allows 0)" in r.stdout
