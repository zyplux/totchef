# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Declarative, idempotent Ubuntu/Kubuntu Wayland laptop config: apt repos + packages, eGPU auto-PRIME at boot, and Chromium/Electron GPU flags. Same script handles first-run bootstrap and ongoing upkeep — re-runnable; cooks only rewrite files whose contents would actually change.

## Commands

- `just up` — run the full configuration (`./src/chef.py`); prompts for sudo at the start.
- `just lint` — `ruff check --fix` then `ruff format`.
- `just tc` — lint then `uvx pyright src` (depends on `lint`).
- Markdown lint: `rumdl` (configured in `.rumdl.toml`; disables `MD033`/`MD013`).
- Requires Python ≥3.14 (`pyproject.toml`). The only runtime deps are `loguru` and `toon-format`; everything else is stdlib.
- There is **no test suite** — verification is done by re-running `just up` and confirming "Unchanged:" lines for everything you didn't intend to touch.

## Architecture

### Orchestrator → cook contract

`src/chef.py` is the entry point. It does three things, in order:

1. Reads `src/recipe.toml` and walks its **top-level sections in file order** (TOML preserves order). For each section `[foo]`, it spawns `src/foo_cook.py` as a **subprocess** (not an import — so a cook can `execvp` into `sudo` without taking down the orchestrator). The section's TOML slice is passed via the `SYS_CONF_PY_SECTION_JSON` env var.
2. After all recipe sections, runs `STANDALONE_PLAYBOOKS` unconditionally: `configure_gpu.py`, then `configure_apps.py`. These read their own config (static for GPU, `src/apps_config.toml` for apps) and aren't keyed off `recipe.toml`.
3. Exit-code contract: `0` = success, `75` (`SOFT_FAIL_EXIT`) = soft fail (continue, name the section in a final stderr banner), anything else = hard fail (abort `just up`). `chef.py` itself exits 75 if any section soft-failed.

**Adding a new tool category** means adding a `[newcategory]` section to `recipe.toml` *and* creating `src/newcategory_cook.py`. Missing cook → `chef.py` aborts with an error. File order in `recipe.toml` is execution order; `[apt]` is intentionally last because it re-execs under sudo and is the slowest.

### Shared scaffolding (`src/harness.py`)

Every cook imports from here. Key utilities:

- `reexec_under_sudo(script)` — if not root, `os.execvp` into `sudo` preserving `SYS_CONF_PY_LOG_FILE` + `SYS_CONF_PY_SECTION_JSON`. Used by cooks that need root (`apt_cook`, `configure_gpu`, `configure_apps`).
- `load_section()` — read the slice that `chef.py` passed via env. Always call this **before** `reexec_under_sudo` so a missing-env or JSON error surfaces before the sudo prompt.
- `start_log_tee()` — tees stdout/stderr into `logs/sys-conf-py-<timestamp>.log`. Honors `SYS_CONF_PY_LOG_FILE` if set (so all cooks in one run share one log file); pre-chowns log + dir to `SUDO_USER` so root-written lines keep the original owner.
- `stream_subprocess(cmd, ...)` — runs a child with merged stdout/stderr piped line-by-line through `loguru`. Forces `TERM=dumb` + `NO_COLOR=1` + `start_new_session=True` to strip ANSI and block `/dev/tty` bypass. Splits CR-overwrites into separate log lines. Use this, not `subprocess.run`, for anything whose output you want in the log.
- `write_if_changed(path, content, mode, note)` — the idempotency primitive: compare bytes, skip when equal, log `Unchanged:` vs `Writing  :`. Cooks should funnel **every** file write through this so re-runs stay quiet.
- `find_binary(name)` — `shutil.which` first, then `BOOTSTRAP_BIN_DIRS` (`~/.cargo/bin`, `~/.bun/bin`, `~/.local/bin`, `~/.claude/local`). Needed because `rustup` / `bun` / `uv` install into those dirs before they're on `PATH`. **Don't call after `reexec_under_sudo`** — `Path.home()` was resolved at import.
- `fetch_url(url)` — `urllib` with a custom `User-Agent: sys-conf-py` (some CDNs 403 the urllib default).

### Cooks

Each cook is a self-contained playbook with a documented soft/hard failure contract. Behaviors that matter when editing:

- **`bash_cook.py`** — `[bash.<name>]` entries; each is a `curl | bash` installer. Runs entries in parallel via `ThreadPoolExecutor`. Install failure = exit 1 (hard, downstream may depend on the tool); update failure = exit 75 (soft, tool stays usable). Refuses to run as root — installers write into `$HOME`. `update_action` semantics: list → `<bin> <args...>`; `"rerun-installer"` → re-pipe URL; absent → no update.
- **`cargo_cook.py`** — `[cargo].packages`; one batched `cargo binstall --no-confirm pkg1 pkg2 …` (binstall does its own parallel resolution + per-crate skip-if-current). Bootstraps `cargo-binstall` via a slow source compile if missing. Refuses root.
- **`uv_cook.py`** — `[uv].packages`; parallel `uv tool install` / `uv tool upgrade`, with the install/upgrade decision driven by a single up-front `uv tool list` parse. Refuses root.
- **`apt_cook.py`** — heaviest cook. `load_section()` pre-sudo, then `reexec_under_sudo`. Drives `nala` (parallel downloads + `nala history undo`). Idempotent third-party repos: GPG key under `/usr/share/keyrings/<name>.gpg` + `.sources` file with `Signed-By:`. Hardening: `chattr +i` on `/etc/apt/trusted.gpg.d/` to block legacy installer scripts; a DPkg::Pre/Post-Invoke hook unlocks it around dpkg runs so `do-release-upgrade` still works. Cross-repo safety: an Ubuntu pin file upranks Ubuntu archives to priority 900 so a name-colliding third-party package loses to Ubuntu by default. Fails fast before `full-upgrade` if any requested package has `apt-cache policy` priority 0 (not available in any configured repo).

### Standalone playbooks

- **`configure_gpu.py`** — installs `/usr/local/sbin/egpu-prime-switch` and `/etc/systemd/system/egpu-prime.service` (the service picks `prime-select nvidia` if the eGPU is on PCI, else `on-demand`, before SDDM starts at boot). Also writes `/etc/modprobe.d/nvidia-power.conf` and adds `mem_sleep_default=deep` to `GRUB_CMDLINE_LINUX_DEFAULT` — both mitigate the s2idle / NVIDIA suspend crash documented in `docs/investigations/sleep-crash.md`.
- **`configure_apps.py`** — reads `src/apps_config.toml`. Dispatches per app section on which marker key(s) are present: `desktop` (per-user `.desktop` override under `~/.local/share/applications/` with `env` prefix + `--<switch>`es + `--enable-features=`), `local_state` (Chromium-family `Local State` JSON merge for `brave://flags`-style UI mirroring — **skipped if the target browser is running** to avoid racing the write), `argv_json` (Electron-style allowlisted flag merge, e.g. VS Code), `settings_json` (merge an `env` block into a JSON settings file, e.g. `~/.claude/settings.json`). If any `.desktop` was rewritten, `kbuildsycoca6 --noincremental` runs as the invoking user — without it, KDE's launcher keeps spawning apps with the previously-cached `Exec` line.

### Static assets (`src/files/`)

Installed verbatim by the cooks above:
- `ubuntu-archives.pref` — template for the `apt-preferences` pin (Ubuntu archives → 900).
- `99-trusted-gpgd-autounlock` — DPkg pre/post hook to auto-unlock the immutable `trusted.gpg.d`.
- `egpu-prime-switch`, `egpu-prime.service` — the boot-time PRIME selector + its systemd unit.

## Editing conventions specific to this repo

- **All file writes go through `write_if_changed`** so idle re-runs print `Unchanged:` and exit fast. Don't introduce a plain `path.write_text(...)` — it breaks the "rerun is cheap" invariant.
- **All subprocesses that produce log-worthy output go through `stream_subprocess`** so they land in the tee'd log with consistent formatting; use `harness.run(...)` only for short utility calls whose stdout you'll capture programmatically.
- **`load_section()` before `reexec_under_sudo`**, always — surface config errors before prompting for a password.
- **Don't call `find_binary` after `reexec_under_sudo`** — `BOOTSTRAP_BIN_DIRS` was resolved against the *invoking* user's `$HOME` at import; under sudo, `Path.home()` would point at `/root`.
- **User-writable tools refuse root** (`bash_cook`, `cargo_cook`, `uv_cook`). If you add a new user-scope cook, mirror that guard — toolchains landing under `/root` is a silent footgun.
- **Repo configuration is data, not code**: a new package goes in `recipe.toml`, a new flag in `apps_config.toml`. The Python files should rarely change when adding/removing tools.

## Recovery

`docs/investigations/` contains write-ups of past failures (e.g. `sleep-crash.md` — the basis for the GRUB + modprobe NVIDIA tuning in `configure_gpu.py`). Logs live in `logs/sys-conf-py-<timestamp>.log`, one per `just up` run, chowned to the invoking user.
