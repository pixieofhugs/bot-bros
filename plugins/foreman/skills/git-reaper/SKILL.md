---
name: git-reaper
description: Sweeps accumulated git debris from this repo — stale worktrees, merged branches, orphaned metadata — and reclaims the disk they hold. Surveys and classifies before it deletes anything, guards live sessions and unmerged work, and never acts on a dry run the user has not seen. Use for "clean up the worktrees", "delete the stale branches", "the repo is a mess", or a periodic tidy.
---

# git-reaper

Agent batches leave debris. Every worktree survives its PR, every branch survives its
squash-merge, and nothing sweeps — so it compounds silently until a `git worktree list` is
290 lines long. This skill is that sweep.

**Invoke:** `/foreman:git-reaper` (or name a scope — "just the branches", "just this month's").

**Four hard rules, up front:**

1. **Survey, classify, show, then delete.** Never delete on the first read of anything. The
   dry run is not a formality — see Rule 4 for what it has caught.
2. **A tool that errors is not a tool that says "clean".** Every signal you classify on must
   be a *successful* measurement. Counting the lines of a failed command silently converts
   "I could not tell" into "there is nothing there".
3. **Deletion is permanent once `gc` runs.** Local deletes live in the reflog until then.
   Say this out loud before running `gc`, not after.
4. **Superseded is a conclusion, not an assumption.** An old branch usually holds a stale
   copy of something main has. Sometimes it holds the only copy of a correction nobody
   merged. The check that tells them apart is in Step 4 and it costs seconds.

---

## Step 1 — Survey, change nothing

```
git worktree list --porcelain
git branch --format='%(refname:short)'
gh pr list --state all --limit 2000 --json number,headRefName,state
du -sh .git
```

Fetch the PR list **fresh**. It is the authority for Step 4 and a cached copy from earlier
in the session rots the moment anything merges.

**Done when** you can state four numbers: worktrees registered, worktrees whose directory
still exists, local branches, and `.git` size. If any is a guess, survey again.

---

## Step 2 — Classify, on signals that actually measured something

Per worktree, from **inside it** (`git -C <path> ...`):

| Signal | Command | The trap |
|---|---|---|
| Is it functional? | `rev-parse HEAD` | A broken worktree fails EVERY later probe. Test this first and record the failure — do not let it flow into the others. |
| Uncommitted work | `status --porcelain` | **Check the exit code.** On a broken worktree this errors and prints nothing; `wc -l` reads that as `0` and calls it clean. This produced a false "168 worktrees are empty" reading on a real run. |
| Unique commits | `rev-list --count origin/main..HEAD` | Empty output means the command failed, not that the count is zero. |
| Gutted vs. real | all-` D` status + only ignored files on disk | A temp-dir cleaner or an interrupted removal deletes a worktree's tracked files but leaves the directory skeleton. That is debris, not work in progress. |

**A worktree with broken metadata can be rebuilt** rather than guessed about, and that is the
honest way to get its real status: `.git/worktrees/<name>/` needs only `commondir` (`../..`),
`gitdir` (absolute path to the worktree's own `.git` file) and `HEAD` (`ref: refs/heads/<branch>`,
or a **full 40-char SHA** when detached — an abbreviated SHA is rejected and will break a
worktree that was previously fine). Then `git status` answers truthfully.

**Done when** every worktree carries a verdict backed by a command that exited 0.

---

## Step 3 — Guard the things that must not be swept

Exclude, and say why in the report:

- **The main checkout and the worktree you are running in.**
- **Anything modified recently** (an hour is a sane default). Other sessions run concurrently;
  three worktrees advanced mid-sweep on a real run. A concurrent prune has emptied a live
  worktree here before.
- **Any branch with an OPEN PR**, and any worktree holding one.
- **Detached HEADs whose commits no branch contains.** `git branch --contains <sha>` returning
  nothing means the worktree's HEAD is the only reference. Removing it orphans those commits;
  the next `gc` collects them. Give them a branch first, then decide.

**Done when** the protected set is enumerated with a reason each, not a category.

---

## Step 4 — Decide what is really superseded

**For branches, PR state is the authority — not ancestry.** A squash-merging repo's
merged branch is NOT an ancestor of `main`: `git branch --merged` and `--is-ancestor` both
under-report, and a sweep built on them deletes almost nothing while claiming success.
(A repo that merges with merge commits can trust ancestry; check which before Step 4.)

Delete a branch when **either** holds:

- its `headRefName` has a **MERGED** PR, or
- `git rev-list --count origin/main..<branch>` is **0**.

Everything else gets the content check, because commit titles lie about what survived:

```
MB=$(git merge-base origin/main <branch>)
FILES=$(git diff --name-only $MB..<branch>)
git diff origin/main..<branch> -- $FILES
```

Read the **direction**. `-` lines are in main and absent from the branch (main is ahead —
superseded, delete). `+` lines are in the branch and absent from main. A residual that is
**purely `+` with no `-`** is the tell: that branch adds something main never received. One
such branch held the only copy of a correction to a design brief that had already misled a
design. It looked exactly like the other seven.

**Done when** every branch is either matched to a merged PR, shown to add nothing, or kept
with a one-line reason. "Probably disposable" is not a verdict — check it or keep it.

---

## Step 5 — Sweep the worktrees

**Unlink `node_modules` symlinks FIRST.** `git worktree remove` and `rm -rf` both follow a
symlink and delete the *target's* contents — and agents link worktrees to the main
checkout's `node_modules` to skip an install. This is not hypothetical: a real sweep found five of them aimed at
main's 316 packages.

```
find <worktree> -maxdepth 3 -name node_modules -type l    # every linked one
rm <worktree>/<dir>/node_modules    # or `unlink` it; never `rm -r` / `rm -rf`
```

Then `git worktree remove --force <path>`.

**When that fails with `Permission denied` on `.git/worktrees/<name>`** — the
`fsmonitor--daemon/cookies` directory is the usual cause — `rm -rf`
(after the symlink is gone) succeeds where git's own unlink fails. Remove the working
directory, remove the metadata directory, then `git worktree prune`.

Note the ordering hazard this creates: a failed `git worktree remove` can delete the metadata
*contents* and leave the working tree, producing a worktree that is broken rather than gone.
Step 2's rebuild recovers it.

**Done when** `git worktree list` matches the intended survivors and the main checkout's
`node_modules` still has its package count.

---

## Step 6 — Sweep the branches

`git branch -D` in batches (`-d` refuses squash-merged branches, which on a squash-merging repo is all of them).
Deleting a branch never touches a worktree that has it checked out — git refuses — and never
deletes commits, only the ref.

**Done when** the survivors list is short enough to read, and each has a reason.

---

## Step 7 — `gc`, only with consent

**`git gc` makes every deletion above permanent.** Unreachable objects older than two weeks
are pruned; most swept branches are older than that. Say so plainly and get an explicit yes.

Expect the win to come from collapsing loose objects, not from pruning history: a real run
went 76M → 42M while the pack itself shrank only 2.4 MiB, because deleted branches share
nearly all their objects with history still reachable from `main`.

Afterwards `git fsck --no-dangling` must exit clean.

---

## Step 8 — Report

Before and after, as numbers: worktrees, branches, `.git` size. Then:

- **What was kept and why** — live sessions, open PRs, unmerged work, one line each.
- **Anything rescued** — commits that had no branch, and where they went.
- **Anything found that is not cleanup** — the Step 4 content check surfaces unmerged work.
  That belongs in an issue, not in a branch nobody will read again.
- **What is now unrecoverable**, if `gc` ran.

---

## Scripting notes

Sweeps mean hundreds of `git` calls, so they get scripted, and two things bite every time:

- **Force UTF-8 when reading git output from Python.** A non-UTF-8 locale
  raises `UnicodeDecodeError` on diff bytes and kills the run mid-sweep:
  `subprocess.run(..., encoding="utf-8", errors="replace")`.

Write the script to the scratchpad, not the repo.
