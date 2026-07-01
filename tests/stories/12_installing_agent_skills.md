# 12. [Installing agent skills](test_12_installing_agent_skills.py)

`[skills]` declares GitHub repos of Claude Code skills to keep installed, wrapping
the `skills` CLI (skills.sh) through `bunx` — the same tool an operator would
otherwise run by hand for each repo. totchef owns the declaration; the CLI owns
fetching and writing into `~/.agents/skills` plus the agent's own skills
directory.

## 12.1 Declare skill repos and keep them current

> As an operator, I want to declare which GitHub repos of skills I use, so that a
> fresh machine (or a stale one) gets them installed without me remembering the
> `skills add` invocation for each one.

### 12.1.1 skills installs each declared repo via the skills cli

`[skills] repos = [...]` installs each repo globally for Claude Code via
`bunx skills add <repo> -g --agent claude-code --skill '*' -y`, one repo at a time.

### 12.1.2 skills requires bun and bunx and fails hard pointing at url bun

Requires both `bunx` (runs the `skills` CLI) and `bun` (links a cli-kind skill's
binary — see 12.1.9) to be present, depending on the `[url]` bun installer; if
either is missing, the run fails hard telling the operator the `[url]` bun install
must run first.

### 12.1.3 a repo report row shows the most recent skill timestamp

The `skills` CLI has no per-skill semantic version, only a per-skill `updatedAt`
timestamp in its own lockfile (`~/.agents/.skill-lock.json`). A repo's report row
shows the most recent `updatedAt` among its skills as a human-readable timestamp,
not an opaque hash.

### 12.1.4 an installed repo reports unchanged when no skill timestamp moved

An already-installed repo is still re-synced on every run (the CLI is re-invoked,
since there's no cheap way to know its upstream latest ahead of time), but a
repo whose skills' timestamps are all unchanged reports back as unchanged.

### 12.1.5 an installed repo reports upgraded when a skill timestamp moved

When any skill under a repo picks up a newer `updatedAt` (new content, or a skill
added since the last run), the repo is reported as upgraded.

### 12.1.6 the run log breaks down which skills were new updated or unchanged

Because the report row is per repo, not per skill, the run log carries the detail:
each sync logs, per repo, which of its skills were newly added, which had a
changed timestamp, and which were untouched — read from the lockfile before and
after that repo's `skills add` ran.

### 12.1.7 a failed repo reports hard naming the failed repo

If `skills add` fails for a repo (an inaccessible or renamed GitHub source), the
run reports a hard failure naming it.

### 12.1.8 multiple repos install concurrently

Each declared repo is installed in its own `skills add` invocation; multiple
repos run concurrently rather than one after another.

### 12.1.9 a cli-kind skill binary is chmod and linked onto path

A skill that ships its own `package.json` with a `bin` entry (a "cli"-kind skill,
e.g. `peek`) gets its files installed by the `skills` CLI, but the CLI never
chmods that binary executable or puts it on PATH. After each repo's `skills add`,
the cook chmods the skill's declared `bin` script(s) executable and runs
`bun link` from within the skill's own directory, so the binary resolves on PATH.
This is best-effort and idempotent, like bun's own node shim (§4.3.4): it runs on
every sync, so a converged re-run restores the link if it was removed.
