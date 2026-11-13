# Bot Bros

**A crew of Claude Code plugins that keep agent-written code honest.**

Agents write a lot of code fast. Left alone they narrate every line in comments, write
PR descriptions nobody reads, merge their own work past red checks, and ship UI that
fails contrast. You can fix each of those once, in one repo — and then you start a second
repo and fix them all again.

Bot Bros is the second repo's fix. Each rule lives here once; every project installs it.
Improve a rule, and every project gets the improvement — as a pinned, bot-bumped PR it
can go red on, never as a surprise on `main`.

## Install (about a minute)

In any repo, new or existing:

```text
/plugin marketplace add pixieofhugs/bot-bros
/plugin install bros@bot-bros
/bros:setup
```

Setup looks at your repo, installs what's missing (asking first), asks a few questions
with a recommended answer for each, and writes one config file: `.bros.toml`. The only
thing you might type yourself is a GitHub login.

## The crew

| Bro | Does | Status |
|---|---|---|
| **bros** | `/bros:setup` — installs and configures the rest | v0 |
| **hush** | Comment budget + PR-body budget. Agents stop narrating. | v0 |
| **bouncer** | Merge guard (owner's PR, green checks, a review), protected paths, safe issue-comment reading, GitHub hardening audit | v0 |
| **foreman** | The `reviewer` agent, `bros merge` and the `merge-bot` skill; planner → builder bots, triage, git-reaper next | v0 |
| **squint** | Contrast (WCAG maths, design tokens, rendered pages), payload budget | planned |
| **ratchet** (CLI) | Lint findings may only shrink: per file and rule, both ways, no cheating | v0 |
| **snek / tsk** | Python / TypeScript rules and lint-on-edit | planned |

## How it's built

Two layers, on purpose:

- **Advice** — skills, agents and hooks that shape how an agent works. Plugins; they float
  to the latest version, because better advice never breaks a build.
- **Enforcement** — the `bros` CLI, run in CI through this repo's GitHub Action at a pinned
  tag. Dependabot bumps the tag, so a stricter rule arrives as a PR.

Nothing fails silently. A hook that can't run — `bros` missing, no `.bros.toml`, `gh`
logged out, a payload it can't read — says what broke and how to fix it, to the agent,
which can then fix it for you.

Everything that *enforces* is a plain CLI, so it works whatever wrote the code — Claude,
another agent, or a person. The Claude-specific parts are thin wrappers around it.

```yaml
# .github/workflows/bros.yml (setup writes this)
- uses: pixieofhugs/bot-bros@<commit-sha> # v0.2.0
  with: { command: "comments origin/${{ github.base_ref }}" }
```

## CLI

| Command | Does |
|---|---|
| `bros check [--all]` | Runs the `[[check]]` commands whose `paths` match your changes; last line is a quotable verdict |
| `bros comments [base]` | Comment budget on the branch: `max(floor, ratio × code)` new comment lines per file |
| `bros comments --hook` | Same, per edit, as a Claude Code hook |
| `bros merge <N>... [--queue FILE]` | Lands PRs: readies, updates, waits for green, needs a `blocking=0` review at the current head, then merges through bouncer's guard. Queue mode stops at the first that doesn't land |
| `bros pr-body` | Warns when a PR body passes `[pr_body] words` |
| `bros issue <N>` | An issue with only collaborators' comments; strangers' text never reaches the agent |
| `bros audit` | GitHub hardening check: ✓/✗/? per setting, with the command that fixes each ✗ |
| `bros guard <paths\|issue-comments\|merge>` | Runs a bouncer hook for another harness (OpenCode, git hooks) — same file the plugin runs |
| `bros ratchet [name] [--shrink\|--init]` | Linter findings (ruff, eslint JSON) per `path::code` may only shrink, and so may the allowlist against `base`. Refuses rule-less suppressions; for ruff, also config that switches rules or files off and unpinned `select`/line length. Not covered yet: eslint config files, per-rule limits, notebooks |
| `bros scrub [--hash <word>]` | Fails on emails, home-directory paths, or denied words in tracked files. `[scrub] deny_sha256` refuses a word without publishing it |

## Contributing

`bros check --all` runs everything CI runs: the suite (including an end-to-end run against `tests/fixture/` — a
tiny Python + TypeScript project that stands in for "some other repo" so nothing
project-specific sneaks into a rule), the scrub, the ruff ratchet and the comment budget.

MIT licensed.
