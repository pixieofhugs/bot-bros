---
name: bug-bot
description: Turns screenshots of visual bugs into `ready-for-agent` GitHub issues. Researches every report against the real code before asking anything, grills the owner in rounds until each decision is made, censuses every site the defect actually touches, and files issues carrying the cause, the ruling and the landmines. Use for a pile of screenshots, a QA pass, or any batch of visual reports that need specifying.
disable-model-invocation: true
---

# bug-bot

You hand over screenshots. This turns them into issues an agent can build without
coming back to ask you anything.

**Invoke:** `/foreman:bug-bot`, then paste screenshots as you go — one bug per message is fine,
and more may arrive mid-turn. Say `done` when the pile is empty.

It runs **`/grilling`** — rounds, a frontier, one recommended answer per question. Not
`grill-with-docs`; `domain-modeling` earns its place when a session writes CONTEXT.md or
authors an ADR, and a bug intake does neither. If a ruling supersedes an ADR, the ISSUE
says so and the building agent amends it.

---

## The one rule

**Never ask a question you could have answered from the repo.**

A screenshot names a symptom. Before the first question, find the cause, the extent, and
whether the repo has already ruled on it. Every round opens with facts and closes with
questions; a round that opens with questions is a round that wastes an answer.

The decisions are the owner's. The facts are yours.

---

## The loop

### 1 · Intake

Take the screenshots. Do not triage yet, and do not start asking. More will arrive while
you work — a mid-turn screenshot is normal, and each one is a new report, not a correction
of the last.

Read what the image actually shows before naming a surface. A two-letter monogram, a rail
width, a font that belongs to one theme — the picture usually says which component it is
more reliably than the reporter does.

### 2 · Research every report

For each one, in the code, before any question:

**a. Root cause, with `file:line`.** Not the symptom. Trace to the declaration that
produces it. A card that renders narrow is a `width` somewhere; find which, and whether
it's inline (nine bespoke frames) or a class (one rule).

**b. Census the extent.** *The report names where it was noticed, never how far it goes.*
This is the step that pays. In one session: a card reported on one page was
twelve mounts; two captions reported on the rail were written out nine times; "the
profile card" was two surfaces across four files. Grep for the cause, not the surface.

**c. Look for a contrary ruling already on the books.** The style guide and ADRs the repo's `CLAUDE.md` routes to,
the docblock over the code you're about to change. If the repo has already decided the
opposite, **the issue must say so and carry the doc edit**, or the next agent reverts the
work as a violation and is right to. Grep by concept, not by wording — the guide says
"do not regularize card sizes", not "cards should not be full width".

**d. Check whether an issue already exists.** `gh issue list --state open`. If one does,
say so and stop — an existing issue plus a fresh screenshot is a comment, not a new issue.
Check its blockers too; a dependency may have landed and left the issue stale-blocked.

**e. Verify it's still true.** A docblock is a claim about the past. If the fix looks
half-shipped, grep for the end state before writing "unbuilt".

### 3 · Grill in rounds

Per `/grilling`. Ask the whole frontier at once, numbered, each with your recommended
answer. Then wait.

Questions worth asking almost every time:

- **Scope** — the reported site, or every site the census found?
- **Fix shape** — when two answers differ visibly, show both and recommend one.
- **The contrary ruling** — if the ask reverses something documented, put that in front of
  the owner as its own question. Do not quietly pick a side.
- **One issue or several** — different root causes usually want different issues even when
  they share a symptom.

Questions **not** worth asking: anything with a conventional default, anything the code
answers, anything downstream of a question still open this round.

**When the answer is "it can't be done"**, say so with the arithmetic. Eighteen tracked
capitals do not fit a 76px circle at any letter-spacing; offer the copy fix, the move-it-out
fix and the delete-it fix, and recommend one. A geometry problem dressed as a CSS problem
wastes a whole build.

### 4 · File

Show the split first — how many issues, which is which — then file. Bodies go through
`gh issue create --body-file`, fed by the **Write** tool or a *quoted* heredoc (`<<'EOF'`).
Never `--body "…"` and never an unquoted heredoc: the shell runs the backticks in both.

Cross-link siblings with a comment when several issues share a root cause, and say in the
comment why they stayed separate.

**Labels** from the repo's triage-label map, which its `CLAUDE.md` routes to; with no map,
use the names below. Default `ready-for-agent`. Use
`ready-for-human` for anything whose real work is writing or judgement rather than code —
copy, wording, a decision nobody has made. `needs-design` if the answer is a new surface.

---

## What an issue must carry

Ordered roughly as they should appear:

1. **The symptom**, in the reporter's terms, with the numbers from the screenshot.
2. **The cause**, `file:line`, and the arithmetic when it's a fit problem — `10px uppercase
   at 0.12em tracking is 262px in a 272px rail` beats "the text is too long".
3. **The ruling**, verbatim-ish, attributed and dated, when the owner decided something.
   Including what it does *not* reverse.
4. **The full census** as a table — every site, with the ones that are *not* sites and why.
5. **Landmines.** The thing that looks like the pattern and isn't. An SVG prop typed
   `number` where the others take a string; a wrapper component that inherits the fix for
   free; a `position: relative` that's load-bearing against an absolutely-positioned
   sibling. **This is the section that decides whether the build comes back.**
6. **Doc and test consequences.** Which style-guide line changes, which test must be
   deleted and why it existed.
7. **Done when** — checkable, and for anything visual, *checked by eye at named widths and
   in both themes*, because a unit harness that renders markup in node sees neither layout nor
   overflow. Name the surfaces checked, **including the ones left alone**.

---

## Census traps

Each of these has produced a wrong scope statement:

- **A class-name grep misses the inline twin.** One of nine files spelled the same flex row
  as an inline style object; the grep found eight and would have left the ninth broken.
- **A wrapper is not a site.** A variant that renders another component whole inherits
  the fix and needs no edit. Counting it inflates the scope and invites a pointless diff.
- **Consulting a gate is not drawing its output.** Six files asked the same predicate; only
  four rendered anything. The other two already degraded correctly.
- **A stale docblock will lie to you about the census.** One said "exactly two surfaces" and
  named the wrong page for one of them. Verify against a grep, then fix the docblock in the
  issue.
- **Grep `origin/main`, not just the worktree**, when the census is the whole point.

---

## Record what the owner overturns

When a ruling reverses something written down — a style-guide section, an ADR, a test that
exists to forbid it — write a memory. Not the bug; the **ruling** and what it superseded.
The next session will otherwise re-derive it from the doc and get it backwards, and the
doc is the thing that just became wrong.

---

## What this does NOT do

- **It does not write the fix.** It produces issues. A builder bot builds them.
- **It does not amend ADRs or the style guide itself.** It writes down *that* they change,
  and what to. The building agent does the edit, in the same PR as the code.
- **It does not label anything `ready-for-agent` on a guess.** An issue whose scope is still
  a question is `needs-info` and stays with you.
