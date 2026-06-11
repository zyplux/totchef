# 1. Running totchef

## 1.1 [Apply a recipe to converge the system](test_1_running_totchef.py)

> As an operator, I want to run `totchef up` and have my machine brought into
> compliance with my recipe, so that one command bootstraps a fresh install or
> reconciles drift on an existing one.

### 1.1.1 up resolves validates escalates previews then executes

`totchef up` resolves the recipe, loads and validates it, escalates to root,
previews the plan, and executes — creating or updating every resource that
differs from the desired state. Validation comes first, so an invalid recipe
is rejected with the schema error *before* the `sudo` prompt ever appears.

### 1.1.2 up is idempotent rerun reports nothing changed

The run is **idempotent**: re-running when nothing has drifted reports
"nothing changed" and makes no modifications. The work done on the second run is
only what genuinely differs. The one exception is the `url` vendor cook, which diffs
*presence* rather than version: a tool that is already present re-runs its
`update_action` on every run (see §3.3.1).

### 1.1.3 exit code communicates outcome

The exit code communicates the outcome to scripts and CI: `0` = success,
`75` = soft failure (something recoverable failed but the system is usable),
`1` = hard failure (a critical step failed and the apply was aborted).

### 1.1.4 invalid recipe rejects the run before any apply

Every run validates the recipe first — the same checks as `totchef lint` — and
one invalid entry rejects the whole run before any cook applies: even the
valid entries' targets stay untouched.

## 1.2 [Preview changes without touching the system](test_1_running_totchef.py)

> As an operator, I want to see exactly what `totchef` would change before it
> changes anything, so that I can review a risky run or check for drift safely.

### 1.2.1 plan dry run prints table makes no changes

`totchef plan` performs a **dry run**: it probes current state and prints a
plan table of every resource and what would happen (`would install`,
`would upgrade`, `would sync`, `would apply`, `up-to-date`, `ok`), but makes no
changes.

### 1.2.2 plan requires no root

A dry run requires **no root** — it never escalates privileges.

### 1.2.3 plan shows all resources including unchanged

The plan shows *all* resources (including unchanged ones) so the operator sees
the full intended end state, not just the diff.

### 1.2.4 up prints plan first from silent probe

During a real `up`, the same plan is printed first (from a silent probe pass)
so the operator sees what is about to happen before it happens.

## 1.3 [Validate a recipe without running it](test_1_running_totchef.py)

> As an operator, I want to check that my recipe is well-formed before I rely on
> it, so that a typo fails fast instead of mid-run.

### 1.3.1 lint validates and prints path valid

`totchef lint` validates the recipe against every cook's schema and the
dependency graph, then prints `<path>: valid` or exits with a precise error.

### 1.3.2 lint catches schema and graph errors

Validation catches: a section with no registered cook, an unknown or
misspelled recipe key (`extra='forbid'` rejects it rather than silently ignoring
it), a dependency naming a node that doesn't exist, a dependency cycle, a node that
depends on itself, and `needs_root` granted on a subtable header instead of a leaf
entry.

### 1.3.3 lint needs no root and changes nothing

Linting needs no root and changes nothing.

## 1.4 [Find out which recipe will be used](test_1_running_totchef.py)

> As an operator, I want to know which `recipe.toml` totchef will pick up from my
> current directory, so that I'm never surprised by the wrong file being applied.

### 1.4.1 where prints resolved recipe path

`totchef where` prints the resolved recipe path and exits.

### 1.4.2 recipe discovery follows fixed precedence

Recipe discovery follows a fixed precedence: an explicit `--recipe`/`-r PATH`,
then the `$TOTCHEF_RECIPE` environment variable, then walking up from the current
directory looking for `recipe.toml` (project-local), then
`~/.config/totchef/recipe.toml`, then `/etc/totchef/recipe.toml`.

### 1.4.3 no recipe found lists searched locations

When no recipe is found, the error lists every location that was searched, so
the operator knows exactly where to put one.

## 1.5 [Discover available cooks](test_1_running_totchef.py)

> As an operator, I want to list every configuration domain totchef can manage on
> this machine, so that I know which recipe sections are valid and where each comes
> from.

### 1.5.1 cooks lists section scope and origin

`totchef --list-cooks` prints a table of every resolvable cook: the **section**
name it serves (e.g. `apt_pkg`, `url`), its **scope** (`root` or `user`), and its
**origin** (`built-in`, `plugin:<dist>`, or `local:<path>`).

### 1.5.2 cooks reflects live registry

This reflects the live registry, so an installed plugin or a dropped-in local
cook shows up immediately.

## 1.6 [Check the version](test_1_running_totchef.py)

> As an operator, I want `totchef --version` to report the installed version, so I
> can confirm what I'm running.

### 1.6 version reports installed version

`totchef --version` prints the installed package version and exits.
