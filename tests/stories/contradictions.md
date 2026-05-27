# user-stories.md — accuracy review

A review of how faithfully [user-stories.md](user-stories.md) describes the actual
user-facing behaviour implemented in `src/totchef/`. Cross-checked criterion by
criterion against the production code and the story tests.

## What the meta-test does and doesn't guarantee

[tests/project/test_user_stories.py](../project/test_user_stories.py) enforces only
*structural lockstep*: every `####` header has a same-id test function, every test
has a header, the header title equals the slugified test name
([story_links.py](../project/story_links.py) `split_id_and_title`), and each `###`
links to its section's test file. It never compares the *prose* under a header to
the code. A paragraph can drift arbitrarily from `src/` and all four meta-tests stay
green, as long as the header id and slug still match a test name. Accuracy rests on
the story tests being faithful plus human review.

## Verdict

The doc is unusually faithful. All 96 criteria were checked against the
implementation and the large majority describe real, correctly-stated behaviour; the
cook-by-cook sections (§3–§5) are especially tight. The items below are the genuine
drifts, gaps, and redundancies, ordered by impact.

## 1. Prose contradicts the code (accuracy bugs)

### 1a. §1.1.1 states the wrong order — escalation precedes validation

user-stories.md §1.1.1 (header and prose) says "reads the
recipe, validates it, escalates to root, previews…". The actual sequence in
[cli.py](../../src/totchef/cli.py#L102-L118) is:

```text
find_recipe (existence only) -> ensure_root (escalate) -> load_recipe -> validate -> preview -> execute
```

`ensure_root` is the first line of `apply()`, before the recipe is parsed or
schema-validated. This is not cosmetic: an invalid recipe still triggers the `sudo`
prompt *before* the validation error surfaces — the opposite of what "validates it,
escalates" implies. The test ([test_1_running_totchef.py](test_1_running_totchef.py#L16-L29))
asserts only the end state (`applied` rows, file written); it never checks ordering
or escalation, so the prose drifted freely.

Honest phrasing: "resolves the recipe, escalates to root, then validates and previews
before executing."

### 1b. §1.2.1's explicit action list omits `would sync`

user-stories.md §1.2.1 enumerates `would install`,
`would upgrade`, `would apply`, `up-to-date`, `ok`. The code also emits **`would
sync`** ([cook_runner.py](../../src/totchef/cook_runner.py#L94)) whenever a versioned
cook can't determine the latest version. Users see it: every present `url.<tool>`
shows `would sync` in a plan (see 1c), as does any crate/uv tool when the
crates.io/PyPI lookup fails.

§7.1.1's *colour-group* description is fine — `missing`/`post-failed`/`would sync`
all fall under its "red for failures / yellow for would…" groupings. The gap is
specifically §1.2.1's literal enumeration.

### 1c. The headline idempotency guarantee has an unflagged `url`-cook exception

§1.1.2 ("re-running when nothing has drifted… makes no
modifications") and §6.1.1 ("Versioned cooks skip
up-to-date packages") are stated as universal. They do not hold for the `url` cook,
which diffs *presence only* — `find_latest` always returns `None`
([url_cook.py](../../src/totchef/cooks/url_cook.py#L81-L82)). Consequences in
[cook_runner.py](../../src/totchef/cook_runner.py#L110-L111):

- Every *present* `url` tool lands in `to_upgrade` on every run, so `sync()` runs its
  `update_action` each time — re-piping the installer for `"rerun-installer"`, or
  re-running `bin self update` for an arg list. That is real work on a no-drift run.
- `plan` shows these as `would sync` (changed, yellow), but `up` reports them as
  `unchanged` (folded into the footer count) — so §1.2.4's "the same plan is printed
  first… sees what is about to happen" mildly misleads for `url` entries.

§3.5.1 is honest about this ("Presence (not version) is what's diffed… if present
it's updated"), so the per-cook story is correct; the global claims overreach without
carving out the exception. This is the one place the doc contradicts itself.

## 2. Missing user-facing behaviour

### 2a. The scheduling/concurrency model has no story of its own

The biggest visible behaviour not captured: user nodes run with unbounded
concurrency while root nodes are serialized one-at-a-time, ties broken by "reach"
([cook_runner.py](../../src/totchef/cook_runner.py#L372-L401)). §7.2.x *assumes*
concurrency ("interleaved output of concurrently-running cooks") and §2.2.2 covers
topological order, but a user reasoning about why a run is slow, or why two root cooks
never overlap, has no story to point to. CLAUDE.md treats this as a headline design
property; the stories do not.

### 2b. `would sync` and the plan-vs-up reporting difference

Same root cause as 1b/1c — worth a dedicated criterion under §3.5 or §7.1.

Correctly absent (not worth stories): `XDG_CONFIG_HOME`/`XDG_STATE_HOME` overrides,
`no_args_is_help`, the apt `lsattr` / `nala list --upgradable` diagnostics.

## 3. Redundant or internals-leaning content

### 3a. Implementation mechanism leaking past observable behaviour

These describe *how* rather than *what the user sees*:

- §6.3.2 "(gid → groups → uid)" — the syscall order is
  internals ([harness.py](../../src/totchef/harness.py#L21-L41)); the user-facing fact
  is "files land owned by you, and freshly bootstrapped toolchains are on PATH."
- §7.3.2 "funnels through a single pump" — "pump" is an
  internal noun ([logs.py](../../src/totchef/logs.py#L94-L103)); the observable
  guarantee is "log lines never interleave with the live table."
- §6.3.1 "pinning the recipe path and shared log file across the boundary" and parts
  of §7.2.3 (the reach/queueing/unlocked counts) lean the same way.

Not wrong, but they read as design notes; phrasing each by its observable outcome
keeps §6/§7 in the user's frame.

### 3b. Cross-section repetition

- Idempotency is asserted in §1.1.2, the §3 intro, §4.2.3, §6.1.1, and §6.1.2 —
  and §6.1.2 ("post_hooks fire only on actual change") is a near-duplicate of §4.2.3
  ("post_hook runs only when the file changed").
- "Never escalates" appears in §1.2.2, §1.3.3, and again in §6.3.3.
- The dependency cycle / self-dep / missing-node lint is in both §1.3.2 and §2.2.3.

Some repetition is defensible (stories are organized by user goal, one mechanism
serves several goals), but these specific pairs are restatements, not new lenses.

## 4. Structural observations

- **H4 titles read like test ids.** Because the meta-test ties each `####` title
  verbatim to a test-function name, headers read as identifiers ("up reads validates
  escalates previews then executes") rather than documentation headings. That is the
  price of the lockstep — a conscious trade-off, not a bug.
- **The privilege/IO boundary is split across test layers (one currently absent).**
  The in-process story harness ([conftest.py](conftest.py)) is black-box and mocks
  every system boundary (`sudo` bypassed; `shell`/network/`$HOME`/OS-release faked), so
  it cannot observe the real escalate-to-root-then-drop. By design — see the
  [test_story_imports.py](../project/test_story_imports.py) docstring — the OS-state
  half (§6.3.2 file ownership by scope, §7.3.1 log chown-back) is delegated to a
  container suite under `tests/container/`, while the §7.2/§7.3.2 rendering/pump/timing
  criteria are tested white-box in
  [test_7_observing_a_run.py](test_7_observing_a_run.py), pinned as the single allowed
  white-box exception so the set cannot quietly grow. **Caveat:** `tests/container/` is
  currently deleted from the working tree (staged `AD`), so on disk that OS-state layer
  is presently missing — if that deletion is unintentional, §6.3.2/§7.3.1 lose their
  end-to-end coverage and fall back to prose plus review.

## Suggested fixes

1. Fix §1.1.1 ordering to "resolves → escalates → validates → previews → executes"
   (and the header).
2. Add `would sync` to §1.2.1; add a §3.5 line that presence-diffed `url` tools
   re-run their `update_action` every run and show `would sync` in plan /
   `unchanged` in up.
3. Add a one-criterion §2 or §7 story for the concurrency / root-serialization
   scheduler.
4. Re-voice §6.3.1 / §6.3.2 / §7.3.2 / §7.2.3 to lead with the observable outcome;
   drop the syscall / "pump" internals.
5. Collapse the §6.1.2 ≈ §4.2.3 and §6.3.3 ≈ (§1.2.2 + §1.3.3) duplications.
