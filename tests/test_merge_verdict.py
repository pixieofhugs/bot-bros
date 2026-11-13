"""The review verdict line bros merge requires (written by foreman's reviewer agent)."""

import pytest

from bros import merge as merge_pr

FULL = "a" * 40
CLEAN = f"PR #7 @ {FULL} — VERDICT: blocking=0 nits=1"


def _refusal(text, resolve=lambda sha: FULL if FULL.startswith(sha) else None):
    return merge_pr.review_refusal("7", text, lambda sha: [], resolve=resolve)


def test_a_clean_review_may_say_a_blocking_finding_is_resolved():
    text = f"{CLEAN}\n\nThe BLOCKING finding from round one is resolved."
    assert _refusal(text) is None


def test_a_verdict_with_blocking_findings_refuses():
    text = f"PR #7 @ {FULL} — VERDICT: blocking=1 nits=0\n\nBLOCKING  a.py:1 — breaks"
    assert _refusal(text)[0] == merge_pr.BLOCKED


@pytest.mark.parametrize(
    "text",
    [
        "no findings",
        f"PR #7 @ {FULL}\n\nno findings",
        f"x\n{CLEAN}",
        f"PR #7 @ {FULL} — VERDICT: blocking=1 nits=0 blocking=0",
        f"PR #8 @ {FULL} — VERDICT: blocking=0 nits=0",
    ],
)
def test_a_missing_or_malformed_verdict_line_refuses(text):
    assert _refusal(text)[0] == merge_pr.NEEDS_REVIEW


def test_a_short_unique_sha_passes_and_an_unknown_one_refuses():
    assert _refusal(f"PR #7 @ {FULL[:9]} — VERDICT: blocking=0 nits=0") is None
    unknown = _refusal(f"PR #7 @ {'b' * 9} — VERDICT: blocking=0 nits=0")
    assert unknown[0] == merge_pr.NEEDS_REVIEW


def test_an_extra_field_is_ignored_but_blocking_is_required():
    assert _refusal(f"PR #7 @ {FULL} — VERDICT: blocking=0 nits=1 follow-ups=2") is None
    assert _refusal(f"PR #7 @ {FULL} — VERDICT: nits=1")[0] == merge_pr.NEEDS_REVIEW


def test_follow_ups_never_block():
    assert _refusal(f"PR #7 @ {FULL} — VERDICT: blocking=0 follow-ups=3 nits=0") is None
