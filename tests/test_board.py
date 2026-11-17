"""`bros board` row building from canned GraphQL data, plus its gh boundary."""

import json

from bros import board, issue

STAMP = (
    "> *Verified by /foreman:planner-bot.*\n**Verified-at:** `abcdef1234567890`\n"
    "**Premise:** the thing holds"
)


def _issue(number, blockers=(), prs=(), comments=(), body="x" * 10):
    return {
        "number": number, "title": f"issue {number}", "body": body,
        "blockedBy": {"nodes": [{"number": n, "state": s} for n, s in blockers]},
        "closedByPullRequestsReferences": {
            "nodes": [{"number": n, "isDraft": d} for n, d in prs]
        },
        "comments": {
            "nodes": [{"author": {"login": a}, "createdAt": "t", "body": b} for a, b in comments]
        },
    }


def test_rows_show_open_blockers_in_flight_pr_size_and_collaborator_stamp():
    issues = [
        _issue(1, blockers=[(7, "OPEN"), (8, "CLOSED")], prs=[(50, True)],
               comments=[("owner", STAMP)]),
        _issue(2, comments=[("stranger", STAMP)]),  # a stranger's stamp is not a stamp
    ]
    one, two = board.build_rows(issues, frozenset({"owner"}))
    assert (one["blocked_by"], one["pr"], one["size"]) == ("#7", "#50 (draft)", 10)
    assert one["stamp"] == "abcdef123 — the thing holds"
    assert two["blocked_by"] == "-" and two["pr"] == "-" and two["stamp"] == "-"


def test_main_makes_one_graphql_call(monkeypatch, capsys):
    calls = []

    def fake_gh(args):
        calls.append(args)
        if args[:2] == ["api", "graphql"]:
            return json.dumps({"data": {"repository": {"issues": {"nodes": [_issue(1)]}}}})
        return "owner\n"

    monkeypatch.setattr(issue, "_gh", fake_gh)
    assert board.main(["--repo", "o/r"]) == 0
    assert sum(c[:2] == ["api", "graphql"] for c in calls) == 1
    assert "#1 issue 1" in capsys.readouterr().out


def test_failed_collaborator_lookup_prints_no_board(monkeypatch, capsys):
    def fake_gh(args):
        if args[:2] == ["api", "graphql"]:
            raise AssertionError("board fetched before collaborators")
        return ""  # nobody: collaborators_from refuses

    monkeypatch.setattr(issue, "_gh", fake_gh)
    assert board.main(["--repo", "o/r"]) == 1
    out = capsys.readouterr()
    assert out.out == "" and "REFUSING" in out.err


def test_a_pipe_in_a_title_stays_inside_its_cell():
    row = board.build_rows([_issue(3)], frozenset())[0] | {"title": "a | b"}
    assert "a \\| b" in board.render([row])
