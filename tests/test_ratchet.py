import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"


def bros(repo, *args):
    return subprocess.run(
        [sys.executable, "-c", "from bros.cli import main; main()", "ratchet", *args],
        cwd=repo, capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(SRC)})


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "lint").mkdir()
    (tmp_path / "lint/a.py").write_text("x = 1\n")
    (tmp_path / ".bros.toml").write_text(
        '[[ratchet]]\nname = "ruff"\nformat = "ruff"\ndir = "lint"\n'
        'run = "cat findings.json"\nallowlist = "allow.json"\n')
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    return tmp_path


def findings(repo, *codes):
    (repo / "lint/findings.json").write_text(
        json.dumps([{"filename": "a.py", "code": c} for c in codes]))


def allow(repo, mapping):
    (repo / "allow.json").write_text(json.dumps(mapping))


def test_matching_allowlist_passes(repo):
    findings(repo, "E501", "E501")
    allow(repo, {"a.py::E501": 2})
    r = bros(repo)
    assert r.returncode == 0 and "ok (2 findings, 2 allowed)" in r.stdout


def test_a_new_finding_fails(repo):
    findings(repo, "E501", "F401")
    allow(repo, {"a.py::E501": 1})
    r = bros(repo, "ruff")
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


def test_suppressing_instead_of_fixing_fails(repo):
    findings(repo)
    allow(repo, {})
    (repo / "lint/a.py").write_text(
        "import os  # noqa\nimport re  # noqa: F401\nnote = '# noqa in a string'\n")
    (repo / "lint/pyproject.toml").write_text('[tool.ruff.lint]\nignore = ["E501"]\n')
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    r = bros(repo)
    assert r.returncode == 1
    assert "a.py:1: bare suppression" in r.stdout
    assert "a.py:2" not in r.stdout and "a.py:3" not in r.stdout
    assert "`ignore` switches rules" in r.stdout


def test_a_linter_that_did_not_run_is_not_clean(repo):
    (repo / ".bros.toml").write_text((repo / ".bros.toml").read_text().replace(
        "cat findings.json", "false"))
    r = bros(repo)
    assert r.returncode != 0 and "printed no JSON" in r.stderr


def test_the_wrong_linter_version_fails_loudly(repo):
    findings(repo)
    allow(repo, {})
    (repo / ".bros.toml").write_text(
        (repo / ".bros.toml").read_text() + 'requires = "no-such-linter==1.0"\n')
    r = bros(repo)
    assert r.returncode != 0 and "needs no-such-linter==1.0" in r.stderr


def test_eslint_json_is_keyed_by_rule(repo):
    (repo / ".bros.toml").write_text((repo / ".bros.toml").read_text().replace(
        'format = "ruff"', 'format = "eslint"'))
    (repo / "lint/findings.json").write_text(json.dumps(
        [{"filePath": str(repo / "lint/a.ts"), "messages": [{"ruleId": "no-console"}]}]))
    allow(repo, {"a.ts::no-console": 1})
    assert bros(repo).returncode == 0


def test_a_missing_allowlist_is_an_error_until_baselined(repo):
    findings(repo, "E501", "F401")
    r = bros(repo)
    assert r.returncode != 0 and "--init" in r.stderr
    assert bros(repo, "--init").returncode == 0
    assert json.loads((repo / "allow.json").read_text()) == {"a.py::E501": 1, "a.py::F401": 1}
    assert "baselined" not in bros(repo, "--init").stdout
