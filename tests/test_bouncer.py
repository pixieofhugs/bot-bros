import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from bros import audit, issue

HOOKS = Path(__file__).parent.parent / "plugins/bouncer/hooks"


def load(name):
    spec = importlib.util.spec_from_file_location(name, HOOKS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


paths = load("guard_paths")
comments = load("guard_issue_comments")


def run(script, payload, root):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    out = subprocess.run([sys.executable, str(HOOKS / script)], input=raw, text=True,
                         capture_output=True, env={"CLAUDE_PROJECT_DIR": str(root)})
    return json.loads(out.stdout)["hookSpecificOutput"] if out.stdout else None


@pytest.fixture
def root(tmp_path):
    (tmp_path / ".bros.toml").write_text(
        '[bouncer]\nask = [".github/*"]\ndeny = ["scripts/hooks/guard.py"]\n')
    return tmp_path


def edit(path):
    return {"tool_name": "Edit", "tool_input": {"file_path": path}}


def bash(command):
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def test_protected_paths_ask_or_deny(root):
    assert paths.decide(edit(".github/workflows/ci.yml"), root)[0] == "ask"
    assert paths.decide(edit(str(root / "scripts/hooks/guard.py")), root)[0] == "deny"
    assert paths.decide(edit("src/app.py"), root) is None
    assert paths.decide(edit("/elsewhere/.github/x"), root) is None


def test_bash_writes_are_caught_but_reads_are_not(root):
    assert paths.decide(bash("sed -i s/a/b/ .github/workflows/ci.yml"), root)[0] == "ask"
    assert paths.decide(bash("echo x > scripts/hooks/guard.py"), root)[0] == "deny"
    assert paths.decide(bash("rm -f scripts/hooks/guard.py"), root)[0] == "deny"
    assert paths.decide(bash("cat .github/workflows/ci.yml 2>/dev/null"), root) is None
    assert paths.decide(bash("grep x scripts/hooks/guard.py"), root) is None


def test_defaults_without_config_still_guard_github(tmp_path):
    verdict = paths.decide(edit(".github/ci.yml"), tmp_path)
    assert verdict[0] == "ask" and "run /bros:setup" in verdict[1]


def test_guards_deny_when_they_cannot_read_input(root):
    assert run("guard_paths.py", "not json", root)["permissionDecision"] == "deny"
    assert run("guard_issue_comments.py", "not json", root)["permissionDecision"] == "deny"
    assert run("guard_paths.py", edit("src/a.py"), root) is None


def test_raw_comment_reads_are_denied():
    flag = "--" + "comments"
    assert comments.decide(bash(f"gh issue view 12 {flag}"))
    assert comments.decide(bash("gh issue view 12 --json title,comments"))
    assert comments.decide(bash("gh api repos/o/r/issues/12/" + "comments"))
    assert comments.decide(bash("gh issue view 12 --json title | grep comments")) is None
    assert comments.decide(bash("bros issue 12")) is None


def test_issue_filter_keeps_only_collaborators():
    collaborators = issue.collaborators_from("Alice\nbob\n")
    kept, withheld = issue.filter_comments(
        [{"author": {"login": "alice"}}, {"author": {"login": "mallory"}}, {"author": None}],
        collaborators)
    assert [c["author"]["login"] for c in kept] == ["alice"] and withheld == 2
    with pytest.raises(issue.LookupError_):
        issue.collaborators_from("\n")


def test_issue_render_says_how_many_were_withheld():
    text = issue.render({"number": 1, "title": "t", "body": "b"}, [], 2)
    assert "2 comments from non-collaborators withheld" in text


def test_audit_pipe_quotes_its_json():
    cmd = audit.pipe({"a": "it's"}, "-X", "PUT", "repos/o/r")
    assert cmd.startswith("echo ") and cmd.endswith("repos/o/r --input -")
    assert json.loads(subprocess.run(cmd.split(" | ")[0], shell=True, capture_output=True,
                                     text=True).stdout) == {"a": "it's"}


def test_bros_guard_runs_the_plugin_hook(root):
    src = Path(__file__).parent.parent / "src"
    out = subprocess.run(
        [sys.executable, "-c", "from bros.cli import main; main()", "guard", "paths"],
        input=json.dumps(edit(".github/x.yml")), text=True, capture_output=True,
        env={"CLAUDE_PROJECT_DIR": str(root), "PYTHONPATH": str(src)})
    assert json.loads(out.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"
