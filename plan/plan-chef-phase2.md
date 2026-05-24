# Chef Phase 2 — Plan

> **Status:** ready — readiness 96%
> **Last updated:** 2026-05-25
> **Walking skeleton:** none — all 7 bullets ship in one sweep, verified by one re-run

## 1. Vision

Move all idempotency / diff logic out of the cooks and into chef, so each cook
becomes a thin "manager" that only knows how to list what's installed, fetch the
latest version, install, and upgrade. Chef becomes a typer CLI that walks the
dependency graph in parallel, decides per-item what action to take, runs the
work (dropping privilege for user-scope cooks), and prints a final report of
current vs latest version and what changed. `just up` behaviour must not change.

## 2. Problem & motivation

Every cook re-implements its own idempotency — `uv_cook` parses `uv tool list`,
`snap_cook` parses `snap list`, `bash_cook` does ad-hoc check-and-act — so the
same "present? install vs upgrade?" decision is scattered and inconsistent.
There is no end-of-run report, no view-only mode, and chef runs strictly
sequentially even where sections are independent. The scaffolding for this
consolidation already exists and is unused: `Result.changed`, the whole
`VersionInfo` dataclass, and the deliberate subprocess boundary chef keeps "for
a later phase where chef imports cooks directly." This plan is that phase.

## 3. Users & primary scenarios

- **Primary user:** the repo owner, running `just up` on a Kubuntu Wayland laptop.
- **Key scenarios:**
  - Run the full config and get a final table of every managed item: installed
    version, latest version, and action taken (installed / upgraded / unchanged).
  - Run a view-only command to see what's behind (drift) without changing anything.

## 4. Goals

- All diff / idempotency decisions are made in chef, not in cooks.
- Cooks expose a minimal contract (probe + act); no idempotency logic of their own.
- Chef runs independent sections concurrently, honouring `depends_on`.
- End-of-run report table; a view-only mode that only probes.
- Chef is a typer CLI.
- `just up` behaviour/output is unchanged (verified by `Unchanged:` lines on re-run).

## 5. Non-goals (current scope)

- No plan-then-apply / Terraform-style workflow (this is self-contained).
- No drift detection or scheduled runs.
- No new package managers; classic snaps stay out of scope.

## 6. Constraints

- Python ≥3.14; stdlib + `loguru` + `toon-format` today. typer would be a new dep.
- **Mixed privilege:** apt / snap / bash need root; uv / cargo / url must write
  into the invoking user's `$HOME`.
- Idempotent re-runs must stay cheap (the `write_if_changed` invariant).
- No test suite — verification is re-running `just up` and reading `Unchanged:`.
- Global rules apply: no lint suppression, config-as-code, self-documenting code.

## 7. Functional requirements

- [DECIDED] Chef runs as root; user-scope cooks have privilege dropped to
  `SUDO_USER` before they run (like `sudo apt`). _(round-1 #3)_
- [DECIDED] Two resource kinds: **versioned-package** and **desired-state**;
  each cook declares which it is. _(round-1 #2)_
- [DECIDED] Chef owns the diff: it compares installed vs available (packages)
  or current vs desired (resources) and decides install / upgrade / skip.
- [DECIDED] User-scope cooks run in a forked child via one shared `become_user()`
  chokepoint; results return to the root parent over a pipe. _(round-2 #1)_
- [DECIDED] Minimal cook contract — **versioned-package** cook:
  `list_installed()`, `latest_available(names)`, `install(names)`,
  `upgrade(names)`; **desired-state** cook: `desired()`, `current()`,
  `apply(diff)`. Cooks hold no decision logic. _(round-2 #2)_
- [DECIDED] Chef dispatches ready graph nodes concurrently within `depends_on`;
  **user cooks fork** (for `become_user`), **root cooks run in-process** —
  apt/snap serialize on the dpkg/snapd global lock anyway, so the parallelism
  win is among the independent user cooks. _(round-5 #3)_
- [DECIDED] The report's "latest" column is **best-effort per manager**: filled
  where cheap (`apt-cache policy` candidate, cargo/uv/url native), `—` where not
  (e.g. snap without an extra network call). _(round-5 #2)_
- [DECIDED] End-of-run report is **compact, changes-first**: columns name,
  manager, installed → latest, action; a normal run shows only changed rows plus
  an "N unchanged" footer, while `--dry-run` shows every managed item. _(round-4 #3)_
- [DECIDED] Chef is a typer CLI, single command; `--dry-run` probes only and
  prints the report table without acting. _(round-3 #3)_
- [DECIDED] `file_cook` is a desired-state cook (`[file.<name>]`: path, content,
  mode) and supports an optional post hook. _(round-3 #2)_
- [DECIDED] **Uniform lifecycle hooks across all cooks**, owned by chef: `pre`
  and `post` run immediately before/after the action chef decides on, and fire
  **only when an action is taken** (an all-unchanged run fires no hooks).
  _(round-4 #1, #2)_
- [DECIDED] Convert the playbooks with **different treatments**:
  `configure_gpu` is fully unconditional at install time, so it **decomposes**
  into static `[file.<name>]` entries + `post` hooks (`daemon-reload`, `enable`,
  `update-grub`, `update-initramfs`); fold its raw `GRUB_FILE.write_text` into
  `write_if_changed` while doing so. `configure_apps` has genuine conditional
  logic (skip `Local State` if the browser is running, JSON deep-merges) so it
  **wraps as-is** as one desired-state cook. The boot-time `egpu-prime-switch`
  (which owns the per-boot eGPU conditionality) is unaffected — it stays a
  standalone boot script that this cook merely installs.
- [DECIDED] `just up` behaviour is unchanged.

## 8. Walking skeleton (v1 / MVP)

[DECIDED] No staged skeleton — deliver all 7 bullets in one sweep, then run it
(round-2 #3, round-3 #1). Verification is a single `just up` re-run; the owner
accepts the no-test-net risk (worst case: reinstall Kubuntu).

## 9. Architecture sketch

Chef-as-root is the orchestrator. It imports cooks as objects, so the diff /
idempotency logic lives in one chef-side engine that every cook shares. For each
ready section in the graph:

- **user-scope cook** → run in a forked child that passes through one shared
  `become_user()` chokepoint (set gid, set uid, fix `$HOME` / `$XDG_*` / `PATH`),
  then runs the cook; the root parent stays root.
- **root-scope cook** → run in-process (or also forked, for isolation/parallelism).

Children return structured `Result` / `VersionInfo` to the parent, which merges
them into the report. The graph is walked with `TopologicalSorter`, but ready
nodes are dispatched to a concurrent pool rather than run one at a time.

Two cook shapes share `CookBase`: a versioned-package cook and a desired-state
cook. Chef's diff engine takes `(installed, available)` or `(current, desired)`
and yields a per-item action.

## 10. Tech stack

- **typer** — CLI framework [DECIDED] (new dep; single command + `--dry-run`,
  vs argparse: nicer flag handling at the cost of one dependency).
- **graphlib.TopologicalSorter** — ordering (already used).
- **concurrent.futures** — parallel dispatch of ready nodes [PROPOSED].
- **loguru + toon-format** — logging and the report table (already used).

## 11. Roadmap

True big-bang (round-2 #3, round-3 #1): all 7 bullets in one sweep, verified by
one `just up` re-run at the end. Suggested internal build order so the pieces
land coherently: diff engine + `become_user` + lifecycle-hook runner first, then
convert each cook to the contract, then typer CLI + report + `--dry-run`, then
`file_cook` and the `configure_gpu` / `configure_apps` conversions.

## 12. Decisions log

- Chose **chef-as-root + privilege-drop** (#3) over subprocess-protocol (#1) and
  two-pass re-exec (#2): mirrors `sudo apt`, simplest control flow. The footgun
  (toolchains landing under `/root`) is contained by a single privilege-drop
  chokepoint, and one process can't drop-then-reclaim root, so user cooks run in
  forked, privilege-dropped children.
- Chose **two resource kinds** over a generic reconcile model: keeps the report
  honest (version→version for packages, changed/unchanged for files).
- **Self-contained scope:** no plan/apply, no drift detection.
- Chose **fork-per-user-cook + one `become_user()` chokepoint** over a persistent
  user worker or `sudo -u` re-invocation: single place to get the drop right
  (closes the `/root` footgun), clean isolation for parallel dispatch.
- Chose a **probe/act contract** (`list_installed`/`latest_available`/`install`/
  `upgrade`; `desired`/`current`/`apply`) over a cook-side `reconcile()`: keeps
  all decision logic in chef and preserves batch verbs (`uv tool install a b c`).
- Chose **true big-bang** over staged/incremental-verify (round-3 #1): owner
  accepts no-test-net risk for speed; worst case is a reinstall.
- Chose **`file_cook` + uniform lifecycle hooks**: rather than a one-off post
  hook on `file_cook`, every cook gains a consistently-named hook lifecycle
  (round-3 #2). Generalizes today's ad-hoc `pre_update` on `url`/`bash`.
- Chose a **single `chef` command + `--dry-run`** over subcommands (round-3 #3):
  most widely understood term for "show the diff without acting."
- Chose **`pre`/`post` hooks, fire-on-action-only** over always-fire or
  per-action names (round-4 #1, #2): preserves the quiet-re-run invariant;
  upgrade-only needs (e.g. `herdr` server stop) live as idempotent `pre` guards.
- Chose a **compact, changes-first report** over full-every-time (round-4 #3):
  mirrors the existing `Unchanged:`/`Writing:` philosophy; `--dry-run` is the
  full-inventory view.
- Chose **best-effort "latest" column** over always-fetch or drop-it (round-5 #2):
  fast and honest (`—` beats a fabricated version for snap).
- Chose **root cooks in-process, user cooks forked** (round-5 #3): real
  parallelism is among user cooks; root cooks can't run concurrently anyway
  (dpkg/snapd global lock), so forking them is plumbing for ~no speedup.
- Corrected a misread (round 5): `configure_gpu` the cook is **unconditional** —
  the per-boot eGPU conditionality lives in `egpu-prime-switch`, a boot-time
  script (a static install-time file was the original login-loop staleness trap).
  So `configure_gpu` decomposes into static files + post hooks; `configure_apps`
  wraps as-is for its conditional JSON merges.

## 13. Open questions

_All major questions resolved. Remaining items are implementation-time details:_

1. Exact `[file.<name>]` decomposition of `configure_gpu` (which files, which
   post hooks) — mechanical, settle while building.
2. The `--dry-run` flag's exact spelling and whether `just up` keeps its name or
   maps to a `chef` subcommand alias — cosmetic.

## 14. Known unknowns

1. Snap's "latest available version" without a network round-trip — resolved
   pragmatically by the best-effort column (`—` when unknown), but if a cheap
   source turns up it can be filled later.
