"""`merge_pr.py` queue: the same `land` in a loop, stopping at the first failure."""

from __future__ import annotations

from bros import merge as merge_pr
from bros.merge import guard


def test_queue_stops_at_first_failure_and_uses_its_exit_code(tmp_path, capsys):
    seen = []

    def fake_land(number, say):
        seen.append(number)
        say("merging")
        merged = {"1": (merge_pr.MERGED, "ok")}
        return merged.get(number, (merge_pr.NEEDS_REVIEW, "no review"))

    code = merge_pr.run_queue(["1", "2", "3"], land_fn=fake_land, root=tmp_path)

    assert code == merge_pr.NEEDS_REVIEW
    assert seen == ["1", "2"]  # 3 never ran
    assert "#1: merging" in capsys.readouterr().out
    assert (tmp_path / ".claude" / "merge-queue.lock").exists()


def test_queue_prints_failing_ci_lines_then_stops(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(merge_pr, "failed_lines", lambda n: ["FAILED test_x - boom"])

    def land(n, say):
        return merge_pr.BLOCKED, f"PR #{n}: required checks failed: test"

    code = merge_pr.run_queue(["5", "6"], land_fn=land, root=tmp_path)
    assert code == merge_pr.BLOCKED
    out = capsys.readouterr().out
    assert "FAILED test_x - boom" in out
    assert "#6" not in out


def test_failed_lines_keeps_only_failed_and_E_lines(monkeypatch):
    log = (
        "job\tstep\t2026-01-01T00:00:00Z noise\n"
        "job\tstep\t2026-01-01T00:00:01Z E   assert 1 == 2\n"
        "job\tstep\tFAILED a::b"
    )
    monkeypatch.setattr(merge_pr, "_view", lambda n: {"headRefOid": "abc"})
    runs = '[{"databaseId": 9, "conclusion": "FAILURE"}]'
    monkeypatch.setattr(guard, "_gh", lambda *a: runs if a[1] == "list" else log)
    assert len(merge_pr.failed_lines("5")) == 2


def test_read_queue_takes_numbers_and_skips_comments(tmp_path):
    f = tmp_path / "q"
    f.write_text("# wave 2\n12\n#34 later\n\nnotes\n")
    assert merge_pr.read_queue(str(f)) == ["12", "34"]
