---
name: quiet
description: The comment and PR-body rules hush enforces. Use before writing code comments, docstrings, or a PR description, or when a hush hook says an edit or PR body is over budget.
---

## Comments

A comment says a *why* or a *hazard* the code cannot, in one or two lines. Never what the
code does, what changed, or which issue asked for it — the commit and PR say that. No
docstring that restates the signature.

Budget: a file may gain at most `max(floor, ratio × code)` comment lines (default
`max(4, 0.2 × code)`). `ponytail:` notes and lint pragmas are free. `bros comments` checks
a branch; the edit hook nudges you the moment one edit overspends.

Over budget? Delete narration first. Never pad with code to buy comment room.

## PR body

At most `[pr_body] words` (default 250), attribution footer excluded. Shape:

1. One line: what changed and why.
2. The seam the test sits at.
3. The `bros check` verdict line.
4. One line per deviation from the issue.
5. What a reviewer should eyeball.

No file list, no tour of the diff — the reviewer has the diff.
