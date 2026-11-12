import json
import os
import subprocess
import sys
from pathlib import Path

from bros import comments as cb
from bros import prbody as pb

SRC = Path(__file__).parent.parent / "src"

PY = '''"""Module docstring."""
x = 1  # trailing comments are code lines
# a comment
answer = """
not a docstring
"""
# ponytail: free
def f():
    """Doc
    string."""
'''

TS = """// line
/**
 * block
 */
const a = 1;
{/* jsx */}
// eslint-disable-next-line
"""


def test_classify_python():
    assert cb.classify(PY, ".py") == ["c", "x", "c", "x", "x", "x", "", "x", "c", "c"]


def test_classify_c_like():
    assert cb.classify(TS, ".tsx") == ["c", "c", "c", "c", "x", "c", ""]


def test_budget_floor_ratio_and_config():
    assert cb.Budget().allowed(0) == 4
    assert cb.Budget().allowed(50) == 10
    assert cb.Budget({"floor": 1, "ratio": 0.5}).allowed(10) == 5
    assert not cb.Budget({"skip": ["gen/"]}).checked("gen/a.py")


def _hook(tool_input, cwd):
    return subprocess.run(
        [sys.executable, "-c", "from bros.cli import main; main()", "comments", "--hook"],
        input=json.dumps({"tool_input": tool_input}),
        capture_output=True, text=True, cwd=cwd,
        env={**os.environ, "PYTHONPATH": str(SRC)},
    )


def test_hook_nudges_a_comment_heavy_edit(tmp_path):
    r = _hook({"file_path": "a.py", "old_string": "x = 1",
               "new_string": "# c\n" * 6 + "x = 1"}, tmp_path)
    assert r.returncode == 2 and "6 comment lines" in r.stderr


def test_hook_passes_a_rewrite_that_cuts_comments(tmp_path):
    r = _hook({"file_path": "a.ts", "old_string": "// c\n" * 9,
               "new_string": "// c\n" * 6}, tmp_path)
    assert r.returncode == 0


def test_hook_ignores_non_code_and_bad_input(tmp_path):
    assert _hook({"file_path": "a.md", "content": "# h\n" * 50}, tmp_path).returncode == 0
    assert _hook({}, tmp_path).returncode == 0


def test_pr_body_words_skip_the_footer():
    footer = "🤖 Generated with [Claude Code](https://claude.com/claude-code)"
    assert pb.words(f"one two three\n\n{footer}\nhttps://claude.ai/code/x") == 3


def test_pr_number_from_create_output_or_edit_arg():
    assert pb.pr_number("gh pr create --fill", "https://github.com/o/r/pull/12\n") == "12"
    assert pb.pr_number("gh pr edit 34 --body-file -", "") == "34"
    assert pb.pr_number("gh pr view 34", "") is None
