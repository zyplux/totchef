"""StateCook for [desktop] — per-user .desktop Exec= overrides.

For each app in apps_config.toml carrying a `desktop` key, copies the system
.desktop, rewrites each Exec= line with an `env` prefix + `--<switch>`es +
`--enable-features=`, and writes the override under
~/.local/share/applications/. Diffable: desired = hash of the rendered override,
current = hash of what's on disk, so chef skips unchanged apps and fires the
KDE-cache refresh `post_hook` only when a .desktop actually changed.

Runs as the invoking user (needs_root = false -> chef forks + drops privilege),
so it writes into $HOME directly — no chown. depends_on the packages it tunes.
"""

import hashlib
from pathlib import Path

from apps_config import apps_with, load
from cook_base import ItemOutcome, StateCook, debug_main
from harness import logger, write_if_changed

# Refresh KDE's ksycoca so the launcher stops spawning apps with the stale Exec
# line; tolerant of non-KDE systems where kbuildsycoca6 is absent.
KSYCOCA_REFRESH = (
    "command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 --noincremental || true"
)


def rewrite_exec_line(
    exec_value: str,
    env: dict[str, str],
    features: list[str],
    switches: list[str],
) -> str:
    """Idempotent rewrite of a .desktop Exec= value with env prefix, --<switch>s, and
    --enable-features. New args insert before trailing field codes (%U, %u, %F, %f)."""
    tokens = exec_value.split()

    if tokens and tokens[0] == "env":
        i = 1
        while i < len(tokens) and "=" in tokens[i] and not tokens[i].startswith("-"):
            i += 1
        tokens = tokens[i:]

    # Switches may be bare ("enable-foo") or key=value ("render-node-override=/x"); dedupe
    # by key so a value change in apps_config.toml replaces the old token instead of duplicating.
    managed_keys = {f"--{s.split('=', 1)[0]}" for s in switches}
    tokens = [
        t
        for t in tokens
        if not t.startswith("--enable-features=")
        and not any(t == k or t.startswith(k + "=") for k in managed_keys)
    ]

    insert_at = next(
        (i for i, t in enumerate(tokens) if len(t) == 2 and t.startswith("%")),
        len(tokens),
    )
    for sw in switches:
        tokens.insert(insert_at, f"--{sw}")
        insert_at += 1
    if features:
        tokens.insert(insert_at, f"--enable-features={','.join(features)}")

    if env:
        tokens = ["env", *(f"{k}={v}" for k, v in env.items()), *tokens]

    return " ".join(tokens)


class DesktopCook(StateCook):
    manager = "desktop"
    user_only_reason = "it writes a per-user .desktop override under ~/.local/share"

    def __init__(self, section: dict) -> None:
        super().__init__(section)
        config = load()
        self.shared_env: dict[str, str] = config.get("env", {})
        self.chromium_features: list[str] = config.get("chromium", {}).get(
            "features", []
        )
        self.apps = apps_with(config, "desktop")

    def items(self) -> list[str]:
        return list(self.apps)

    def _target(self, name: str) -> Path:
        system_desktop = Path(self.apps[name]["desktop"])
        return Path.home() / ".local/share/applications" / system_desktop.name

    def _render(self, name: str) -> bytes | None:
        app = self.apps[name]
        system_desktop = Path(app["desktop"])
        if not system_desktop.exists():
            return None
        env = {**self.shared_env, **app.get("env", {})}
        features = self.chromium_features + app.get("features", [])
        switches = app.get("switches", [])
        lines = []
        for line in system_desktop.read_text().splitlines():
            if line.startswith("Exec="):
                lines.append(
                    "Exec=" + rewrite_exec_line(line[5:], env, features, switches)
                )
            else:
                lines.append(line)
        return ("\n".join(lines) + "\n").encode()

    def current(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for name in self.apps:
            target = self._target(name)
            out[name] = (
                hashlib.sha256(target.read_bytes()).hexdigest()
                if target.exists()
                else "absent"
            )
        return out

    def desired(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for name in self.apps:
            content = self._render(name)
            out[name] = (
                hashlib.sha256(content).hexdigest() if content else "(no source)"
            )
        return out

    def hooks(self, name: str) -> tuple[str | None, str | None]:
        return (None, KSYCOCA_REFRESH)

    def apply_one(self, name: str) -> ItemOutcome:
        content = self._render(name)
        if content is None:
            return ItemOutcome(
                changed=False,
                message=f"{self.apps[name]['desktop']} not found; install the package first.",
            )
        changed = write_if_changed(self._target(name), content, note=name)
        if changed:
            logger.info("Restart the app to apply the new Exec= line.")
        return ItemOutcome(changed=changed)


if __name__ == "__main__":
    debug_main(DesktopCook)
