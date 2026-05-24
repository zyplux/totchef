# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Declarative, idempotent Ubuntu/Kubuntu Wayland laptop config: apt repos + packages, eGPU auto-PRIME at boot, and Chromium/Electron GPU flags. Same script handles first-run bootstrap and ongoing upkeep — re-runnable; cooks only rewrite files whose contents would actually change.

## Commands

- `just up` — run the full configuration (`./src/chef.py`). Chef re-execs itself under `sudo` if it isn't root yet, so this prompts for the password once at the start.
- `just plan` — `./src/chef.py --dry-run`: probe everything and print the report (full inventory) without changing anything.
- `just lint` — `ruff check --fix` then `ruff format`.
- `just tc` — lint then `uvx pyright src` (depends on `lint`). After changing deps, run `uv sync` so the `.venv` pyright reads against is current.
- Markdown lint: `rumdl` (configured in `.rumdl.toml`; disables `MD033`/`MD013`).
- Requires Python ≥3.14 (`pyproject.toml`). Runtime deps are `loguru`, `toon-format`, and `typer`; everything else is stdlib.
- There is **no test suite** — verification is `just plan` (everything you didn't touch shows `up-to-date`/`unchanged`) and re-running `just up`.

## Architecture

### Orchestrator → cook contract

Phase 2: **chef runs as root and owns all idempotency/diff decisions**; cooks are thin managers that only probe and act. `src/chef.py` (a `typer` single-command CLI, `--dry-run` flag) does the following, in order:

1. `ensure_root()` re-execs chef under `sudo` if `geteuid() != 0` (preserving argv via the venv's `sys.executable`, so `uv run`'s deps stay importable). `sudo` sets `SUDO_USER`, the user `become_user()` later drops back to.
2. Reads `src/recipe.toml` and **expands it into a graph of nodes**: a section with named subtables (`[url.*]`, `[file.*]`, `[bash.*]`, `[apt_repo.*]`) becomes **one node per entry** (`url.rustup`, `file.write_if_changed`); a section with plain data (`[apt_pkg]`) or none (`[desktop]`) is a single node. `needs_root` / `depends_on` are read **per entry**, falling back to the section default; `depends_on` may name a whole section (`"bash"` → all its entry nodes) or a specific entry (`"url.rustup"`). The cook class is found **generically** — `<section>_cook`'s sole `CookBase` subclass, located by import (no central registry; chef stays ignorant of concrete cooks, seeing them only as `VersionedCook` / `StateCook`).
3. Walks the graph via `graphlib.TopologicalSorter`, dispatching **ready nodes concurrently**: a `needs_root = true` node runs **in-process** (apt/snap serialize on the dpkg/snapd lock anyway), a `needs_root = false` node runs in a **forked child** that passes through the single `harness.become_user()` chokepoint, returning its `CookResult` to the root parent over a pipe (pickle). Forks happen only from the main thread, so loguru's locks stay safe.
4. Chef computes the diff itself, per cook kind (see Cook base class), runs the work, and for desired-state cooks fires `pre_hook`/`post_hook` **only when an action is taken** (`pre_hook` is a guard — non-zero skips the item benignly; `post_hook` non-zero is a soft failure). The node's TOML slice (minus `needs_root` / `depends_on`) is handed to the cook's constructor directly.
5. Prints a compact, **changes-first** report (TOON): a normal run shows only changed/failed rows plus an "N unchanged" footer; `--dry-run` shows the full inventory and acts on nothing.
6. Exit-code contract: `0` = success, `75` (`SOFT_FAIL_EXIT`) = soft fail (continue; named in a final banner), `1` = hard fail (abort `just up`; chef stops dispatching new work, drains running children, then exits).

**Adding a new tool category** means adding a `[newcategory]` section to `recipe.toml` and creating `src/newcategory_cook.py` as a single `VersionedCook` or `StateCook` subclass — the section name *is* the cook module name, so no registration step. **Prefer data over a new cook:** if it's just files, use `[file.<name>]` entries; if it fits the `[bash.<name>]` schema (a `check_installed`/`desired`-gated idempotent snippet), add an entry there. A dedicated cook is for operations that genuinely need structured parsing, complex idempotency, or non-trivial state probing.

### Shared scaffolding (`src/harness.py`)

Every cook imports from here. Key utilities:

- `become_user()` — the **one privilege-drop chokepoint**. Chef's forked child calls it before running a user-scope cook: drops gid first (root can't set gid after dropping uid), reconstructs supplementary groups via `initgroups`, drops uid, then repoints `HOME` / `USER` / `LOGNAME` / `XDG_CACHE_HOME` / `PATH` at the invoking user. `load_section()` is the env-based loader used only by `debug_main` for inspecting a single cook by hand.
- `start_log_tee()` — tees stdout/stderr into `logs/sys-conf-py-<timestamp>.log`. Honors `SYS_CONF_PY_LOG_FILE` if set (one log per run; forked children inherit the fds); pre-chowns log + dir to `SUDO_USER`.
- `stream_subprocess(cmd, ...)` — runs a child with merged stdout/stderr piped line-by-line through `loguru`. Forces `TERM=dumb` + `NO_COLOR=1` + `start_new_session=True` to strip ANSI and block `/dev/tty` bypass. Splits CR-overwrites into separate log lines. Use this, not `subprocess.run`, for anything whose output you want in the log.
- `write_if_changed(path, content, mode, note)` — the idempotency primitive: compare bytes, skip when equal, log `Unchanged:` vs `Writing  :`. Cooks funnel **every** file write through this. `[bash.*]` snippets get a `write-if-changed` CLI (a standalone stdlib reimplementation, `src/files/write-if-changed.py`, installed to `/usr/local/bin` by the `[file.write_if_changed]` entry that `bash.ubuntu_pin` depends on).
- `find_binary(name)` — `shutil.which` first, then `bootstrap_bin_dirs()` (`~/.cargo/bin`, `~/.bun/bin`, `~/.local/bin`, `~/.claude/local`), needed because `rustup` / `bun` / `uv` install there before they're on `PATH`. The dirs are computed from `Path.home()` **at call time** so they follow `become_user`'s `$HOME` in the forked child — but still **only call from user-scope context**: in the root parent `Path.home()` is `/root`.
- `fetch_url(url)` — `urllib` with a custom `User-Agent: sys-conf-py` (some CDNs 403 the urllib default).

### Cook base class (`src/cook_base.py`)

A cook holds **no decision logic** — chef diffs and decides. Two shapes share `CookBase` (which carries `needs_root` / `manager`):

- **`VersionedCook`** (packages with a version): `requested()`, `list_installed() -> {name: version}`, `latest_available(names) -> {name: version | None}` (best-effort; `None` renders as `—`), and `sync(to_install, to_upgrade) -> Result`. Chef computes the install/upgrade split, calls `sync` once (the cook batches it as its manager requires), then re-probes `list_installed` to derive each item's actual `installed`/`upgraded`/`unchanged` — so the report stays honest even when `latest` is unknowable cheaply.
- **`StateCook`** (desired-state resources): `items()`, `current() -> {name: token}`, `desired() -> {name: token}`, `hooks(name) -> (pre_hook, post_hook)`, and `apply_one(name) -> ItemOutcome`. Chef compares current vs desired and for each differing item runs `pre_hook` → `apply_one` → `post_hook`, firing hooks only when `apply_one` reports `changed`.

`Result(status, message)` carries `"ok" | "soft_fail" | "hard_fail"`; `ItemOutcome(changed, status, message)` is the per-item version. **Expected** failures return a status; only bugs raise (chef wraps each cook in `run_cook_guarded`). `CookResult` (cook name + status + `ItemReport` rows) is what travels back from a forked child, so it must stay picklable (plain dataclasses).

`debug_main(cls)` is the `__main__` entry for inspecting one cook by hand: it loads the section from `SYS_CONF_PY_SECTION_JSON`, enforces the euid contract (a `needs_root` cook refuses a non-root euid and vice-versa), and prints the cook's probe as a TOON table. In a real run chef imports the class and never spawns the script.

### Cooks

Each cook owns one recipe section (which may expand to several graph nodes). Behaviors that matter when editing:

- **`url_cook.py`** (`UrlCook`, versioned) — `[url.<name>]` `curl | bash` installers, one node per entry; presence-only (no version, so `latest` is `—`). Each node installs/upgrades its single entry, and chef runs the independent `url.*` nodes concurrently (so e.g. `cargo`'s `depends_on = ["url.rustup"]` waits only on rustup). Install failure → `hard_fail`; update failure → `soft_fail`. `needs_root = false`. `update_action`: list → `<bin> <args...>`; `"rerun-installer"` → re-pipe URL; absent → no update. The per-entry `update_guard` (herdr server-stop guard) runs before the upgrade and must be idempotent (it can fire on first install too) — it's a `url_cook`-internal guard, distinct from chef's StateCook `pre_hook`.
- **`bash_cook.py`** (`BashCook`, state) — generic shell executor for `[bash.<name>]`. `current()` runs each entry's `check_installed`; `desired` is the declared token meaning "done"; chef runs `install_or_update` only when they differ. `pre_hook`/`post_hook` are chef-owned hooks. Home of apt's runtime-computed setup steps (prereqs, debconf, the release-templated Ubuntu pin), so `needs_root = true`. Snippets use the `write-if-changed` CLI (installed by `[file.*]`), not a shim this cook generates.
- **`cargo_cook.py`** (`CargoCook`, versioned) — `[cargo].packages`; `sync` hands chef's whole set to one batched `cargo binstall --no-confirm …` (binstall does per-crate skip-if-current). Bootstraps `cargo-binstall` via a slow source compile if missing. `needs_root = false`.
- **`uv_cook.py`** (`UvCook`, versioned) — `[uv].packages`; `sync` runs `uv tool install` (to_install) / `uv tool upgrade` (to_upgrade) in parallel. `needs_root = false`.
- **`apt_repo_cook.py`** (`AptRepoCook`, state) — `[apt_repo.<name>]`, one subtable per repo (each its own node). Desired state "configured": GPG key under `/usr/share/keyrings/<name>.gpg` + `.sources` with `Signed-By:`. `current()` only checks the files exist, so a re-run does **no key fetch**. `needs_root = true`; `depends_on = ["bash.apt_prereqs"]` (just gnupg, not all of bash).
- **`apt_pkg_cook.py`** (`AptPkgCook`, versioned) — `[apt_pkg].packages` (single node). The one cook with a cheap real `latest` (`apt-cache policy` candidate). `sync` **ignores chef's split and always runs the full transaction** (`nala update` → policy check → `full-upgrade` → `install` → `autoremove`) because full-upgrade is system-wide; chef still derives per-package changes from before/after versions. Fails fast before `full-upgrade` if any package has priority 0. `needs_root = true`; `depends_on = ["bash", "apt_repo"]` (both fan out to every bash/repo node).
- **`snap_cook.py`** (`SnapCook`, versioned) — `[snap].packages` (firefox, chromium; apt names are transitional debs). `sync` installs/refreshes **sequentially** (snapd global lock). `latest` is `—`. Install failure → `hard_fail`, refresh → `soft_fail`. Strictly-confined only. `needs_root = true`. Snap confinement means a host VA-API driver isn't visible inside — this cook only installs.
- **`file_cook.py`** (`FileCook`, state) — `[file.<name>]`: install a file with exact content (`content` inline or `source` under `src/files/`) + `mode` + optional `pre_hook`/`post_hook`. Diffs by content hash; `post_hook` fires only when the file changed. Each entry is its own node with its own `depends_on`, so this one cook handles both the early files (the `write-if-changed` CLI, the `trusted.gpg.d` hardening hook, the `grub.d` deep-sleep drop-in — `depends_on = []`) and the driver-dependent ones (`egpu-prime-switch`/its unit/`nvidia-power.conf` — `depends_on = ["apt_pkg"]`). `needs_root = true`. **file_cook is for static, exact content**; runtime-computed or attribute-stateful work (the release-templated pin, `chattr +i`) stays in `[bash.*]` — the `chattr` is a `post_hook` on the hardening file entry.

### Per-app config cooks (`apps_config.toml`)

`configure_apps` was split by mechanism into three **user-scope** cooks (`needs_root = false` → forked + privilege-dropped, so they write into `$HOME` directly, no chown). All `depends_on = ["apt_pkg"]`. Each reads `src/apps_config.toml` (via the shared `apps_config.py` loader) and is a *diffable* state cook (desired = hash of rendered content vs on-disk):

- **`desktop_cook.py`** (`DesktopCook`) — per-user `.desktop` Exec= overrides (`env` prefix + `--<switch>`es + `--enable-features=`) for apps with a `desktop` marker. `kbuildsycoca6 --noincremental` is a chef-owned **`post_hook`**, firing only when a `.desktop` changed.
- **`chromium_flags_cook.py`** (`ChromiumFlagsCook`) — Chromium `Local State` flag union (apps with `local_state`) + Electron `argv.json` merge (apps with `argv_json`). The skip-while-running race-guard is a **`pre_hook`** (`pgrep` → non-zero → benign skip).
- **`settings_cook.py`** (`SettingsCook`) — merge an `env` block into a JSON settings file (apps with `settings_json`, e.g. `~/.claude/settings.json`).

### Static assets (`src/files/`)

Installed by `file_cook.py` (the `[file.*]` entries). Both Python scripts carry a `.py` source name (so they're linted/typechecked) but install to an extensionless command path:

- `egpu-prime-switch.py` → `/usr/local/sbin/egpu-prime-switch`, `egpu-prime.service` — the boot-time eGPU-primary selector + its systemd unit. The switch is a **standalone `/usr/bin/python3`, stdlib-only** script (no `harness`/`loguru`/uv imports — it runs as root before login, where none of that is importable). When the eGPU is on PCI it flips `boot_vga` onto it, writes `/etc/environment.d/10-egpu-primary.conf` (`KWIN_DRM_DEVICES` resolved to colon-free `/dev/dri/cardN` + `VULKAN_ADAPTER`), and selects `prime-select nvidia`; otherwise it removes that file and selects `on-demand`. A reboot drops the `boot_vga` bind-mount and the file is regenerated, so nothing goes stale.
- `write-if-changed.py` → `/usr/local/bin/write-if-changed` — a standalone stdlib reimplementation of `write_if_changed` for `[bash.*]` snippets (can't import `harness` on a clean system).

## Editing conventions specific to this repo

- **Cooks hold no idempotency/diff logic — chef decides.** A cook only probes (`list_installed`/`latest_available`, or `current`/`desired`) and acts (`sync` or `apply_one`). Don't gate work inside a cook with ad-hoc check-and-act; expose the state and let chef diff it. This is what keeps the report honest and the re-run cheap.
- **All file writes go through `write_if_changed`** so idle re-runs print `Unchanged:` and exit fast. Don't introduce a plain `path.write_text(...)`. In `[bash.*]` snippets, pipe through the `write-if-changed` CLI.
- **All subprocesses that produce log-worthy output go through `stream_subprocess`**; use `harness.run(...)` only for short utility calls whose stdout you capture programmatically.
- **`needs_root` / `depends_on` live in `recipe.toml`, per entry** (an entry overrides its section's default; both default to `false` / `[]`, so omit them at those values). The cook's `needs_root` class attr only mirrors the privilege for the `debug_main` euid guard. Chef owns privilege: root nodes run in-process, user nodes (`needs_root = false`) fork through `become_user`. Want different deps/privilege for two things? They must be different nodes — use separate `[file.<name>]` entries (or sections), not one entry doing both. `depends_on` should be **as precise as true**: `["url.rustup"]`, not `["url"]`, when only rustup is needed.
- **`find_binary` only from user-scope context.** Its bootstrap dirs follow `$HOME` (which `become_user` repoints in the forked child); calling it in the root parent probes `/root`.
- **Lifecycle hooks (`pre_hook`/`post_hook`) are chef-owned for state cooks** and fire only when an action is taken — put per-resource side effects (daemon-reload, update-grub, kbuildsycoca) in `post_hook`, not inside `apply_one`. `pre_hook` is a **guard**: non-zero exit skips the item benignly (e.g. the skip-while-browser-running race-guard), so don't use it for things whose failure should abort.
- **Repo configuration is data, not code**: a new package goes in `recipe.toml`, a new flag in `apps_config.toml`, a new system-config one-off in a `[bash.<name>]` or `[file.<name>]` entry. The Python files should rarely change when adding/removing tools.

## Recovery

`docs/investigations/` contains write-ups of past failures (e.g. `sleep-crash.md` — the basis for the `[file.grub_deep_sleep]` GRUB drop-in and the NVIDIA modprobe options in `[file.nvidia_power]`). Logs live in `logs/sys-conf-py-<timestamp>.log`, one per `just up` run, chowned to the invoking user.
