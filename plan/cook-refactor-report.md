# Cook refactor — Implementation report

> **Phase:** Walking skeleton (§8 of cook-refactor.md)
> **Date:** 2026-05-23
> **Status:** Ready for `just up` verification

## What was implemented

### `src/harness.py`
Added two frozen dataclasses:
- `Result(status, message, changed)` — cook return type; `status` is `"ok" | "soft_fail" | "hard_fail"`
- `VersionInfo(name, installed_version, available_version, source, status, cook, manager)` — version probe return type

### `src/uv_cook.py`
Converted from procedural to class-based:
- `UvCook(packages, uv)` — constructor takes parsed section data and binary path
- `install_or_update() -> Result` — existing parallel install/upgrade logic, now returns `Result`
- `show_version() -> list[VersionInfo]` — reads `uv tool list`, maps to `VersionInfo` per requested package
- `main()` reduced to: validate preconditions → construct → call → map result to exit code

### `src/chef.py`
- Replaced linear `config.items()` loop with `graphlib.TopologicalSorter` walk (`get_ready()` / `done()`)
- `run_cook()` now takes `needs_root: bool`; chef spawns via `sudo --preserve-env=...` when true, refuses to run `needs_root=false` cooks as root
- Orchestration keys (`needs_root`, `depends_on`) stripped from section data before serialising to `SECTION_ENV` — cooks never see them
- `STANDALONE_PLAYBOOKS` kept intact (configure_gpu / configure_apps move to recipe.toml in Phase 1.5)
- Added `run_standalone()` for the standalone playbooks path (no needs_root logic — they manage their own sudo internally)

### `src/recipe.toml`
Added `needs_root` and `depends_on` to all four top-level sections:

| section | needs_root | depends_on |
|---------|------------|------------|
| `bash`  | false | `[]` |
| `cargo` | false | `["bash"]` |
| `uv`    | false | `["bash"]` |
| `apt`   | true  | `[]` |

### `.gitignore`
Added `.venv` (no trailing slash) alongside `.venv/` — the slash-only pattern misses symlinks, which arise when sharing a venv across git worktrees.

## Behavioral note: execution order changed

Old order: `bash → cargo → uv → apt`
New order: `bash → apt → cargo → uv`

`bash` and `apt` both have `depends_on = []` so they land in the same topo tier; within a tier, file-insertion order applies, putting apt second. This is correct per the plan's design intent (apt is intended to parallelize with bash in Phase 2). No functional dependency is violated. If the old order is preferred for now, add `depends_on = ["cargo", "uv"]` to `[apt]`.

## Verification needed

`just up` must be run manually — sudo is unavailable in this environment. Expected outcome: "Unchanged:" on all sections if the system is already configured.

## Deferred (next steps per plan)

- **Phase 1.1:** Convert `cargo_cook` to `CargoCook`; extract base class after two cooks share scaffolding
- **Phase 1.2:** bash split — rename `bash_cook.py` → `url_cook.py`; new `bash_cook.py` as generic shell executor
- **Phase 1.3:** apt split — `apt_repo_cook.py` + `apt_pkg_cook.py`
- **Phase 1.4:** Migrate apt's weird stuff (debconf, prereqs, hardening, pinning) to `[bash.<name>]` entries
- **Phase 1.5:** Declare `configure_gpu` / `configure_apps` in recipe.toml; remove `STANDALONE_PLAYBOOKS`
