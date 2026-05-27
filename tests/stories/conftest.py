"""Fixtures for the prose-style tests, split by arrange/act/assert role across `arrange_fixtures`, `act_fixtures`, and `assert_fixtures`. A test names the fixtures it needs and reads top-to-bottom like a user story; the only things mocked are the system boundaries — bash (`totchef.shell`), network (`harness.urlopen`), the home directory, and the host (discoverable binaries + OS release).

- `terminal` (autouse) — the bash double; patched over `totchef.shell.run`/`stream`, so no test can shell out for real. Arrange replies with `arrange`; verify with `expect_ran`/`expect_not_ran`.
- `http` (autouse) — the network double; patched over `harness.urlopen`, so no test can reach the real network (an un-programmed URL raises).
- `home` (autouse) — `$HOME` redirected to a temp dir, so per-user cooks write under it. Returns the Path.
- `system` (autouse) — the host double: PATH isolated to an empty bin dir and `freedesktop_os_release` pinned. `has(...)` provisions a binary, `running_release(...)` sets the codename.
- `recipe` — the operator's recipe.toml, built with `declares(...)`.
- `totchef` — the user action (`plan`/`up`/`lint`), run against `recipe`.
- `scenario` — a factory for an independent run with its own fresh recipe, for a test that compares several distinct recipes.
- `cli` — invoke a real `totchef <command>` (`where`/`lint`/`--version`/`--list-cooks`) and capture its output and exit code.
- `apply_in_container` — run a real `totchef up` inside a throwaway container as a non-root user and read back the ownership of what it produced; for the few stories whose criterion is the real privilege drop (§6.3.2, §7.3.1). Skips when podman is absent. See `container_fixtures`.

A `fresh_registry` autouse fixture clears the cached cook registry around every test, so a local-cook drop-in (or HOME redirection) never leaks between tests.
"""

from act_fixtures import cli, scenario, totchef
from arrange_fixtures import fresh_registry, home, http, recipe, system, terminal
from assert_fixtures import read_json
from container_fixtures import apply_in_container, container_image

__all__ = ["apply_in_container", "cli", "container_image", "fresh_registry", "home", "http", "read_json", "recipe", "scenario", "system", "terminal", "totchef"]
