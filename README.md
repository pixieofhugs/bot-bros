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
| **bouncer** | Merge guard, protected paths, safe issue-comment reading, GitHub hardening | next |
| **foreman** | planner → builder → merge bots, reviewer, triage, git-reaper | planned |
| **squint** | Contrast (WCAG maths, design tokens, rendered pages), payload budget | planned |
| **snek / tsk** | Python / TypeScript rules and lint-on-edit | planned |

## How it's built

Two layers, on purpose:

- **Advice** — skills, agents and hooks that shape how an agent works. Plugins; they float
  to the latest version, because better advice never breaks a build.
- **Enforcement** — the `bros` CLI, run in CI through this repo's GitHub Action at a pinned
  tag. Dependabot bumps the tag, so a stricter rule arrives as a PR.

Everything that *enforces* is a plain CLI, so it works whatever wrote the code — Claude,
another agent, or a person. The Claude-specific parts are thin wrappers around it.

```yaml
# .github/workflows/bros.yml (setup writes this)
- uses: pixieofhugs/bot-bros@v0.1.0
  with: { command: "comments origin/${{ github.base_ref }}" }
```

## CLI

| Command | Does |
|---|---|
| `bros check [--all]` | Runs the `[[check]]` commands whose `paths` match your changes; last line is a quotable verdict |
| `bros comments [base]` | Comment budget on the branch: `max(floor, ratio × code)` new comment lines per file |
| `bros comments --hook` | Same, per edit, as a Claude Code hook |
| `bros pr-body` | Warns when a PR body passes `[pr_body] words` |
| `bros scrub` | Fails on emails, home-directory paths, or `[scrub] deny` words in tracked files |

## Contributing

`pytest -q` runs the suite, including an end-to-end run against `tests/fixture/` — a
tiny Python + TypeScript project that stands in for "some other repo" so nothing
project-specific sneaks into a rule.

MIT licensed.
