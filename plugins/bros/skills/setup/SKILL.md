---
name: setup
description: Install and configure Bot Bros in the current repo, new or existing. Use when the user says "set up bot bros", "/bros:setup", "add the bros to this repo", or wants to re-run setup after changing their stack. Re-running updates config; it never clobbers it.
disable-model-invocation: true
---

You set the crew up **for** the user. They should type nothing but answers, plus one
login if GitHub needs it. Ask before every install or change; never ask them to run a
command you could run yourself. Never skip anything silently: every step you skip,
every install that fails and every default you fell back to goes in the final summary,
with why and how to fix it.

## 1. Survey (read-only, no questions yet)

Collect, in parallel where you can:

- `git rev-parse --show-toplevel` — not a repo? Offer `git init`, then continue.
- Stack: `pyproject.toml` / `requirements*.txt` / `setup.py` → Python;
  `package.json` → TypeScript/JS (note the package manager from its lockfile). Record
  which directories each lives in — monorepos have several.
- Existing `.bros.toml`, `.claude/settings.json`, `CLAUDE.md`, `.github/workflows/`.
- Tools: `python3 --version` (≥ 3.11), `uv`, `pipx`, `gh`, `gh auth status`, `bros --version`.
- Remote: `gh repo view --json nameWithOwner,visibility,defaultBranchRef`.

Python missing or older than 3.11: say Bot Bros needs it, stop. Any other stack (Go,
Rust, …): say v1 supports Python and TypeScript only; continue for the parts that apply.

## 2. Fix what's missing (ask once, as a list)

Show one list of what's missing and what you'll do about each. On approval:

- **`gh` missing** — install with the package manager you find: `pacman -S github-cli`,
  `brew install gh`, `sudo apt install gh`, `winget install GitHub.cli`. If none fits,
  link https://cli.github.com and wait.
- **`gh` not logged in** — the one step only a human can do. `gh auth login` needs a real
  terminal, which your shell isn't. Tell them, exactly:

  > Open any terminal window, paste `gh auth login --web`, and follow the browser.
  > Come back here and say "done".

  Then confirm with `gh auth status`. Don't continue until it passes.
- **No GitHub remote** — offer `gh repo create <name> --source . --private --push`
  (ask public vs private).
- **`bros` CLI** — `uv tool install git+https://github.com/pixieofhugs/bot-bros`
  (fall back to `pipx install …`, then `python3 -m pip install --user …`).
- **mattpocock-skills** (foreman's triage and grilling skills build on it) —
  `claude plugin marketplace add mattpocock/skills` and
  `claude plugin install mattpocock-skills@mattpocock --scope project`.

## 3. Ask about the project

Use AskUserQuestion; recommend an answer for each from what the survey found, so a user
who knows nothing can accept every default.

1. **Checks** — for each stack directory, the command that proves a change is good
   (propose from `package.json` scripts and pyproject tool config: e.g.
   `pytest`, `npm --prefix web run lint`, `npm --prefix web test`). These become `[[check]]`.
2. **Crew** — which plugins to turn on: `hush` (comment + PR-body budgets), `bouncer`
   (protected paths, safe issue reading, repo hardening). Recommend all.
3. **Generated paths** the comment budget must skip (migrations, generated API clients).
4. **Protected paths** — `ask` (user approves each change; recommend `.github/*`) and
   `deny` (only a human edits: guard scripts, anything they name).

## 4. Write the config

Read each file before you change it; merge, never overwrite keys the user set.

**`.bros.toml`** at the repo root:

```toml
base = "origin/main"          # the branch PRs merge into

[[check]]
name = "pytest"
paths = ["api/*"]             # glob over changed files; omit to always run
run = "pytest -q"

[comments]
skip = ["api/migrations/"]    # floor = 4, ratio = 0.2 are the defaults

[pr_body]
words = 250

[bouncer]
ask = [".github/*"]
deny = []
```

**`.claude/settings.json`** — add, keeping everything else:

```json
{
  "extraKnownMarketplaces": {
    "bot-bros": { "source": { "source": "github", "repo": "pixieofhugs/bot-bros" } }
  },
  "enabledPlugins": { "hush@bot-bros": true, "bouncer@bot-bros": true },
  "permissions": { "deny": ["Read(./.env)", "Read(./.env.*)", "Read(./**/.env)"] }
}
```

The `permissions.deny` lines keep secrets files out of context; drop any the repo
doesn't have and add its own (key files, credential JSON).

Committed settings switch plugins on but don't download them, so also run
`claude plugin install <plugin>@bot-bros --scope project` for each.

**CI** — `.github/workflows/bros.yml`, pinned by commit SHA to the latest release
(`gh release view -R pixieofhugs/bot-bros --json tagName`, then
`gh api repos/pixieofhugs/bot-bros/commits/<tag> --jq .sha`), with the tag as a comment
so Dependabot can bump it:

```yaml
name: bros
on: pull_request
permissions: { contents: read }
jobs:
  comments:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@<sha> # <tag>
        with: { fetch-depth: 0 }
      - uses: pixieofhugs/bot-bros@<sha> # <tag>
        with: { command: "comments origin/${{ github.base_ref }}" }
```

If `.github/dependabot.yml` has no `github-actions` entry, offer to add one — that is
how a newer Bot Bros reaches this repo: as a PR it can go red on, not a surprise.

**`CLAUDE.md`** — add (or refresh) one block, nothing else:

```markdown
## Bot Bros
Rules and gates come from the Bot Bros plugins; project values live in `.bros.toml`.
Hand back with the last line of `bros check`.
```

## 5. Harden GitHub

Run `bros audit`. Show its list as is — ✓, ✗ with the fix, ? with why — and ask which
✗ fixes to apply. Then:

- `gh api` fixes: run the printed command.
- **CODEOWNERS**: write `.github/CODEOWNERS` with `* @<owner>` plus a line per
  `[bouncer]` path.
- **Dependabot**: add a `github-actions` entry to `.github/dependabot.yml`. If the file
  says the owner left it out on purpose, say so and leave it.
- **SHA pins**: rewrite each flagged `uses: owner/action@tag` to
  `owner/action@<sha> # <tag>`.

Re-run `bros audit` and show what's still ✗ or ?, and why.

## 6. Prove it

Run `bros check --all` and `bros comments`. Show the verdict lines. Then one short
summary: what you installed, what you wrote, what's left (if anything) and why.
