# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

- `just up` — full run: prompts for sudo once, then executes every loader (`src/main.py`).
- `just lint` — `ruff check --fix` then `ruff format`.
- `just tc` — lint, then `uvx pyright src` (type check uses the in-repo `.venv` per `pyproject.toml`).

Python 3.14, dependencies pinned in `uv.lock`. Loaders run via `sys.executable`; `main.py` itself uses the `#!/usr/bin/env -S uv run` shebang. There is no test suite — correctness is validated by re-running `just up` on a real system and observing that already-correct state logs `Unchanged: …` instead of rewriting.

## Architecture

### Orchestrator → loader dispatch

`src/main.py` reads `src/install.toml`, then for each top-level `[section]` (file order = execution order) **spawns `src/<section>.py` as a subprocess** with the section's TOML slice serialized into the `SYS_CONF_PY_SECTION_JSON` env var. Subprocesses (not imports) are deliberate: loaders that need root call `harness.reexec_under_sudo()` and `os.execvp` into `sudo` — under an import model, that would take down the orchestrator.

After install.toml sections, `STANDALONE_PLAYBOOKS` (`configure_gpu.py`, `configure_apps.py`) run unconditionally. These are **not** driven by `install.toml`; `configure_apps.py` reads its own `src/apps_config.toml`, `configure_gpu.py` reads static files from `src/files/`.

A section in `install.toml` with no matching `src/<name>.py` is a hard error.

### Exit-code contract

- `0` — success
- `75` (`SOFT_FAIL_EXIT`, sysexits `EX_TEMPFAIL`) — recoverable failure; `main.py` keeps going and prints a final stderr banner listing soft-failed sections, then itself exits 75.
- anything else — hard failure; `main.py` aborts immediately.

A loader chooses soft vs. hard based on downstream impact. Example (`bash.py`): install failure → hard (downstream sections may depend on the tool); update failure → soft (the tool is still on disk and usable).

### Shared scaffolding: `src/harness.py`

Every loader leans on this module — read it once before changing any loader:

- `load_section()` — parse the JSON slice `main.py` injected via `SYS_CONF_PY_SECTION_JSON`. Refuses to run if env var is missing (i.e. loader invoked directly instead of via `just up`).
- `reexec_under_sudo(SCRIPT)` — if not root, `execvp` into `sudo` preserving `SYS_CONF_PY_LOG_FILE` and `SYS_CONF_PY_SECTION_JSON`.
- `start_log_tee()` — dup stdout/stderr through `tee -a logs/<timestamp>.log`. Honours `SHARED_LOG_ENV` so every loader in one `just up` writes to the same file; pre-chowns the log to `SUDO_USER`.
- `stream_subprocess(cmd, tag=…, …)` — line-streamed `Popen` whose merged stdout/stderr flows through `logger.info`. Sets `TERM=dumb`, `NO_COLOR=1`, and `start_new_session=True` to suppress ANSI and block `/dev/tty` bypass; splits `\r` so progress-bar redraws become separate log lines. **Use this, not `subprocess.run`, for any user-visible external command.**
- `write_if_changed(path, content, mode=…, note=…)` — the project's idempotency primitive. Writes only when content differs and logs either `Writing  : <path>` or `Unchanged: <path>`. Returns True on change so callers can chain consequences (e.g. `systemctl daemon-reload`, `update-initramfs -u`).
- `find_binary(name)` — PATH first, then `BOOTSTRAP_BIN_DIRS` (`~/.cargo/bin`, `~/.bun/bin`, `~/.local/bin`, `~/.claude/local`) — these hold tools that rustup/bun/uv install pre-PATH on a fresh box. Don't call after a sudo re-exec; `Path.home()` was resolved at import time.
- `fetch_url(url)` — `urllib` with `User-Agent: sys-conf-py`. The custom UA is load-bearing — Signal's/herdr's CDNs 403 the urllib default.

### Root policy

Two camps, applied per-loader:

| Loader | Writes to | Runs as |
|---|---|---|
| `bash.py`, `cargo.py`, `uv.py` | `$HOME` | invoking user (refuses root — toolchains would land under `/root`) |
| `apt.py`, `configure_gpu.py`, `configure_apps.py` | `/etc`, `/usr/local`, systemd | re-execs under sudo via `harness.reexec_under_sudo()` |

`configure_apps.py` is the interesting case: it runs as root (writes touch `/etc`-adjacent areas) but explicitly `chown`s user-owned destinations (`~/.local/share/applications`, `~/.config/.../Local State`, `~/.vscode-insiders/argv.json`, `~/.claude/settings.json`) back to `SUDO_USER` after writing. Use `get_invoking_user()` for the uid/gid/home triple.

### Declarative-config dispatch

- `src/install.toml` header is authoritative for section semantics and field meanings — read it before adding entries. Each top-level `[section]` maps 1:1 to `src/<section>.py`; subtables like `[apt.repo.<name>]` and `[bash.<cli>]` carry their identifier in the header.
- `src/apps_config.toml` is consumed only by `configure_apps.py`. Sections are dispatched on **marker keys**: `desktop` → `.desktop` launcher override, `local_state` → Chromium Local State patch, `argv_json` → Electron argv.json merge, `settings_json` → JSON settings `env`-block merge. A section without any marker key is skipped (this is how `[chromium]` and `[env]` stay as shared config rather than being treated as apps).
- `src/files/` ships static assets installed verbatim (apt hook, Ubuntu pin template, egpu-prime systemd unit + switch script).

### `[apt]` is intentionally last in `install.toml`

`apt.py` is the only loader that re-execs sudo *and* does the heavy lifting (full-upgrade, repo install, package install, autoremove). Earlier sections (`bash`, `cargo`, `uv`) bootstrap user-space tools first so the apt run can fail loudly without leaving the box half-configured. If you add a new section, think about whether it needs to run before or after `[apt]` and place it accordingly in the file.

### Domain landmines already mitigated (don't re-introduce)

These are recorded in the source as inline rationale; check before "simplifying":

- **NVIDIA driver branch jumps:** Ubuntu rebuilds retired `nvidia-driver-*` metapackages as transitional shims that depend on a newer branch. Mitigation lives in `apt.py` + `install.toml` — every other `nvidia-driver-*` is pinned to priority `-1`. Don't remove the pin file generation.
- **`/etc/apt/trusted.gpg.d` is chattr +i:** with a `DPkg::Pre/Post-Invoke` hook that unlocks it around dpkg runs. This blocks legacy install scripts from dropping global-trust keys while keeping `do-release-upgrade` working. The hook source is `src/files/99-trusted-gpgd-autounlock`.
- **`apt-cache policy` priority 0 = "no candidate":** `apt.py` fails fast before `full-upgrade` if any requested package is unreachable (typo, missing repo, release-codename rename like `libva-nvidia-driver` → `nvidia-vaapi-driver`). The error message lists the common causes — preserve it.
- **KDE `ksycoca` cache:** when a `.desktop` file changes, `configure_apps.py` runs `kbuildsycoca6 --noincremental` via `runuser`. Without this, KDE's launcher keeps spawning apps with the previously-cached `Exec=` line. The "desktop changed" bit is tracked separately from "anything changed" precisely so we only pay the rebuild when needed.
- **VS Code `code-insiders` postinst rewrites the apt repo:** `[apt.debconf.code-insiders]` answers `code-insiders/add-microsoft-repo false` so our `[apt.repo.vscode]` survives package upgrades.
- **Suspend / s2idle kernel oopses on this Tiger Lake + NVIDIA hybrid:** `configure_gpu.py` forces deep S3 via GRUB `mem_sleep_default=deep` and pins NVIDIA modprobe options (`NVreg_PreserveVideoMemoryAllocations=1`, `NVreg_DynamicPowerManagement=0x00`, `NVreg_EnableS0ixPowerManagement=0`). See `docs/investigations/sleep-crash.md` before touching those.

## Conventions

- **Idempotency is the contract.** Every state-changing action must be safe to run twice. Use `write_if_changed` for files; check `systemctl is-enabled`, `lsattr`, `apt-cache policy`, etc. before mutating. Log `Unchanged: <thing>` on no-ops — re-runs should be quiet.
- **Choose `stream_subprocess` over `subprocess.run`** when a user should see the command's output in real time (apt, nala, systemctl, installer scripts). Reserve `subprocess.run` for short capturing calls (policy parsing, state probes).
- **`logger.info` for every state-changing step** with a short note (`note=...`). The log file is what the user reads when something goes sideways; silent success is harder to debug than verbose success.
- **Status tables → TOON, not ad-hoc text.** `toon_format.encode(rows)` renders policy/state summaries (see `apt.py:build_policy_row` and `configure_gpu.py:build_gpu_state_row`). Stay consistent.
