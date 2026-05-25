# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Idempotent, declarative system config for a fresh Ubuntu/Kubuntu Wayland laptop: apt repos/packages, vendor CLIs, eGPU auto-PRIME, and per-app GPU flags. One script (`chef`) serves both first-run bootstrap and ongoing upkeep — re-runs only touch what would actually change.

## Commands

Tooling is `uv` (Python ≥ 3.14) driven through `just`:

- `just up` — apply `recipe.toml` (re-execs under sudo).
- `just plan` — dry-run: probe and print the report, no changes, no root.
- `just lint` — `ruff check --fix` + `ruff format` + `chef --lint` (validate `recipe.toml` against cook schemas).
- `just tc` — lint, then `uvx pyright src`.
- `just test` — typecheck, then `uv run pytest`.
- Single test: `uv run pytest tests/test_recipe_graph.py::test_name`.

## Architecture

The core abstraction is **chef** (the orchestrator) driving **cooks** (thin per-domain managers). Chef owns every diff/idempotency decision; a cook only *probes* current state and *acts* — it holds no diff logic.

Flow (`src/chef.py`): re-exec as root → parse `recipe.toml` → `schema_lint.validate` → `recipe_graph` builds a DAG → `cook_runner.run_recipe` topo-sorts and runs it → report. Exit codes: `0` ok, `75` soft fail, `1` hard fail (aborts).

**Section → cook by naming convention, no registry.** A `recipe.toml` section `[foo]` resolves to `cooks/foo_cook.py`, or `cooks/foo_root_cook.py` for an always-root cook. The module must define exactly one `CookBase` subclass (`recipe_graph.load_cook_class`). Adding a domain = add a `[section]` to `recipe.toml` + a `cooks/<section>_cook.py` with one cook class and its `entry_model`.

**`recipe.toml` is the single source of config.** Its header documents every section and the two chef-reserved per-entry fields, `needs_root` and `depends_on` (stripped before the slice reaches the cook). A subtable section (`[url.<name>]`) fans out to one graph node per entry; a plain-data section (`[apt_pkg]`) is one node.

**Two cook shapes** (`src/cook_base.py`):

- `VersionedCook` — versioned packages. Implements `list_requested` / `list_installed` / `find_latest` / `sync`. `PackageListCook` covers plain `packages = [...]` sections (cargo, uv, snap, apt_pkg).
- `StateCook` — desired-state resources. Implements `get_current_state` / `get_desired_state` / `apply_resource`, plus `get_hooks`. `FileStateCook` diffs by sha256 of rendered bytes vs on-disk file.

`EntrySpec` (pydantic, `extra='forbid'`) is each cook's recipe-entry schema, so a typo'd key fails the run instead of being silently ignored. `pre_hook` (guard: non-zero skips the item) and `post_hook` (runs after a change) live on `StateEntrySpec`, the base for state-cook entries — only `StateCook` honors them, so a versioned section keeps the bare `EntrySpec` and a hook declared there fails the lint instead of silently never running. Cooks compose intrinsic hooks via `chain_hooks`.

**Convergence is create/update only.** Cooks drive resources toward their desired *presence*; they never prune. Removing an entry from `recipe.toml` (or uninstalling its target) leaves prior artifacts in place — a stale `.desktop` override, a repo's keyring + `.sources`, a written `/etc` drop-in. Teardown is manual (see the README's eGPU Rollback).

**Privilege model** (`src/harness.py`, `cook_runner`): chef runs as root. A `needs_root` node runs in-process; every other node is run in a **forked child** that calls `become_user()` (drops gid→groups→uid, repoints `HOME`/`USER`/`PATH` at the invoking `SUDO_USER`) and pipes its `CookResult` back as pickle. `--lint` rejects `needs_root` on a subtable header — grant it per leaf entry (least privilege). Forking only happens from the main thread to keep loguru's locks safe.

**Logging** (`src/logs.py`): one parent thread (the "pump") owns the log file and terminal — fd 1/2 of the parent and every forked cook funnel through a single pipe, so a live rich region (table/progress in `terminal.py`) never interleaves with log lines. Logs are timestamped per run under `logs/`.

## Static assets

`src/files/` holds files installed verbatim (the `egpu-prime` switch + its systemd unit, and `write-if-changed`), referenced by `[file.<name>]` entries.
