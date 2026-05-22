# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

| Command | What it does |
|---|---|
| `just up` | Full system setup. Reads `install.toml`, dispatches to per-section loaders, then runs standalone playbooks. The only user-facing entry point. |
| `just lint` | `ruff check --fix && ruff format`. |
| `just tc` | Runs `lint` then `uvx pyright src`. |

There are no tests in this repo.

## Architecture

**Single entry point.** `just up` runs `./src/main.py`, which sets `SYS_CONF_PY_LOG_FILE`, primes sudo, reads `install.toml`, then for each top-level section spawns `src/<section>.py` as a subprocess with the section's slice serialized to JSON in `SYS_CONF_PY_SECTION_JSON`. Section order in `install.toml` IS the execution order:

1. `[bash.*]` → `bash.py` — vendor `curl | bash` CLIs (user)
2. `[cargo]` → `cargo.py` — cargo-binstall packages; bootstraps cargo-binstall on fresh systems via `cargo install`, then self-hosts via the same TOML (user)
3. `[uv]` → `uv.py` — uv tools (user)
4. `[apt]` → `apt.py` — apt packages, repos, debconf, pinning (re-execs under sudo)

After `install.toml` sections run, `main.py` runs the standalone playbooks (no `install.toml` section, fixed list in `STANDALONE_PLAYBOOKS`):

5. `configure_gpu.py` — egpu-prime systemd service (re-execs under sudo)
6. `configure_apps.py` — `apps_config.toml` (re-execs under sudo)

**Loader discovery by name.** `main.py` resolves `[section]` → `src/<section>.py` purely by filename — no hardcoded section list. Adding a new section is a two-file change: add a `[<name>]` to `install.toml`, write `src/<name>.py`. No orchestrator edit needed.

**Loaders read slices from env, not files.** Each loader calls `load_section()` (from `harness.py`) which reads `SYS_CONF_PY_SECTION_JSON` and returns the parsed dict. Loaders never open `install.toml` themselves — `main.py` owns the file. The env var survives sudo re-exec via `sudo --preserve-env=SYS_CONF_PY_LOG_FILE,SYS_CONF_PY_SECTION_JSON` in `reexec_under_sudo`.

**Why subprocesses, not imports.** Each root-requiring loader calls `reexec_under_sudo(SCRIPT)` which runs `os.execvp("sudo", [..., sys.executable, str(script), ...])`. `execvp` *replaces* the calling process. If `main.py` imported and called the loader functions, the orchestrator would be gone after the first sudo step. Subprocesses isolate the execvp to each child.

**Shared log file via env var.** `SYS_CONF_PY_LOG_FILE` is set once in `main.py` (`os.environ.setdefault`), then propagates through every spawned subprocess and *across the sudo boundary* via `--preserve-env`. Each loader/playbook calls `start_log_tee()` which `tee -a`s into that file. One `just up` run = one consolidated log under `logs/`, chowned to `SUDO_USER`.

**Subprocess output → loguru.** Use `stream_subprocess(cmd, ...)` from `harness.py` for any external command whose output you want logged. It runs with `TERM=dumb`, `NO_COLOR=1`, and `start_new_session=True`, captures merged stdout/stderr, splits CR-overwrites into separate frames, and routes each line through `logger.info`. `start_new_session=True` is what blocks nala from bypassing the pipe by opening `/dev/tty`. Don't reach for raw `subprocess.run` for streamable work — only for short capture-output calls (e.g. `gpg --dearmor`).

**Idempotency by file diff.** `write_if_changed(path, content)` in `harness.py` only writes when bytes differ; logs `Unchanged: <path>` on no-op. Re-running `just up` is cheap by design — every system-file write should go through this.

**User vs root loaders.** Scripts that refuse root (`sys.exit(...)` when `os.geteuid() == 0`) install per-user tools — cargo/uv/bun lands in `$HOME` and would land in `/root` if run as root. Scripts that *require* root call `reexec_under_sudo(SCRIPT)` at the top of `main()`. Don't mix the two roles in one loader. Section order in `install.toml` must put user-scoped sections before root-scoped ones; today's order (bash → cargo → uv → apt) is load-bearing.

**Vendor installer dispatch (`[bash.<name>]`).** Each subtable: if `bin` (default: subtable name) is missing, fetch `url` and pipe into bash with optional `args`; if present, dispatch on `update_action` — a list runs `<bin> <update_action...>`, the literal string `"rerun-installer"` re-fetches and re-pipes. Absent `update_action` means leave as-is.

## Conventions

- **Python 3.14+** (`requires-python = ">=3.14"` in `pyproject.toml`). Stdlib `tomllib` for TOML, match statements, etc. — no `tomli` or other backports.
- **Dependencies in `pyproject.toml`**, project venv at `.venv`, uv-managed. Loader scripts have **no PEP 723 inline metadata** and **no shebang** — they're spawned by `main.py`, never run directly. `main.py` is the only file with `+x` and a shebang (`#!/usr/bin/env -S uv run`).
- **Pyright config in `pyproject.toml`** under `[tool.pyright]` with `venvPath = "."` / `venv = ".venv"`. Required so `uvx pyright src` (run from an ephemeral uvx env) finds project deps.
- **`from harness import ...`** works in loaders because Python adds the script's directory (`src/`) to `sys.path[0]` on direct execution; `main.py` and the loaders all live in `src/` and share the same import resolution.
- **Static assets live in `src/files/`** — copied verbatim by loaders. Edit those files directly rather than embedding their content in Python strings.

## Adding a new install.toml section

1. Pick a section name; this also becomes the loader filename. The section name should name the *installer tool* (`apt`, `cargo`, `uv`, `bash`).
2. Add `[<name>]` (or `[<name>.<item>]` subtables) in `install.toml`, in the position that respects user-vs-root ordering — user-scoped before root-scoped.
3. Create `src/<name>.py` with **no shebang, no `+x` bit, no PEP 723 block**.
4. Import from `harness`: typically `load_section`, `start_log_tee`, `stream_subprocess`, `write_if_changed`, and `reexec_under_sudo` if root is needed.
5. In `main()`: if root-required, call `load_section()` first (so a missing env or malformed JSON surfaces pre-sudo), then `reexec_under_sudo(SCRIPT)`, then `start_log_tee()`. If user-only, `sys.exit(...)` early when `os.geteuid() == 0`, then `load_section()`, then `start_log_tee()`.

## Adding a standalone playbook (no install.toml section)

For one-off configuration that doesn't fit the "list of items" shape (e.g. `configure_gpu`, `configure_apps`):

1. Create `src/<name>.py` (no shebang, no `+x`, no PEP 723).
2. Reads its own config from disk (e.g. `apps_config.toml`) or no config at all.
3. Append the filename to `STANDALONE_PLAYBOOKS` in `src/main.py`.
