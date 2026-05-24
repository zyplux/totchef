"""StateCook for [settings] — merge an env block into a JSON settings file.

For each app in apps_config.toml carrying a `settings_json` key, merges its
`settings_env` table into the top-level `env` key of that JSON file (e.g.
~/.claude/settings.json), preserving every other key the user has set. The TOML
declares the desired state, so same-named env entries are overridden.

Diffable: desired = hash of the merged JSON, current = hash of what's on disk.
Runs as the invoking user (needs_root = false -> chef forks + drops privilege);
writes into $HOME, no chown.
"""

import hashlib
import json
from pathlib import Path

from apps_config import apps_with, load
from cook_base import ItemOutcome, StateCook, debug_main
from harness import write_if_changed


class SettingsCook(StateCook):
    manager = "settings"
    user_only_reason = "it writes a JSON settings file under $HOME"

    def __init__(self, section: dict) -> None:
        super().__init__(section)
        self.apps = apps_with(load(), "settings_json")

    def items(self) -> list[str]:
        return list(self.apps)

    def _target(self, name: str) -> Path:
        return Path.home() / self.apps[name]["settings_json"]

    def _render(self, name: str) -> bytes:
        target = self._target(name)
        env_overrides = self.apps[name].get("settings_env", {})
        existing: dict = json.loads(target.read_text()) if target.exists() else {}
        merged = {**existing, "env": {**existing.get("env", {}), **env_overrides}}
        return (json.dumps(merged, indent=2) + "\n").encode()

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
        return {
            name: hashlib.sha256(self._render(name)).hexdigest() for name in self.apps
        }

    def apply_one(self, name: str) -> ItemOutcome:
        changed = write_if_changed(self._target(name), self._render(name), note=name)
        return ItemOutcome(changed=changed)


if __name__ == "__main__":
    debug_main(SettingsCook)
