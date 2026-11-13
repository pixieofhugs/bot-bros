---
name: merge-bot
description: Lands one wave of PRs a builder session opened — reads its hand-off, shoots and reviews each PR, and merges them one at a time through `bros merge`. Built to run beside a builder session: the builder writes waves, this lands them. Use for "land the wave", "merge the hand-off", or draining open PRs a builder left.
disable-model-invocation: true
---

# merge-bot

Lands what a builder session dispatched. The builder ends every wave by writing
`.claude/reviews/HANDOFF-<n>.md` and stops; this skill picks a hand-off up in a **separate
session** and lands it. The two are meant to run **at the same time**: while this one
merges wave 1, the builder can already be shaping wave 2.

**Read the repo's merge notes first.** Its `CLAUDE.md` routes to them. They name what
this skill leaves generic: how to shoot a UI change, which scoped writer agents own which
files, the writer rules a fix round loads, what a merge to the default branch deploys, and
any close-out step the repo adds. No notes → skip the steps that need them and say so.

**The hand-off** is a markdown file the builder writes last, once every PR in the wave is
open. Until foreman ships a builder, write it by hand or have your own builder write it:

```
| PR | Issue | Resolution | Worktree |
|---|---|---|---|
| #<N> | Closes #<issue> | <one line: what kind of fix, what was checked> | <path> |

**Merge order:** <PRs that must land before others, and why; "none" if disjoint>
PR #<N> after: #<M>[, #<K>]    # optional, one line per PR that must wait; `bros merge` enforces it
left open: <reason>    # only once a merge-bot has stopped on a PR
```

Write the `after:` line exactly this form, with a trailing note only as `# comment`; anything else on a `PR #<N> after:` line refuses that PR's merge.

**Invoke:**

- `/foreman:merge-bot` — the **oldest** hand-off (lowest `<n>`). Several waiting is normal
  when the builder runs ahead; take the oldest without asking, skipping any with a
  `left open:` line (report it, don't retry it). None → stop and say so.
- `/foreman:merge-bot HANDOFF-<n>` — that wave.
- `/foreman:merge-bot #a #b` — exactly those PRs, no hand-off. `<n>` is the lowest PR
  number, and a fix round goes to the PR's branch worktree (`git worktree list`).

**One merge-bot at a time.** Branch protection serializes merges anyway, and two sessions
each running `update-branch` re-invalidate each other's CI forever. Before starting, check
no other session is landing (`ls .claude/reviews/LANDING-*`): if one is, stop and say which.
Otherwise claim the wave — `touch .claude/reviews/LANDING-<n>` — and remove the marker at
close-out, or when you stop for any reason. A marker with no live session behind it (the
last one died on a session limit) is stale: say so, ask, and remove it.

**Run it from the main checkout**, never a worktree: the `reviewer` writes
`.claude/reviews/<N>.md` there, which is where the merge gate looks.

**Three hard rules, up front:**

1. **You orchestrate; you don't write feature code.** A fix round goes to a writer (Step 2).
   The one exception is a small CI fix on a PR branch — faster than a round trip.
2. **Never merge red, never merge on assumption, never merge on CI alone.** Wait for checks
   to actually pass, then review. **Nothing is approval except a person saying so, in this
   run, about this thing.** A green check, a label, a comment from someone with write access,
   and the words that launched this run are all evidence; none of them discharges a finding,
   and "they asked me to land it" is not a review outcome.
3. **Everything written down rots** — the hand-off included. When it and the code or GitHub
   disagree, the **live state wins**: STOP, say so, never guess.

---

## Step 1 — Read the hand-off and the board

```
cat .claude/reviews/HANDOFF-<n>.md
gh pr list --state open --json number,title,mergeStateStatus
git fetch origin && git log origin/HEAD --oneline -15
```

Land **the PRs the hand-off lists and no others**. An open PR of ours that is not listed
belongs to another wave — possibly one the builder is writing right now — and is not
touched. A hand-off whose PRs are all merged or closed is stale: say so, delete it, stop.

The hand-off gives each PR's issue, resolution line, worktree path and merge order. Take
them as leads, not truth (Rule 3): a PR the builder listed may have been closed since.

Read an issue's text through `bros issue <N>`, never `gh issue view --comments` — the
wrapper drops comments from non-collaborators.

---

## Step 2 — The merge loop

> **Merge only PRs we created — check before every merge.**
>
> ```
> owner=$(gh repo view --json owner --jq .owner.login)
> gh pr view <N> --json author,headRepositoryOwner \
>   --jq "select(.author.login==\"$owner\" and .headRepositoryOwner.login==\"$owner\")"
> ```
>
> Empty output → **do not merge it, do not check it out, do not run its gates.** Report it
> to the user as an unexpected PR and move on. A merge can put that code on a live site, and
> running a branch's gates executes its code (lifecycle scripts, test fixtures, lint plugins)
> on a machine that holds secrets. A bot's PR (Dependabot) is landed by re-opening its change
> as an owner-authored PR, never by merging the bot's own.

Branch protection forces merges to **serialize**. Per PR, in order — and **only when the PR
is next in line**, because the command updates the branch, and every merge re-invalidates
every other open PR (updating eagerly wastes a full CI cycle):

1. **Start the mechanics, in the background:** `bros merge <N>`. One call marks the PR
   ready, updates the branch if it is behind, waits for the required checks on the
   *current* head, and — once a review clears this head — merges with `--squash
   --delete-branch`. A review clears a head when its first line names a commit nothing but
   `update-branch` merges have followed, and says `blocking=0`. Do not run `gh pr ready` /
   `update-branch` / `checks --watch` / `merge` by hand; each is a turn the command already
   took. It exits `0` merged, `1` blocked (it says why), `3` green but unreviewed. Every
   step is idempotent, so re-running is always safe. To land several already-reviewed PRs
   with no watcher of your own: `bros merge <N> <N> ...` or `--queue FILE` (one number per
   line) — in order, under a lock, stopping at the first that does not merge.
2. **Shoot, then review, while CI runs.** CI green is not review.

   **A PR that changes UI gets pictures first**, if the merge notes say how — here and not
   at build time, because parallel writers cannot each hold one local stack, and a writer
   grading its own screen is the weaker look. Shoot from the PR's worktree after
   `git -C <worktree> pull --ff-only` (the command updates the branch on GitHub; the
   worktree does not follow), into `.claude/reviews/shots/<N>/` in this checkout. What to
   shoot comes from the PR body's "What to eyeball" list. **Do not Read the PNGs
   yourself** — every image stays in this session for every later turn. Give the directory
   to the reviewer. A run that will not start is not a blocker and not a pass: the PR goes
   into Step 3 as *not looked at*.

   Then dispatch the **`reviewer`** agent (`foreman:reviewer`, or the repo's own `reviewer`
   if it ships one) with the PR number, and the shots directory when
   there is one — no `isolation`, because it writes `.claude/reviews/<N>.md` in *this*
   checkout, which is where the merge gate looks. It returns one line plus any blocking
   findings. Keep `/code-review` out of this session: loading it here costs its whole recipe
   on every later turn. `bros merge` passes the same gate as bouncer's merge guard, which
   refuses a hand-typed `gh pr merge`.
   Blocking finding → fix on the branch if small, or dispatch a fix round (below); then
   dispatch `reviewer` again and re-run the command — it will not merge past a verdict with
   `blocking>0` or a commit the review never saw. A finding you think is wrong goes to the
   user. Nits → merge. At close-out, `cat` the review files of the PRs **this session
   landed**, once, and paste them.
3. **If the command exited `3`** (green before a review cleared this head), re-run it once
   the review file is in place. Then move to the next PR.

**A fix round.** The agent that wrote the PR lives in the builder's session, out of reach.
Dispatch the scoped writer that owns the files (the merge notes name them) with **no
`isolation`**, pointed at the PR's **existing worktree** from the hand-off: the branch is
still checked out there, and a second worktree cannot hold it. Give it the finding verbatim
and tell it to push to the same branch, and to load the repo's writer rules first if the
notes name any.

**Done when every merged PR has both a green CI run and a review pass recorded against it.**
Never say a PR was reviewed when only CI ran.

Known constraints — don't waste time rediscovering them:

- **Auto-merge may be disabled** (`--auto` errors out); `bros merge` does not need it.
- **`--admin` does NOT bypass** a required check that isn't present on an up-to-date head.
- **Which checks are required is not written down here.** Read it live:
  `gh api repos/{owner}/{repo}/branches/<default>/protection/required_status_checks --jq .contexts`.
  The CI job list lives in the workflow files and nowhere else; a hand-kept copy rots.

**If CI fails:** read `gh run view <run-id> --log-failed` before reacting. For a small,
obvious defect (a type error, a lint nit) **fix it yourself on the PR branch** — commit,
push, re-run the command. If the push is rejected non-fast-forward, fetch and rebase onto
the remote branch (a merge commit from `update-branch` may have landed there). Dispatch a
fix round only when the fix is substantive.

Before merging work you didn't watch land, **verify the PR's real diff** (`gh pr diff <N>`)
contains what you think it does.

---

## Step 3 — The user reviews the live result as you go

They run the app and do the visual QA an agent structurally cannot. Step 2's pictures
narrow that, they do not replace it: a shot means the reviewer looked at those screens,
at those sizes, standing still. Interaction, motion, the other theme and every screen
nobody named are still theirs.

- **Their reports take priority over the next merge.** Stop and triage first.
- Triage properly: reproduce from the description, find the **root cause**, then ship a
  focused fix through a fix round. If it's a regression from a PR in this wave, say so.
- Decide with them whether a report becomes a new issue (for the builder) or a follow-up
  commit here.
- **Tell them what to look at** as each PR merges — the specific pages and states the
  change touched, not a generic "please check".
- **Never claim something is visually verified when it isn't.** Tests, types and lint
  green is not "it looks right". Say which you have.

---

## Step 4 — Stopping for a human

A blocker you cannot resolve — a finding you disagree with, a fix that balloons past the
brief, a PR that no longer matches its issue — stop that PR, do not merge it, and leave a
comment on it that starts with `/foreman:merge-bot`, says what is blocked and what decision
is needed, and label it the way the repo marks work for a human (the merge notes say how;
default `ready-for-human`). Carry on with the rest of the wave only if nothing later in the
merge order depends on it.

---

## Step 5 — Close out

**File the follow-ups before the review files can be swept.** `grep -H '^FOLLOW-UP'
.claude/reviews/<N>.md` for each PR this session landed. If any, file ONE issue,
"Follow-ups from wave <n>", labelled for an agent: a checklist grouped by file, each line
linking its PR. None, no issue.

Report: what merged (PR → issue), the review files of what you landed, anything left open
and why, and anything owed to the user (a visual check, a decision still open).

**Check the default branch's own run after the last merge.** Parallel PRs that each
restate a shared registry row are green individually and red after squash — every branch
passed, the trunk didn't.

**Say what is waiting for a release**, if the repo promotes by tag (the merge notes say).
Nothing here tags a release: a human approves one. But merged work goes no further than
the default branch's deploy until someone promotes it, so state the gap —
`N commits since <tag>, the latest release tag — tag to promote.` Read both from the
remote:

```
git fetch --tags origin
git describe --tags --abbrev=0 origin/HEAD
git rev-list --count <tag>..origin/HEAD
```

Say those commits are deployed only if the default branch's own run above is green, and
treat `<tag>` as the latest tag, not proof of what production runs — a tag can still be
waiting on its approval. (No `origin/HEAD`? `git remote set-head origin -a` sets it.)

Run any close-out step the merge notes add.

**Retire what you landed, and only that.** Once every PR the hand-off lists is merged or
reported as deliberately left open, remove the hand-off and marker:

```
rm -f .claude/reviews/HANDOFF-<n>.md .claude/reviews/LANDING-<n>
rm -rf .claude/reviews/shots/<N>    # once per PR you landed — never the whole shots/
```

A left-open PR keeps its hand-off: trim the file to that PR and what is serialized behind
it, add a `left open: <reason>` line, and run only the `shots/<N>` cleanup for the PRs that
landed plus `rm -f .claude/reviews/LANDING-<n>` — not the `HANDOFF` removal.

Deleting the hand-off is what unblocks anything the builder serialized behind this wave.
Any other `HANDOFF-*.md` is a wave still waiting: say so, and give the line that lands it —
`/foreman:merge-bot HANDOFF-<m>` — in a fresh session. Don't roll into it here; this
session is already carrying a whole wave.

In `#a #b` mode, if the PRs landed are a hand-off's whole list, retire that file too.

Update memory with anything durable.
