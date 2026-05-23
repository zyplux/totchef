# Cook refactor — Plan

> **Status:** brainstorming — readiness 95% — ready to break into implementation tasks
> **Last updated:** 2026-05-23
> **Walking skeleton:** Convert `uv_cook` to a class with `install_or_update` and `show_version`. Add `needs_root` and `depends_on` fields to recipe.toml; chef reads both and topo-sorts. Chef owns sudo. No splits in the walking skeleton itself — those land Phase 1.2 once the class shape is proven on the cleanest cook.

## 1. Vision

This refactor is **Phase 1** of a multi-phase shift to a **chef-as-scheduler** architecture. The end-state:

- `recipe.toml` is the source of truth for **every** operation — installing a package, writing a system file, hardening a directory, running a shell incantation. Each operation is a section with declared `depends_on` and `needs_root`.
- `chef.py` reads recipe.toml, builds a DAG via `graphlib.TopologicalSorter`, owns all concurrency, owns all sudo elevation, and drives the per-section idempotency loop.
- Cooks are narrow worker classes — one section type per file, synchronous methods, no business logic in `main()`.
- A guiding principle: **bash one-liners over Python where possible.** If an operation can be expressed as `install_or_update` / `show_version` / `pre_update` / `post_update` bash one-liners, it lives in recipe.toml under `[bash.<name>]` — no dedicated Python cook needed. Dedicated `*_cook.py` files exist only when the operation genuinely doesn't fit bash one-liners (e.g. needs structured TOML output parsing, complex idempotency logic, or non-trivial state probing).

**Phase 1 covers:** all cook classes, `needs_root` + `depends_on` recipe fields, chef-owned sudo, the bash/apt section splits, migration of apt's "weird stuff" to declarative recipe entries (mostly `[bash.<name>]`), and folding `configure_gpu` / `configure_apps` into the recipe graph.

## 2. Problem & motivation

- **Idempotency, ordering, concurrency, and elevation are scattered across cook code.** Each cook re-invents its own logic for "is it installed", its own ThreadPoolExecutor, its own `reexec_under_sudo` call. Chef can't own any of these uniformly until cooks expose a contract that lets it.
- **Today's chef ordering is implicit** (file order in recipe.toml). Adding the bash/apt splits — where `[apt_pkg]` must run after `[apt_repo.*]` — needs a real ordering primitive. Hard-coding the order in chef means every new cook is a chef.py edit; `depends_on` keeps the ordering as data alongside the sections it constrains.
- **No verb for "what's installed at what version".** `show_version` adds one. Phase 1 lands the *method*; Phase 2 wires it to a chef CLI for dry-run audit.
- **`apt_cook` and today's `bash_cook` do multiple jobs in one file.** The class-per-section + uniformity rule forces splits.
- **`apt_cook`'s "weird stuff" — debconf, Ubuntu pinning, `trusted.gpg.d` hardening, prereq install — is Python code expressing what are essentially shell operations.** With the new `bash_cook` generic-shell-runner model, this stuff becomes declarative `[bash.<name>]` entries instead. Three of four items are clean wins (single-line shell); the file-write items (Ubuntu pin, hook file) need a templating mechanism — see §13 q2.

## 3. Users & primary scenarios

- Primary user: the maintainer (sergiy), running `just up` on Ubuntu/Kubuntu laptops.
- Key scenarios:
  - **Add a new package source category.** Subclass the cook base; declare `needs_root` and `depends_on` in recipe.toml; fill in two methods.
  - **Add a system-config one-off** (e.g. a sysctl tweak, a systemd timer). Add a `[bash.<name>]` entry with `install_or_update` and `show_version` one-liners. No Python.
  - **Dry-run "what versions are on this box?"** (Phase 2: chef calls `show_version` across all cooks; aggregates into a TOON table. Phase 1: method exists, individual cook's `main()` is the caller.)
  - **Debug a single cook in isolation.** `python src/uv_cook.py` still works directly.

## 4. Goals

- **Common public surface across all cook classes:** `install_or_update(...)` and `show_version() -> list[VersionInfo]`.
- **Synchronous class methods.** Concurrency lives in script-level `main()` in Phase 1; moves to chef in Phase 2.
- **API shape is import-ready.** Chef.py can `from uv_cook import UvCook` in Phase 2 without API churn.
- **Declarative ordering and privilege.** `depends_on` and `needs_root` in recipe.toml; chef reads both.
- **Chef owns sudo elevation.** Cooks lose `reexec_under_sudo`. Chef spawns sudo subprocesses for `needs_root: true` sections; refuses to launch `needs_root: false` cooks under root.
- **Bash one-liners over Python where possible.** Every system-config operation that fits the `[bash.<name>]` schema lives there, not in a Python cook.
- **`just up` behavior preserved.** Same outputs, same exit codes, same `Unchanged:` quiet re-runs.

## 5. Non-goals (current scope)

- **No chef-driven dry-run command** yet. `show_version` exists but isn't wired to a CLI entry.
- **No conversion of `configure_gpu.py` / `configure_apps.py` internals to the new class shape.** They get declared in recipe.toml with `needs_root` + `depends_on` (so chef dispatches them via the graph), but their internal logic stays as-is for Phase 1. The `STANDALONE_PLAYBOOKS` list goes away; chef treats them like every other graph node. Internal refactor to classes happens Phase 5+.
- **No new runtime dependencies.** Stays stdlib + `loguru` + `toon-format`.
- **No test suite added.** Verification stays "re-run `just up` and watch for unexpected `Writing :` lines."
- **No behavior change in cook output.** Same lines logged, same exit codes.
- **No chef-driven idempotency loop.** Each cook's `install_or_update` still does its own probing internally; chef doesn't yet drive (probe → install → update). That's Phase 3.

## 6. Constraints

- Python ≥3.14.
- Subprocess spawn boundary between chef.py and cooks is **preserved in Phase 1.** Sudo elevation moves to chef but still works by spawning a sudo subprocess running the cook script.
- `load_section()` before cook does anything else that could fail.
- `find_binary()` only pre-sudo (its `BOOTSTRAP_BIN_DIRS` bind to invoking user's `$HOME` at import).
- All file writes through `write_if_changed`. All log-worthy subprocesses through `stream_subprocess`. No drift.
- `recipe.toml` order: `depends_on` declares hard order constraints; file order is the tiebreaker via `TopologicalSorter`'s stable sort.
- Section name → `*_cook.py` filename. For named-subtable sections (`[apt_repo.brave]`, `[url.bun]`), the *parent* section name maps to the cook file (`apt_repo_cook.py`, `url_cook.py`); the subtable name is the entry identifier within the section.
- No test suite; verification is re-running `just up`.

## 7. Functional requirements

### The cook class contract

- [DECIDED] Each cook is a class. The file's `main()` collapses to "construct, call methods, handle exit codes."
- [DECIDED] Class-per-section, one instance per `[section]` slice. Per-item looping is internal to the class.
- [DECIDED] A section is either **uniform** (every item takes the same code path) or **a single batch-tool wrapper** (one underlying command operating on a list).
- [DECIDED] Public method: `install_or_update(...) -> Result` — does today's behavior. Loops internally for uniform sections; issues the native batch call for batch-tool sections. Returns a `Result(status, message, changed)`; never raises for *expected* failures. Unexpected exceptions (bugs) still propagate. `main()` (Phase 1) / chef (Phase 2) inspects the `Result` and maps `status` to an exit code.
- [DECIDED] Public method: `show_version() -> list[VersionInfo]` — read-only; no side effects; always returns a list.
- [DECIDED] `Result` is a small dataclass: `status: Literal["ok", "soft_fail", "hard_fail"]`, `message: str`, `changed: bool`. `changed` lets chef report "what changed this run" in Phase 2. (Round 5 Interaction 3 option 2.)
- [DECIDED] Class methods are **synchronous**. Concurrency is the caller's responsibility.

### Recipe.toml schema additions

- [DECIDED] New field per top-level section: `needs_root` (bool, default false).
- [DECIDED] New field per top-level section: `depends_on` (list of section names, default `[]`).
- [DECIDED] Chef reads both; builds a DAG using `graphlib.TopologicalSorter`; spawns each cook accordingly.
- [DECIDED] Chef owns sudo elevation. `needs_root: true` → chef spawns via `sudo`. `needs_root: false` and already root → chef refuses with a clear error.
- [DECIDED] `depends_on` is section-level for Phase 1. Per-entry granularity within named-subtable sections deferred (Phase 3, when chef drives per-item idempotency).
- [DECIDED] `depends_on` defaults to `[]` (no implicit constraint). File order is purely a tiebreaker when the graph admits multiple valid orderings. Existing recipe.toml needs explicit `depends_on` backfilled — trivial; only 4 sections today.

### The splits

- [DECIDED] `[bash]` → `[url.<name>]` (URL installers) + `[bash.<name>]` (generic shell executor; see schema below).
- [DECIDED] `[apt]` → `[apt_repo.<name>]` per third-party repo + `[apt_pkg]` for the package install batch.
- [DECIDED] Apt's "weird stuff" migrates to `[bash.<name>]` entries (debconf, prereqs, hardening, Ubuntu pin) wherever bash one-liners suffice. Items needing template files from `src/files/` need a referencing mechanism — see §13 q2.

### `[url.<name>]` schema (the renamed today's `bash_cook`)

- `url: str` (required) — installer URL, piped to `bash`
- `bin: str` (optional, default = subtable name) — probe binary
- `args: list[str]` (optional, default `[]`) — appended after `bash -s --`
- `update_action: list[str] | "rerun-installer"` (optional, absent = no update)
- `pre_update: str` (optional) — bash one-liner run before update_action

### `[bash.<name>]` schema (the new generic shell executor)

- `install_or_update: str` (required) — bash snippet (may be multi-line, ~up to 5 lines). Must be idempotent (check + act, or act on something natively idempotent). May inline file content via heredoc for small files (the migrated `ubuntu-archives.pref` and `99-trusted-gpgd-autounlock` are small enough once their comments collapse to one line).
- `check_installed: str` (optional) — bash snippet that queries current state. `show_version()` runs it and wraps stdout into a `VersionInfo`. In Phase 1 nothing branches on its output, so the contract is loose: emit something reasonable (a version string, or `present`/`absent`).
- `pre_update: str` (optional) — bash snippet run before `install_or_update`. Non-zero exit aborts (soft fail).
- `post_update: str` (optional) — bash snippet run after `install_or_update`. Non-zero exit aborts (soft fail).
- Snippets run via `stream_subprocess(["bash", "-c", snippet])` so output lands in the log. `$PATH` carries the invoking environment, plus the `write-if-changed` helper (below).
- **Idempotency via a shell helper.** `bash_cook` exposes a `write-if-changed` CLI on `$PATH` for snippets that write files. It reads content from stdin, takes the destination path (and optional mode) as args, and wraps the existing `harness.write_if_changed` primitive — so there's a single source of truth for the "compare bytes, skip if equal, log `Unchanged:` vs `Writing :`" behavior. A pin-file snippet becomes `… | write-if-changed /etc/apt/preferences.d/ubuntu-archives.pref`. Quiet re-runs preserved; the documented invariant holds. Most natural form: a small Python shebang script wrapping `harness.write_if_changed` (final language/packaging an impl detail).

### `VersionInfo`

- `@dataclass(frozen=True)`, fields: `name`, `installed_version`, `available_version`, `source`, `status` (`Literal["installed", "needs_update", "missing", "unknown"]`), `cook`, `manager`.

### `configure_gpu` / `configure_apps`

- [DECIDED] Declared in recipe.toml as `[configure_gpu]` and `[configure_apps]` with `needs_root = true` and `depends_on = ["apt_pkg"]`.
- [DECIDED] `STANDALONE_PLAYBOOKS` constant in chef.py goes away.
- [DECIDED] Internal refactor to the class shape happens later (Phase 5). For Phase 1 they just participate in the graph + privilege dispatch.

## 8. Walking skeleton (v1 / MVP)

Convert **`uv_cook` first**. Walking skeleton scope:

- `UvCook` class with `install_or_update` and `show_version`.
- Add `needs_root = false` and `depends_on = [...]` to `[uv]` in recipe.toml.
- chef.py grows: TopologicalSorter walk + sudo dispatch + `STANDALONE_PLAYBOOKS` removal.
- Add `needs_root` + `depends_on` to *all* existing top-level sections (`[bash]`, `[cargo]`, `[uv]`, `[apt]`) so chef's graph builder doesn't crash on missing fields. Other cooks remain as-is for now.
- Cook stops calling `reexec_under_sudo` (uv_cook never did; the pattern in chef is what gets validated, using `[apt]`'s needs_root=true).

If the experiment lands:
- **Phase 1.1:** Convert `cargo_cook`. Two cooks share scaffolding — extract base class.
- **Phase 1.2:** Land the bash split (`[url.*]` + `[bash.*]`). `url_cook.py` is mostly today's `bash_cook.py` renamed and class-ified; `bash_cook.py` is new (generic shell executor).
- **Phase 1.3:** Land the apt split (`[apt_repo.<name>]` + `[apt_pkg]`).
- **Phase 1.4:** Migrate apt's weird stuff to `[bash.<name>]` entries.
- **Phase 1.5:** Declare `[configure_gpu]` and `[configure_apps]` in recipe.toml; remove `STANDALONE_PLAYBOOKS`.

## 9. Architecture sketch

Phase 1 (this refactor):

- Each `*_cook.py` defines a class with `install_or_update` and `show_version`.
- File's `main()`: read section JSON via `load_section`; construct the class; call `install_or_update`; handle exceptions per soft/hard-fail contract.
- chef.py changes:
  - Read recipe.toml.
  - For each top-level section, extract `depends_on` and `needs_root`.
  - Build a `graphlib.TopologicalSorter`; `get_ready()` / `done()` walk.
  - For each ready section, spawn the cook subprocess directly or via `sudo` depending on `needs_root`.
  - `STANDALONE_PLAYBOOKS` constant removed — the configure_* scripts are declared in recipe.toml instead.
- `BashCook` (new): generic shell executor. For each entry, runs `pre_update`, `install_or_update`, `post_update` via `stream_subprocess(["bash", "-c", one_liner])`. `show_version` runs each entry's `show_version` one-liner and parses stdout into a `VersionInfo`.
- `UrlCook` (renamed today's `bash_cook`): essentially the existing code, refactored to a class.
- Concurrency: `main()` keeps existing ThreadPoolExecutor wrappers (bash → url, uv) for Phase 1. Move to chef in Phase 2.

Phase 2: chef imports cook classes directly. chef owns concurrency. `--dry-run` flag wired to `show_version` aggregation across all sections.

Phase 3: chef extracts the idempotency loop where it factors cleanly. Batch-tool cooks (cargo, apt_pkg) keep doing their own idempotency atomically.

Phase 4: per-entry `depends_on` granularity (e.g. `[bash.apt_prereqs].depends_on = ["apt_repo.brave-browser"]`).

Phase 5: `configure_gpu` / `configure_apps` internal refactor to classes if it still feels worthwhile.

## 10. Tech stack

- [DECIDED] Python ≥3.14 + stdlib + `loguru` + `toon-format`. No new deps.
- [DECIDED] No test framework added.
- [DECIDED] `VersionInfo` and `Result` as `@dataclass(frozen=True)` (stdlib).
- [DECIDED] Topo sort: stdlib `graphlib.TopologicalSorter`.

## 11. Roadmap

- **Phase 1 (this plan):** Classes + `needs_root` + `depends_on` + chef-owned sudo + bash/apt splits + apt weird-stuff migration to `[bash.*]` + configure_* declared in recipe.toml.
- **Phase 2:** chef imports cook classes (replaces subprocess spawn for non-root cooks). chef owns concurrency. `--dry-run` wired to `show_version`.
- **Phase 3:** chef extracts the idempotency loop where it factors cleanly. Per-entry `depends_on` becomes meaningful.
- **Phase 4:** Whatever weird stuff didn't fit into `[bash.*]` in Phase 1 (likely template-heavy file writes; see §13 q2) gets re-evaluated — either a new minimal cook type or stays in Python.
- **Phase 5:** `configure_gpu` / `configure_apps` internal refactor to the class shape (if still desired).

## 12. Decisions log

- **Phase 1 keeps the subprocess boundary** between chef.py and cooks; chef spawns each cook script. Imports come in Phase 2.
- **Class methods are synchronous; concurrency is the caller's job.**
- **`show_version` returns a list of records, never a printed table.**
- **`uv_cook` is the walking skeleton.**
- **Class-per-section, not class-per-item.**
- **Sections must be uniform OR single-tool batch wrappers.**
- **`install_or_update` does not change behavior in Phase 1** — pure structural refactor.
- **`needs_root` is a recipe flag, binary, chef does the sudo, cooks lose `reexec_under_sudo`.**
- **bash/apt splits land in Phase 1.**
- **`VersionInfo` fields include `cook` and `manager`** for downstream aggregation.
- **`depends_on` lands in Phase 1.** Section-level. `graphlib.TopologicalSorter`. Defaults to `[]`; file order is tiebreaker only.
- **`bash_cook` (new) is a generic shell executor.** Schema: `install_or_update`, `show_version`, `pre_update`, `post_update` — all bash one-liners. Idempotency is the bash author's responsibility.
- **`url_cook` (renamed) is today's `bash_cook`** — URL-driven `curl | bash` installers, retains `pre_update` mechanic.
- **Apt's "weird stuff" migrates to `[bash.<name>]` entries** wherever bash one-liners suffice (debconf, prereqs, hardening assertions). Template-heavy file writes need a referencing mechanism (see §13 q2).
- **`configure_gpu` / `configure_apps` declared in recipe.toml** with `needs_root = true` and `depends_on = ["apt_pkg"]`. `STANDALONE_PLAYBOOKS` removed. Internal refactor deferred to Phase 5.
- **Rule of thumb:** if an operation fits as bash one-liners, it lives in `[bash.<name>]`. Otherwise it gets a dedicated `*_cook.py`.
- **`[bash.<name>]` schema field is `check_installed`, not `show_version`.** The class *method* is `show_version()`; it runs `check_installed` and wraps stdout into a `VersionInfo`. Phase 1 branches on neither — loose contract, "reasonable and simple" output. (Round 5 Interaction 1.)
- **Template files inline into recipe.toml** via heredoc; comments collapse to one line. No env var, no pre-render, no `apt_setup_cook`. (Round 5 Interaction 2 — variant of option 1.)
- **`install_or_update` returns `Result(status, message, changed)`; expected failures don't raise.** (Round 5 Interaction 3 option 2.)
- **A `write-if-changed` shell helper on `$PATH`** wraps `harness.write_if_changed` so inlined heredoc writes in `[bash.*]` snippets stay idempotent (quiet re-runs preserved) without per-entry boilerplate. Single source of truth for the idempotency primitive. (Round 6 Interaction 1 option 2.)

## 13. Open questions

*All blocking questions resolved. Remaining items are deliberately deferred to implementation time:*

1. **Where the base class lives** when extracted: `harness.py` or `cook_base.py`. Decide after the second cook is converted — premature now (the walking skeleton, `uv_cook`, has no base class).
2. **Exact packaging of the `write-if-changed` helper** (standalone shebang script in `src/`, console entry point, etc.) — impl detail; the contract (wraps `harness.write_if_changed`, reads stdin, path as arg) is fixed.

*(Resolved across rounds: subprocess boundary kept for Phase 1 with import-ready API (R2). Class-per-section + uniformity rule (R3). Behavior preserved (R3). `needs_root` flag, chef-owned sudo (R3). bash/apt splits in Phase 1 (R3). `VersionInfo` shape (R3). `depends_on` via `graphlib`, section-level, default `[]` (R4). configure_* into recipe.toml, `STANDALONE_PLAYBOOKS` removed (R4). `[bash.*]` generic shell executor with `check_installed` (R4/R5). Template files inline via heredoc (R5). `Result(status, message, changed)` return (R5). `write-if-changed` shell helper wrapping `harness.write_if_changed` (R6).)*

## 14. Known unknowns

- How much code actually generalizes once two cooks are converted. The "real" base class only emerges after the second case.
- Whether `apt_pkg`'s show_version cost (~50ms × N packages via `apt-cache policy`) is tolerable as a routine probe or wants caching by Phase 2.
- Whether topo-sort will surface a hidden cycle once `depends_on` is declared everywhere.
- Whether expressing the apt hardening/pinning rationale as bash entries obscures intent. Today's `apt_cook.py` carries ~4 paragraphs explaining cross-repo safety, the immutable-bit hardening, and the DPkg pre/post hook (release upgrade aborted once when `ubuntu-keyring` couldn't write into the locked dir). That rationale must survive the migration — likely as `#` comment lines above each `[bash.<name>]` entry in recipe.toml. Losing it would be a regression in institutional memory even if the behavior is identical.
