# Cook refactor — Implementation report

> **Date:** 2026-05-23
> **Branch:** wb+cook-sna-refactor
> **Plan:** cook-refactor.md (all phases 1.0–1.5 implemented)

## What was done

### Deleted
- `src/apt_cook.py` — superseded by the apt split
- `src/configure_gpu.py` — renamed to `configure_gpu_cook.py`
- `src/configure_apps.py` — renamed to `configure_apps_cook.py`

### Created
| File | Phase | Purpose |
|------|-------|---------|
| `src/url_cook.py` | 1.2 | `UrlCook(CookBase)` — former bash_cook, URL installers |
| `src/apt_repo_cook.py` | 1.3 | `AptRepoCook(CookBase)` — GPG keys + .sources files |
| `src/apt_pkg_cook.py` | 1.3 | `AptPkgCook(CookBase)` — nala upgrade/install |
| `src/configure_gpu_cook.py` | 1.5 | rename of configure_gpu.py, no internal changes |
| `src/configure_apps_cook.py` | 1.5 | rename of configure_apps.py, no internal changes |
| `src/write-if-changed` | 1.2 | shell helper (executable); wraps `harness.write_if_changed` for bash snippets |

### Modified

**`src/harness.py`**
- Added `Result(status, message, changed)` frozen dataclass
- Added `VersionInfo(name, installed_version, available_version, source, status, cook, manager)` frozen dataclass
- Added `CookBase` ABC with `install_or_update() → Result` and `show_version() → list[VersionInfo]`
- Added `detect_release()` (moved from apt_cook; used by apt_repo_cook)

**`src/chef.py`**
- Replaced file-order loop + `STANDALONE_PLAYBOOKS` with `graphlib.TopologicalSorter` DAG walk
- Chef reads `needs_root` and `depends_on` from each section; spawns `sudo python cook.py` when `needs_root = true`
- Cooks no longer own sudo elevation

**`src/uv_cook.py`** — walking skeleton
- `UvCook(CookBase)` with `install_or_update() → Result` and `show_version() → list[VersionInfo]`
- `_list_installed()` now returns `dict[str, str]` (name → version) instead of `set[str]`

**`src/cargo_cook.py`** — Phase 1.1
- `CargoCook(CookBase)` with same class shape
- `_read_installed()` parses `~/.cargo/.crates.toml` for `show_version`
- Bootstrap logic extracted to `_bootstrap_binstall()`

**`src/bash_cook.py`** — Phase 1.2
- Repurposed from URL-installer to `BashCook(CookBase)` generic shell executor
- Processes `[bash.<name>]` entries: runs `pre_update` → `install_or_update` → `post_update` snippets via `bash -c`
- Prepends `SRC_DIR` to `$PATH` so `write-if-changed` is available to snippets

**`src/recipe.toml`** — all phases
- `[bash.*]` renamed to `[url.*]`; `url_cook.py` handles these
- `needs_root` and `depends_on` added to all top-level sections
- New `[bash]` section (root) with four entries migrated from apt_cook:
  - `apt_prereqs` — installs curl, gnupg, ca-certificates, nala
  - `trusted_gpgd_hook` — writes DPkg hook file + sets chattr +i
  - `ubuntu_pin` — writes `/etc/apt/preferences.d/ubuntu-archives.pref` via heredoc
  - `debconf_code_insiders` — sets debconf selection for code-insiders
- `[apt]` split into `[apt_repo.*]` (repos) + `[apt_pkg]` (packages)
- `[configure_gpu]` and `[configure_apps]` declared with `needs_root = true`, `depends_on = ["apt_pkg"]`

## Execution order (verified via topo-sort)

```
level 0  url, bash            (independent; url=non-root, bash=root)
level 1  cargo, uv, apt_repo  (url→cargo/uv, bash→apt_repo)
level 2  apt_pkg              (apt_repo→apt_pkg)
level 3  configure_gpu, configure_apps  (apt_pkg→both)
```

## Deferred (per plan)

- Phase 2: chef imports cook classes directly; chef owns concurrency; `--dry-run` CLI
- Phase 3: chef-driven idempotency loop
- Phase 4: per-entry `depends_on` granularity
- Phase 5: configure_gpu / configure_apps internal refactor to class shape
