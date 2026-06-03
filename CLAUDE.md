# CLAUDE.md

`totchef` — an idempotent, declarative system configurator: you describe the machine in `recipe.toml`, and `just up` makes it comply.

## Invariants

- **Installing means codifying.** Any "install package" / "add configuration" request means one thing: add it to `recipe.toml`. The user applies it with `just up`. Never modify the system directly (`apt`, `curl | sh`, …), and never offer to.
- **Wayland-only.** Never install or suggest anything Xorg / X11 / X-display-manager related. It's a relic here — don't reach for it.
