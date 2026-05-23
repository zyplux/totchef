# Cook refactor — Phase 1 report

**Status:** implemented; lint + typecheck clean. Live `just up` run unverified (needs real sudo + system).

## Done

- **`cook_base.py`** (new): `Result(status, message, changed)` + `VersionInfo` frozen dataclasses; `CookBase` with `install_or_update() -> Result` and `show_version() -> list[VersionInfo]`; `main_for(cls)` entry point (load section → euid guard → log tee → run → map status to exit code; `--show-version` debug flag).
- **All cooks are classes** on `CookBase`, behavior preserved:
  - `UvCook`, `CargoCook` — same thread-pool / batch strategy as before.
  - **bash split:** `url_cook.py` (`UrlCook`, the old `curl|bash` installer cook renamed) + `bash_cook.py` (`BashCook`, new generic shell executor).
  - **apt split:** `apt_repo_cook.py` (repos + keys) + `apt_pkg_cook.py` (policy check, full-upgrade, install, autoremove). Old `apt_cook.py` deleted.
- **`recipe.toml`** restructured: `[url.*]`, `[bash.*]`, `[cargo]`, `[uv]`, `[apt_repo.*]`, `[apt_pkg]`, `[configure_gpu]`, `[configure_apps]`. Each top-level section declares `needs_root` + `depends_on`.
- **`chef.py`**: `graphlib.TopologicalSorter` walk (file order = tiebreaker); strips `needs_root`/`depends_on` before passing the slice; chef owns sudo (spawns root cooks under `sudo`, refuses non-root cook as root); resolves `<section>_cook.py` then `<section>.py`. `STANDALONE_PLAYBOOKS` removed.
- **apt weird-stuff → `[bash.*]`**: `trusted_gpgd_hardening`, `ubuntu_pin`, `code_insiders_debconf`, `apt_prereqs`. Rationale kept as comments. `src/files/ubuntu-archives.pref` + `99-trusted-gpgd-autounlock` inlined as heredocs and deleted.
- **`write-if-changed` shim**: `bash_cook` drops a CLI on `$PATH` wrapping `harness.write_if_changed` so heredoc writes stay idempotent.
- **Cleanup**: `reexec_under_sudo` removed from `harness.py` + both `configure_*`; `CLAUDE.md` + `README.md` updated.

## Verified

- ruff (check + format), pyright (0 errors), rumdl — clean.
- Topo plan valid; all hard deps hold: `url`→`cargo`/`uv`; `bash`→`apt_repo`; `bash`+`apt_repo`→`apt_pkg`; `apt_pkg`→`configure_*`.
- `write-if-changed` shim end-to-end (`Writing` → `Unchanged`, single-line logs).
- `BashCook` outcome→`Result` mapping (ok / soft_fail / hard_fail) and `show_version`.

## Not verified / notes

- **Live `just up` unverified** — apt `[bash.*]` snippets as root, sudo-spawn dispatch, quiet re-run (`Unchanged:`) all need a real box.
- **Ordering shift:** `bash` runs 2nd (graphlib yields ready batch `[url, bash]` in insertion order), not last. Safe — independent of `cargo`/`uv` — but observable.

## Deferred (per plan)

- Phase 2: chef imports cooks, owns concurrency, `--dry-run` wired to `show_version`.
- `configure_gpu`/`configure_apps` internals still procedural (Phase 5).
- `cook_base.py` chosen over `harness.py` for the base class (§13 q1 resolved).
