# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A re-runnable, declarative Ubuntu/Kubuntu Wayland laptop configurator. `src/recipe.toml`
declares desired state; `chef` topo-sorts it into a graph and runs cooks that probe and
act. Same path serves first-run bootstrap and ongoing upkeep — idle re-runs are cheap
because nothing rewrites what already matches.

## Commands

Recipes are driven through `just` (see `justfile`):

- `just up` — run chef (re-execs itself under `sudo`). Applies `recipe.toml`.
- `just plan` — dry run: probe and print the report, no changes, no root.
- `just lint` — `ruff check --fix`, `ruff format`, then `chef --lint` (validates `recipe.toml` against cook schemas, no root).
- `just tc` — lint, then `uvx pyright src`.
- `just test` — tc, then `uv run pytest`.
- `just clone <owner/repo>` — shallow clone into `reference_clones/` for reading upstream code.

Run a single test (pytest `pythonpath`/`testpaths` are configured in `pyproject.toml`):

```bash
uv run pytest tests/test_recipe_graph.py::test_name
```

Python 3.14, uv-managed. Direct invoke: `./src/chef.py [--dry-run|--lint]`.

## Architecture

Pipeline (`recipe.toml` → report):

`schema_lint.validate` → `recipe_graph` (build nodes + dependency graph) →
`cook_runner.run_recipe` (topo-sort + execute) → `cooks/*` → `chef.print_report`.

**chef owns the diff; cooks only probe and act.** Cooks hold no idempotency logic. chef
decides install-vs-upgrade (VersionedCook) or current-vs-desired (StateCook) and calls
the cook to execute that decision.

**Section → cook by filename, no registry.** Recipe section `[foo]` resolves to
`cooks/foo_cook.py`, or `cooks/foo_root_cook.py` for an always-root cook.
`load_cook_class` imports whichever exists and requires exactly one `CookBase` subclass
per module. To add functionality: add a section to `recipe.toml` and create the cook file
— nothing else to register.

**Two cook shapes** (`cook_base.py`):

- `VersionedCook` — packages with versions: `list_requested` / `list_installed` / `find_latest` / `sync`.
- `StateCook` — desired-state resources: `list_resources` / `get_current_state` / `get_desired_state` / `get_hooks` / `apply_resource`.

Each cook declares an `entry_model` (a pydantic `EntrySpec` subclass, `extra='forbid'`)
that is the recipe schema for its entries — `--lint` validates every node's slice against
it, so a typo'd recipe key fails fast with a precise message.

**Graph & scheduling.** A subtable section (`[url.<name>]`) expands to one node per entry;
a plain section (`[apt_pkg]`) is a single node. Per-entry `depends_on` (name an entry, a
single-node section, or a whole section to fan out) and `needs_root` are read by chef and
stripped before the slice reaches the cook. `run_recipe` walks the graph with
`graphlib.TopologicalSorter`, running ready nodes concurrently.

**Privilege model.** chef re-execs under `sudo` once. Each node runs either in-process as
root (`needs_root`) or in a forked child that drops to the invoking user via
`harness.become_user()` and pipes its `CookResult` back (so `CookResult` and its rows must
stay picklable — plain dataclasses only). `needs_root` is granted per leaf, never on a
subtable header (`--lint` rejects that) — least privilege.

**Logging is single-writer** (`logs.py`). fd 1/2 are redirected into a pipe that one pump
thread reads, so the parent and every forked cook funnel through one writer. The log file
gets minimalist TOON; the terminal gets rich tables/progress bars rendered on a saved dup
of the real stdout (`TERMINAL_FD`). Forked user cooks must not draw to the terminal — they
emit log lines and the parent renders after collecting results. `drain_logs()` is a FIFO
barrier so a directly-rendered table lands after the logs preceding it.

**Module layering.** `logs.py` is a leaf (stdlib + loguru + toon). `harness.py` (privilege
drop, streamed subprocess, `write_if_changed`, binary discovery, URL fetch) builds on it.
`recipe_graph` → `schema_lint` → `cook_runner` layer on top.

Exit codes: `0` success, `75` soft fail (recoverable, named in a banner), `1` hard fail
(aborts the run).

## Conventions specific to this repo

- Cook docstrings state privilege scope and which manager dirs they write into — keep that when editing.
- Static assets installed verbatim (systemd units, the `write-if-changed` CLI) live in `src/files/`; shell snippets pipe writes through `write-if-changed` rather than writing files directly.
- Markdown is linted with `rumdl` (`.rumdl.toml`).
